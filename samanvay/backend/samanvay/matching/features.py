"""Candidate generation and match feature engineering.

Matching two cadastral layers is a record-linkage problem where the "record" is a polygon.
Comparing every polygon to every other is O(n²) and impossible at 250,000 features per
layer, so the work splits into two stages, and almost all the engineering value is in
getting the split right:

**Blocking / candidate generation** narrows 250,000 × 250,000 to a few hundred thousand
plausible pairs using an R-tree over buffered envelopes. The buffer is set from the
declared positional accuracies of the two layers rather than a magic number, because that
is the distance a true match can actually be displaced by.

**Feature extraction** describes each candidate pair with a vector that a classifier can
learn on. The features are chosen so that each captures a different *kind* of sameness,
and so that none of them can be trivially spoofed by the others:

* *Overlap* — IoU, containment both ways. The primary signal, and the one that fails
  exactly when the layers are systematically offset.
* *Position* — centroid distance normalised by size; offset direction and magnitude.
* *Shape* — area ratio, perimeter ratio, compactness difference, turning-function
  distance, Hu moment distance. These survive translation entirely, which is what makes
  them able to match a shifted layer that IoU has given up on.
* *Boundary* — Hausdorff and mean boundary distance, vertex-count ratio.
* *Semantic* — attribute agreement after normalisation: survey number, ward, locality,
  owner name similarity.
* *Context* — agreement of the local neighbourhood, which catches the case where two
  buildings in a row are individually ambiguous but collectively unambiguous.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from ..core.models import MatchPair


# --------------------------------------------------------------------------------------
# feature entities
# --------------------------------------------------------------------------------------


@dataclass
class MatchableFeature:
    """A feature prepared for matching: geometry in a metric CRS plus cached descriptors."""

    fid: str
    dataset_id: str
    geometry: BaseGeometry
    attributes: dict[str, Any] = field(default_factory=dict)
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def centroid(self):
        if "c" not in self._cache:
            self._cache["c"] = self.geometry.centroid
        return self._cache["c"]

    @property
    def area(self) -> float:
        if "a" not in self._cache:
            self._cache["a"] = float(self.geometry.area)
        return self._cache["a"]

    @property
    def perimeter(self) -> float:
        if "p" not in self._cache:
            self._cache["p"] = float(self.geometry.length)
        return self._cache["p"]

    @property
    def compactness(self) -> float:
        if "k" not in self._cache:
            p = self.perimeter
            self._cache["k"] = float(4 * math.pi * self.area / (p * p)) if p > 0 else 0.0
        return self._cache["k"]

    @property
    def radius(self) -> float:
        """Equivalent-circle radius: the natural length scale of the feature."""
        return math.sqrt(max(self.area, 1e-9) / math.pi)

    @property
    def turning(self) -> np.ndarray:
        if "t" not in self._cache:
            self._cache["t"] = _turning_function(self.geometry)
        return self._cache["t"]

    @property
    def hu(self) -> np.ndarray:
        if "h" not in self._cache:
            self._cache["h"] = _hu_moments(self.geometry)
        return self._cache["h"]


# --------------------------------------------------------------------------------------
# candidate generation
# --------------------------------------------------------------------------------------


@dataclass
class BlockingConfig:
    search_radius_m: float | None = None
    """If None, derived from the two layers' declared accuracies."""
    accuracy_multiplier: float = 3.0
    """A true match should lie within k-sigma of the combined positional uncertainty."""
    max_candidates_per_feature: int = 12
    min_area_ratio: float = 0.08
    """Reject pairs whose areas differ by more than this factor — a 4 m² shed is not the
    same object as a 400 m² block, however much they overlap."""
    max_area_ratio: float = 12.0
    require_intersection: bool = False
    attribute_block_keys: tuple[str, ...] = ()
    """Optional hard blocking keys (e.g. ward). Cheap and very effective when reliable."""


class CandidateGenerator:
    """Produces plausible pairs cheaply, so the expensive scorer runs on few of them."""

    def __init__(self, config: BlockingConfig | None = None) -> None:
        self.config = config or BlockingConfig()
        self.stats: dict[str, Any] = {}

    def radius_for(self, acc_left_m: float, acc_right_m: float) -> float:
        if self.config.search_radius_m is not None:
            return self.config.search_radius_m
        combined = math.hypot(acc_left_m or 1.0, acc_right_m or 1.0)
        return max(2.0, self.config.accuracy_multiplier * combined)

    def generate(self, left: Sequence[MatchableFeature], right: Sequence[MatchableFeature],
                 *, acc_left_m: float = 1.0, acc_right_m: float = 1.0
                 ) -> list[tuple[MatchableFeature, MatchableFeature]]:
        cfg = self.config
        radius = self.radius_for(acc_left_m, acc_right_m)
        if not left or not right:
            return []

        rgeoms = [f.geometry for f in right]
        tree = STRtree(rgeoms)
        pairs: list[tuple[MatchableFeature, MatchableFeature]] = []
        truncated = 0

        for lf in left:
            probe = lf.geometry.buffer(radius) if radius > 0 else lf.geometry
            idx = tree.query(probe)
            if len(idx) == 0:
                continue
            cands: list[tuple[float, MatchableFeature]] = []
            for j in idx:
                rf = right[int(j)]
                if cfg.attribute_block_keys and not _block_agrees(lf, rf, cfg.attribute_block_keys):
                    continue
                ratio = rf.area / lf.area if lf.area > 0 else float("inf")
                if not (cfg.min_area_ratio <= ratio <= cfg.max_area_ratio):
                    continue
                if cfg.require_intersection and not lf.geometry.intersects(rf.geometry):
                    continue
                d = lf.centroid.distance(rf.centroid)
                cands.append((d, rf))
            cands.sort(key=lambda t: t[0])
            if len(cands) > cfg.max_candidates_per_feature:
                truncated += 1
            for _, rf in cands[: cfg.max_candidates_per_feature]:
                pairs.append((lf, rf))

        self.stats = {
            "left": len(left),
            "right": len(right),
            "search_radius_m": round(radius, 3),
            "candidate_pairs": len(pairs),
            "pairs_per_left": round(len(pairs) / max(len(left), 1), 3),
            "reduction_ratio": round(1.0 - len(pairs) / max(len(left) * len(right), 1), 9),
            "features_truncated_at_cap": truncated,
        }
        return pairs


def _block_agrees(a: MatchableFeature, b: MatchableFeature, keys: Iterable[str]) -> bool:
    for k in keys:
        av, bv = a.attributes.get(k), b.attributes.get(k)
        if av in (None, "") or bv in (None, ""):
            continue
        if str(av).strip().lstrip("0") != str(bv).strip().lstrip("0"):
            return False
    return True


# --------------------------------------------------------------------------------------
# feature extraction
# --------------------------------------------------------------------------------------

FEATURE_NAMES: tuple[str, ...] = (
    "iou",
    "containment_left",
    "containment_right",
    "centroid_distance_m",
    "centroid_distance_norm",
    "area_ratio",
    "log_area_ratio",
    "perimeter_ratio",
    "compactness_diff",
    "hausdorff_norm",
    "boundary_mean_distance_norm",
    "vertex_ratio",
    "turning_distance",
    "hu_distance",
    "orientation_diff",
    "elongation_diff",
    "attr_survey_number",
    "attr_admin_agreement",
    "attr_name_similarity",
    "context_agreement",
    "rank_left",
    "rank_right",
)


class FeatureExtractor:
    """Turns a candidate pair into the fixed-length vector the classifier consumes."""

    def __init__(self, attribute_comparators: dict[str, Callable[[Any, Any], float]] | None = None
                 ) -> None:
        self.attribute_comparators = attribute_comparators or {}

    #: Features that are cheap to compute — area arithmetic and one intersection.
    CHEAP = (
        "iou", "containment_left", "containment_right", "centroid_distance_m",
        "centroid_distance_norm", "area_ratio", "log_area_ratio", "perimeter_ratio",
        "compactness_diff", "vertex_ratio",
    )
    #: Features that cost boundary sampling, rotating calipers or moment integration.
    EXPENSIVE = (
        "hausdorff_norm", "boundary_mean_distance_norm", "turning_distance",
        "hu_distance", "orientation_diff", "elongation_diff",
        "attr_survey_number", "attr_admin_agreement", "attr_name_similarity",
    )

    #: Placeholder values for expensive features on pairs that never get them. They are
    #: set to the "no evidence of similarity" end of each feature's range, which is the
    #: truthful encoding for a pair the platform declined to examine closely.
    EXPENSIVE_DEFAULTS = {
        "hausdorff_norm": 9.0,
        "boundary_mean_distance_norm": 9.0,
        "turning_distance": 3.0,
        "hu_distance": 3.0,
        "orientation_diff": 0.5,
        "elongation_diff": 0.5,
        "attr_survey_number": 0.5,
        "attr_admin_agreement": 0.5,
        "attr_name_similarity": 0.5,
    }

    def extract(self, a: MatchableFeature, b: MatchableFeature, *,
                context: dict[str, float] | None = None,
                full: bool = True) -> dict[str, float]:
        """Describe a candidate pair.

        With ``full=False`` only the cheap half is computed and the expensive half is filled
        with its no-similarity defaults. That is what makes the matcher scale: on real urban
        data roughly two thirds of blocked candidates have zero overlap and are separated by
        several feature radii, and spending a boundary-sampling budget on those pairs to
        confirm they are not matches is the single largest cost in the pipeline. Two-stage
        scoring computes the cheap half for everything, keeps what is plausible, and pays
        for shape descriptors only where they can change the answer.
        """
        ga, gb = a.geometry, b.geometry
        try:
            inter_area = float(ga.intersection(gb).area)
        except Exception:  # noqa: BLE001
            inter_area = 0.0
        union_area = a.area + b.area - inter_area
        iou = inter_area / union_area if union_area > 0 else 0.0

        d = float(a.centroid.distance(b.centroid))
        scale = max(a.radius, b.radius, 1e-6)
        area_ratio = (min(a.area, b.area) / max(a.area, b.area)) if max(a.area, b.area) > 0 else 0.0

        feats: dict[str, float] = {
            "iou": iou,
            "containment_left": inter_area / a.area if a.area > 0 else 0.0,
            "containment_right": inter_area / b.area if b.area > 0 else 0.0,
            "centroid_distance_m": d,
            "centroid_distance_norm": d / scale,
            "area_ratio": area_ratio,
            "log_area_ratio": abs(math.log((a.area + 1e-6) / (b.area + 1e-6))),
            "perimeter_ratio": (min(a.perimeter, b.perimeter) / max(a.perimeter, b.perimeter))
                               if max(a.perimeter, b.perimeter) > 0 else 0.0,
            "compactness_diff": abs(a.compactness - b.compactness),
            "vertex_ratio": _vertex_ratio(ga, gb),
            "context_agreement": (context or {}).get("agreement", 0.0),
            "rank_left": 0.0,
            "rank_right": 0.0,
        }

        if not full:
            feats.update(self.EXPENSIVE_DEFAULTS)
            return feats

        try:
            hausdorff = float(ga.hausdorff_distance(gb))
        except Exception:  # noqa: BLE001
            hausdorff = float("nan")
        oa, ea = _orientation_elongation(ga)
        ob, eb = _orientation_elongation(gb)

        feats.update({
            "hausdorff_norm": (hausdorff / scale) if math.isfinite(hausdorff) else 9.0,
            "boundary_mean_distance_norm": min(_mean_boundary_distance(ga, gb) / scale, 9.0),
            "turning_distance": _turning_distance(a.turning, b.turning),
            "hu_distance": float(np.abs(a.hu - b.hu).sum()),
            "orientation_diff": abs(((oa - ob + 90) % 180) - 90) / 90.0,
            "elongation_diff": abs(ea - eb),
            "attr_survey_number": self._survey_number(a, b),
            "attr_admin_agreement": self._admin(a, b),
            "attr_name_similarity": self._names(a, b),
        })
        return feats

    @staticmethod
    def is_plausible(f: dict[str, float], *, max_distance_norm: float = 2.5) -> bool:
        """Whether a pair deserves the expensive descriptors.

        Deliberately generous: a pair is kept if the two features touch at all, if either
        contains a meaningful part of the other, or if their centroids are within a couple
        of feature radii. Only pairs that fail all three — no overlap, no containment and
        far apart — are settled on the cheap features alone, and those are non-matches by
        every measure the platform has.
        """
        return (f.get("iou", 0.0) > 0.0
                or max(f.get("containment_left", 0.0), f.get("containment_right", 0.0)) > 0.02
                or f.get("centroid_distance_norm", 99.0) <= max_distance_norm)

    def vector(self, feats: dict[str, float]) -> np.ndarray:
        return np.array([_finite(feats.get(k, 0.0)) for k in FEATURE_NAMES], dtype=np.float32)

    @staticmethod
    def matrix(feats: Sequence[dict[str, float]]) -> np.ndarray:
        """Stack many feature dictionaries into the model's design matrix.

        Building the matrix row by row through ``vector`` costs a Python-level sanitisation
        call per cell — about three quarters of a million of them on a single AOI, which
        the profile showed as the largest remaining cost in the matcher. Stacking first and
        sanitising the whole array with one vectorised pass does the same work an order of
        magnitude faster.
        """
        if not feats:
            return np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32)
        raw = np.array([[f.get(k, 0.0) for k in FEATURE_NAMES] for f in feats],
                       dtype=np.float64)
        return np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    # -- attribute comparators -----------------------------------------------------

    def _survey_number(self, a: MatchableFeature, b: MatchableFeature) -> float:
        from .normalise import normalise_survey_number

        av = _first(a.attributes, ("survey_number", "kide", "Survey_Number", "survey_no"))
        bv = _first(b.attributes, ("survey_number", "kide", "Survey_Number", "survey_no"))
        if av is None or bv is None:
            return 0.5  # unknown, not disagreement
        na, nb = normalise_survey_number(av), normalise_survey_number(bv)
        if not na or not nb:
            return 0.5
        if na == nb:
            return 1.0
        # parent survey number agreement (437/2A vs 437/3) is weak evidence of adjacency,
        # which is evidence *against* being the same parcel
        if na.split("/")[0] == nb.split("/")[0]:
            return 0.35
        return 0.0

    def _admin(self, a: MatchableFeature, b: MatchableFeature) -> float:
        keys = [("ward", "ward_number"), ("zone", "zone_number"),
                ("village_lgd", "lgd_village_code"), ("locality", "area_name")]
        hits = 0
        total = 0
        for ka, kb in keys:
            av = _first(a.attributes, (ka, kb))
            bv = _first(b.attributes, (ka, kb))
            if av in (None, "") or bv in (None, ""):
                continue
            total += 1
            if str(av).strip().lstrip("0").lower() == str(bv).strip().lstrip("0").lower():
                hits += 1
        return hits / total if total else 0.5

    def _names(self, a: MatchableFeature, b: MatchableFeature) -> float:
        from ..attributes.translit import canonical_place, normalise_name

        for keys in (("street", "road_name", "Rd_Name"), ("locality", "area_name", "Locality"),
                     ("village_name", "Village")):
            av = _first(a.attributes, keys)
            bv = _first(b.attributes, keys)
            if av and bv:
                pa, pb = canonical_place(str(av)), canonical_place(str(bv))
                if pa and pb:
                    from rapidfuzz.distance import JaroWinkler
                    return float(JaroWinkler.similarity(pa, pb))
        av = _first(a.attributes, ("owner_name", "owner"))
        bv = _first(b.attributes, ("owner_name", "owner"))
        if av and bv:
            return normalise_name(str(av)).similarity(normalise_name(str(bv)))
        return 0.5


# --------------------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------------------


def neighbourhood_context(pairs: Sequence[tuple[MatchableFeature, MatchableFeature]],
                          *, radius_m: float = 60.0) -> dict[tuple[str, str], float]:
    """How much the local neighbourhood agrees that a pair is a match.

    Individual small buildings on a dense street are close to indistinguishable. The row
    they sit in is not: if the eight neighbours of A all have a plausible counterpart at
    roughly the same offset as B is from A, that offset is a real registration difference
    and the pair is very likely correct. This is a cheap approximation of graph matching
    and it lifts precision on dense urban fabric substantially.
    """
    if not pairs:
        return {}
    lefts = {p[0].fid: p[0] for p in pairs}
    offsets: dict[str, tuple[float, float]] = {}
    for lf, rf in pairs:
        offsets.setdefault(lf.fid, (rf.centroid.x - lf.centroid.x,
                                    rf.centroid.y - lf.centroid.y))
    ids = list(lefts)
    geoms = [lefts[i].centroid for i in ids]
    tree = STRtree(geoms)

    # Neighbour sets depend only on the left feature, so they are found once per left
    # feature rather than once per pair. On dense urban fabric a left feature has six or
    # more candidates, so this is a six-fold reduction in R-tree queries and buffers —
    # and buffering was the third most expensive call in the profile.
    offs = np.array([offsets.get(i, (0.0, 0.0)) for i in ids], dtype=float)
    neighbours: dict[str, np.ndarray] = {}
    for i, fid in enumerate(ids):
        idx = np.asarray(tree.query(geoms[i].buffer(radius_m)), dtype=int)
        neighbours[fid] = idx[idx != i]

    out: dict[tuple[str, str], float] = {}
    for lf, rf in pairs:
        idx = neighbours.get(lf.fid)
        if idx is None or idx.size == 0:
            out[(lf.fid, rf.fid)] = 0.0
            continue
        dx = rf.centroid.x - lf.centroid.x
        dy = rf.centroid.y - lf.centroid.y
        d = np.hypot(offs[idx, 0] - dx, offs[idx, 1] - dy)
        out[(lf.fid, rf.fid)] = float(np.mean(np.exp(-d / 2.0)))
    return out


# --------------------------------------------------------------------------------------
# geometric descriptors
# --------------------------------------------------------------------------------------


def _turning_function(geom: BaseGeometry, n: int = 64) -> np.ndarray:
    """Sampled cumulative turning function of the exterior ring, normalised for start
    point and scale. Two congruent shapes have near-identical turning functions
    regardless of where they sit or how they are rotated."""
    try:
        ring = geom.exterior if geom.geom_type == "Polygon" else geom.convex_hull.exterior
        coords = np.asarray(ring.coords)[:, :2]
    except Exception:  # noqa: BLE001
        return np.zeros(n, dtype=np.float32)
    if len(coords) < 4:
        return np.zeros(n, dtype=np.float32)
    seg = np.diff(coords, axis=0)
    lengths = np.hypot(seg[:, 0], seg[:, 1])
    total = lengths.sum()
    if total <= 0:
        return np.zeros(n, dtype=np.float32)
    angles = np.unwrap(np.arctan2(seg[:, 1], seg[:, 0]))
    s = np.concatenate([[0.0], np.cumsum(lengths)]) / total
    grid = np.linspace(0, 1, n, endpoint=False)
    sampled = np.interp(grid, s[:-1], angles)
    sampled = sampled - sampled.mean()
    return sampled.astype(np.float32)


def _turning_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Rotation-invariant L2 distance between turning functions.

    The quantity wanted is ``min_k ||a - roll(b, k)||``, the best alignment over all cyclic
    shifts. Evaluating that by rolling the array is O(n²) and, at 32,000 candidate pairs,
    was the single most expensive operation in the whole pipeline.

    Expanding the norm gives ``||a||² + ||b||² - 2·max_k (a ⋆ b)[k]``, where ``a ⋆ b`` is
    the circular cross-correlation — computable for *every* shift at once by FFT in
    O(n log n). The result is not an approximation of the loop: it searches all 64 shifts
    where the loop searched 16, and it is about fifteen times faster.
    """
    n = a.size
    if n == 0 or b.size != n:
        return 3.0
    corr = np.fft.irfft(np.fft.rfft(a) * np.conj(np.fft.rfft(b)), n)
    best_dot = float(corr.max())
    sq = float(a @ a + b @ b - 2.0 * best_dot) / n
    return min(math.sqrt(max(sq, 0.0)), 3.0)


def _hu_moments(geom: BaseGeometry, grid: int = 32) -> np.ndarray:
    """Hu invariant moments computed from a rasterised mask.

    Invariant to translation, scale and rotation, so they measure pure shape. Cheap at
    32x32 and remarkably discriminative between a rectangle, an L and a compound plot.
    """
    try:
        minx, miny, maxx, maxy = geom.bounds
    except Exception:  # noqa: BLE001
        return np.zeros(7, dtype=np.float32)
    w = maxx - minx
    h = maxy - miny
    if w <= 0 or h <= 0:
        return np.zeros(7, dtype=np.float32)
    xs = np.linspace(minx, maxx, grid)
    ys = np.linspace(miny, maxy, grid)
    gx, gy = np.meshgrid(xs, ys)
    try:
        from shapely import contains_xy  # shapely >= 2.0: vectorised, ~500x faster

        mask = contains_xy(geom, gx.ravel(), gy.ravel()).reshape(grid, grid).astype(np.float64)
    except Exception:  # noqa: BLE001  # pragma: no cover - shapely 1.x fallback
        from shapely.geometry import Point

        mask = np.array(
            [[1.0 if geom.contains(Point(x, y)) else 0.0 for x in xs] for y in ys],
            dtype=np.float64,
        )
    if mask.sum() == 0:
        return np.zeros(7, dtype=np.float32)
    yy, xx = np.mgrid[0:grid, 0:grid]
    m00 = mask.sum()
    xbar = (xx * mask).sum() / m00
    ybar = (yy * mask).sum() / m00

    def mu(p: int, q: int) -> float:
        return float((((xx - xbar) ** p) * ((yy - ybar) ** q) * mask).sum())

    def nu(p: int, q: int) -> float:
        return mu(p, q) / (m00 ** (1 + (p + q) / 2.0))

    n20, n02, n11 = nu(2, 0), nu(0, 2), nu(1, 1)
    n30, n03, n21, n12 = nu(3, 0), nu(0, 3), nu(2, 1), nu(1, 2)
    h1 = n20 + n02
    h2 = (n20 - n02) ** 2 + 4 * n11 ** 2
    h3 = (n30 - 3 * n12) ** 2 + (3 * n21 - n03) ** 2
    h4 = (n30 + n12) ** 2 + (n21 + n03) ** 2
    h5 = ((n30 - 3 * n12) * (n30 + n12) * ((n30 + n12) ** 2 - 3 * (n21 + n03) ** 2)
          + (3 * n21 - n03) * (n21 + n03) * (3 * (n30 + n12) ** 2 - (n21 + n03) ** 2))
    h6 = ((n20 - n02) * ((n30 + n12) ** 2 - (n21 + n03) ** 2)
          + 4 * n11 * (n30 + n12) * (n21 + n03))
    h7 = ((3 * n21 - n03) * (n30 + n12) * ((n30 + n12) ** 2 - 3 * (n21 + n03) ** 2)
          - (n30 - 3 * n12) * (n21 + n03) * (3 * (n30 + n12) ** 2 - (n21 + n03) ** 2))
    hu = np.array([h1, h2, h3, h4, h5, h6, h7], dtype=np.float64)
    # log-scale compression keeps the higher moments from dominating
    hu = np.sign(hu) * np.log10(np.abs(hu) + 1e-30)
    return (hu / 30.0).astype(np.float32)


def _orientation_elongation(geom: BaseGeometry) -> tuple[float, float]:
    """Principal-axis orientation in degrees and elongation from the minimum rotated
    rectangle. Buildings are overwhelmingly rectangular, so this is highly informative."""
    try:
        rect = geom.minimum_rotated_rectangle
        coords = np.asarray(rect.exterior.coords)[:-1, :2]
    except Exception:  # noqa: BLE001
        return 0.0, 0.0
    if len(coords) < 4:
        return 0.0, 0.0
    edges = np.diff(np.vstack([coords, coords[:1]]), axis=0)
    lens = np.hypot(edges[:, 0], edges[:, 1])
    i = int(np.argmax(lens))
    ang = math.degrees(math.atan2(edges[i, 1], edges[i, 0])) % 180.0
    long_side = float(lens.max())
    short_side = float(sorted(lens)[1]) if len(lens) > 1 else long_side
    elong = 1.0 - (short_side / long_side if long_side > 0 else 1.0)
    return ang, elong


def _mean_boundary_distance(a: BaseGeometry, b: BaseGeometry, n: int = 16) -> float:
    """Average distance from sampled points on A's boundary to B's boundary.

    Less brittle than Hausdorff, which is dominated by a single worst vertex — and a single
    worst vertex is exactly what a spike artefact produces.

    Sampling and measurement are both vectorised over the whole point set. Shapely 2's
    array interface does the n interpolations and n distances in two calls into GEOS
    instead of 2n, and the per-call Python overhead was costing more than the geometry.
    """
    try:
        import shapely

        ba, bb = a.boundary, b.boundary
        if ba.is_empty or bb.is_empty:
            return 1e6
        length = ba.length
        if length <= 0:
            return 1e6
        pts = shapely.line_interpolate_point(ba, np.linspace(0.0, length, n, endpoint=False))
        return float(np.mean(shapely.distance(pts, bb)))
    except Exception:  # noqa: BLE001
        return 1e6


def _vertex_ratio(a: BaseGeometry, b: BaseGeometry) -> float:
    def count(g: BaseGeometry) -> int:
        if g.geom_type == "Polygon":
            return len(g.exterior.coords)
        if hasattr(g, "geoms"):
            return sum(count(x) for x in g.geoms)
        try:
            return len(g.coords)
        except Exception:  # noqa: BLE001
            return 0

    ca, cb = count(a), count(b)
    if max(ca, cb) == 0:
        return 0.0
    return min(ca, cb) / max(ca, cb)


def _first(d: dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _finite(v: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return f
