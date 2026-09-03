"""Topological validation.

A cadastral fabric is not a bag of polygons; it is a **planar partition**. Every point of
the surveyed area belongs to exactly one parcel. When that invariant breaks — and it breaks
constantly once two independently produced layers are put in the same database — the
consequences are not cosmetic:

* a **gap** is land that belongs to nobody, which is precisely what an encroachment claim
  is built on;
* an **overlap** is land claimed twice, which is a title dispute waiting in a file;
* a **sliver** is either of the above, small enough that nobody notices until a boundary
  wall is built on it.

This module finds them. ``samanvay.topology.repair`` fixes them. Keeping detection and
repair apart matters, because the audit record has to show what was wrong *before* anything
was changed.

The rules implemented map onto the OGC Simple Features validity model plus the additional
integrity rules a cadastre needs, and each carries a severity so that triage is possible:
a self-intersecting ring is fatal, a 3 cm sliver is not.
"""

from __future__ import annotations

import enum
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree


class Severity(str, enum.Enum):
    FATAL = "fatal"        # the geometry is not usable at all
    ERROR = "error"        # violates a cadastral invariant
    WARNING = "warning"    # suspicious, may be legitimate
    INFO = "info"


class RuleId(str, enum.Enum):
    INVALID_GEOMETRY = "invalid_geometry"
    SELF_INTERSECTION = "self_intersection"
    EMPTY_GEOMETRY = "empty_geometry"
    DUPLICATE_VERTEX = "duplicate_vertex"
    SPIKE = "spike"
    RING_ORIENTATION = "ring_orientation"
    OVERLAP = "overlap"
    GAP = "gap"
    SLIVER = "sliver"
    DANGLE = "dangle"
    UNDERSHOOT = "undershoot"
    DISCONNECTED_NETWORK = "disconnected_network"
    NOT_COVERED_BY_PARENT = "not_covered_by_parent"
    MULTIPART_PARCEL = "multipart_parcel"
    MICRO_AREA = "micro_area"


@dataclass
class Violation:
    rule: RuleId
    severity: Severity
    feature_ids: list[str]
    message: str
    geometry_wkt: str | None = None
    measure: float | None = None
    """The quantity that triggered the rule — area of the sliver, length of the dangle."""
    auto_repairable: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule.value,
            "severity": self.severity.value,
            "feature_ids": self.feature_ids,
            "message": self.message,
            "measure": self.measure,
            "auto_repairable": self.auto_repairable,
            "geometry_wkt": self.geometry_wkt,
        }


@dataclass
class ValidationConfig:
    """Thresholds, in the units of the working CRS (metres — never degrees)."""

    sliver_max_area_m2: float = 5.0
    sliver_max_thinness: float = 0.18
    """Polsby-Popper compactness below which a shape is a sliver rather than a parcel."""
    spike_angle_deg: float = 6.0
    duplicate_vertex_tol_m: float = 0.005
    overlap_min_area_m2: float = 0.25
    gap_min_area_m2: float = 0.25
    gap_max_area_m2: float = 2000.0
    """Above this a hole is a genuine unsurveyed block (a road, a tank), not a defect."""
    dangle_max_length_m: float = 3.0
    undershoot_tol_m: float = 1.0
    micro_area_m2: float = 1.0


@dataclass
class ValidationReport:
    n_features: int = 0
    violations: list[Violation] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    total_overlap_area_m2: float = 0.0
    total_gap_area_m2: float = 0.0
    planar_partition_error: float = 0.0
    """Fraction of the total area that is either double-covered or uncovered."""

    def add(self, v: Violation) -> None:
        self.violations.append(v)
        self.counts[v.rule.value] = self.counts.get(v.rule.value, 0) + 1

    def by_severity(self, s: Severity) -> list[Violation]:
        return [v for v in self.violations if v.severity == s]

    @property
    def clean(self) -> bool:
        return not any(v.severity in (Severity.FATAL, Severity.ERROR) for v in self.violations)

    def summary(self) -> str:
        f = len(self.by_severity(Severity.FATAL))
        e = len(self.by_severity(Severity.ERROR))
        w = len(self.by_severity(Severity.WARNING))
        return (
            f"{self.n_features} features: {f} fatal, {e} errors, {w} warnings; "
            f"planar partition error {self.planar_partition_error * 100:.4f}% "
            f"(overlap {self.total_overlap_area_m2:,.1f} m², gap {self.total_gap_area_m2:,.1f} m²)"
        )


# --------------------------------------------------------------------------------------


class TopologyValidator:
    """Validates a layer against the cadastral integrity rules.

    Geometries must already be in a **projected, metric** CRS. Validating in degrees is the
    single most common way a topology check silently passes: a 5 m sliver has an area of
    about 4e-9 square degrees, which is below every sensible threshold.
    """

    def __init__(self, config: ValidationConfig | None = None) -> None:
        self.config = config or ValidationConfig()

    def validate(self, features: dict[str, BaseGeometry], *,
                 check_partition: bool = True) -> ValidationReport:
        cfg = self.config
        report = ValidationReport(n_features=len(features))

        ids = list(features)
        geoms = [features[i] for i in ids]

        # -- per-feature rules ----------------------------------------------------
        for fid, g in zip(ids, geoms):
            if g is None or g.is_empty:
                report.add(Violation(RuleId.EMPTY_GEOMETRY, Severity.FATAL, [fid],
                                     "geometry is empty", auto_repairable=False))
                continue
            if not g.is_valid:
                reason = _explain_invalid(g)
                report.add(Violation(
                    RuleId.INVALID_GEOMETRY, Severity.FATAL, [fid],
                    f"invalid geometry: {reason}",
                    geometry_wkt=g.wkt[:2000], auto_repairable=True,
                ))
            if g.geom_type in ("Polygon", "MultiPolygon"):
                area = g.area
                if area < cfg.micro_area_m2:
                    report.add(Violation(
                        RuleId.MICRO_AREA, Severity.WARNING, [fid],
                        f"area {area:.3f} m² is below the smallest recordable parcel",
                        measure=area, auto_repairable=False,
                    ))
                if _thinness(g) < cfg.sliver_max_thinness and area < cfg.sliver_max_area_m2:
                    report.add(Violation(
                        RuleId.SLIVER, Severity.ERROR, [fid],
                        f"sliver polygon: area {area:.3f} m², compactness {_thinness(g):.3f}",
                        measure=area, auto_repairable=True,
                    ))
                if isinstance(g, MultiPolygon) and len(g.geoms) > 1:
                    report.add(Violation(
                        RuleId.MULTIPART_PARCEL, Severity.WARNING, [fid],
                        f"parcel has {len(g.geoms)} disjoint parts; legitimate for a "
                        f"subdivided holding, a defect if it came from a bad union",
                        measure=float(len(g.geoms)), auto_repairable=False,
                    ))
                for v in self._ring_rules(fid, g):
                    report.add(v)

        # -- pairwise and partition rules ----------------------------------------
        if check_partition:
            # Partition checks run on repaired copies. Real cadastral data always contains
            # some self-intersecting rings, and GEOS raises a side-location conflict rather
            # than returning a wrong answer when it meets one — which would abort the whole
            # run over a handful of bad polygons. The invalidity is still reported above,
            # against the feature that actually has it; here it is only worked around so
            # that the other several hundred thousand features can still be checked.
            polys: dict[str, BaseGeometry] = {}
            for i, g in zip(ids, geoms):
                if g is None or g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
                    continue
                polys[i] = g if g.is_valid else _coerce_valid(g)
            polys = {i: g for i, g in polys.items()
                     if g is not None and not g.is_empty
                     and g.geom_type in ("Polygon", "MultiPolygon")}
            if polys:
                self._overlaps(polys, report)
                self._gaps(polys, report)
                total = sum(g.area for g in polys.values()) or 1.0
                report.planar_partition_error = (
                    report.total_overlap_area_m2 + report.total_gap_area_m2
                ) / total
        return report

    # -- rule groups --------------------------------------------------------------

    def _ring_rules(self, fid: str, g: BaseGeometry) -> Iterable[Violation]:
        cfg = self.config
        polys = g.geoms if isinstance(g, MultiPolygon) else [g]
        for poly in polys:
            if not isinstance(poly, Polygon):
                continue
            for ring, kind in [(poly.exterior, "exterior")] + [(r, "interior") for r in poly.interiors]:
                coords = list(ring.coords)
                # duplicate vertices
                dups = sum(
                    1 for a, b in zip(coords[:-1], coords[1:])
                    if math.hypot(b[0] - a[0], b[1] - a[1]) < cfg.duplicate_vertex_tol_m
                )
                if dups:
                    yield Violation(
                        RuleId.DUPLICATE_VERTEX, Severity.WARNING, [fid],
                        f"{dups} coincident vertices on the {kind} ring",
                        measure=float(dups), auto_repairable=True,
                    )
                # spikes
                spikes = _count_spikes(coords, cfg.spike_angle_deg)
                if spikes:
                    yield Violation(
                        RuleId.SPIKE, Severity.ERROR, [fid],
                        f"{spikes} spike vertices (interior angle below "
                        f"{cfg.spike_angle_deg}°) on the {kind} ring",
                        measure=float(spikes), auto_repairable=True,
                    )
            # orientation: OGC requires exterior CCW, interiors CW
            if not poly.exterior.is_ccw:
                yield Violation(
                    RuleId.RING_ORIENTATION, Severity.INFO, [fid],
                    "exterior ring is clockwise; OGC simple features expects "
                    "counter-clockwise", auto_repairable=True,
                )

    def _overlaps(self, polys: dict[str, BaseGeometry], report: ValidationReport) -> None:
        cfg = self.config
        ids = list(polys)
        geoms = [polys[i] for i in ids]
        tree = STRtree(geoms)
        seen: set[tuple[int, int]] = set()
        for i, g in enumerate(geoms):
            for j in tree.query(g):
                j = int(j)
                if j <= i:
                    continue
                key = (i, j)
                if key in seen:
                    continue
                seen.add(key)
                other = geoms[j]
                try:
                    if not g.intersects(other):
                        continue
                    inter = g.intersection(other)
                except Exception:  # noqa: BLE001
                    # a predicate failure on one pair must not abort the scan over the
                    # other quarter of a million
                    continue
                if inter.is_empty or inter.geom_type in ("Point", "LineString",
                                                         "MultiPoint", "MultiLineString"):
                    continue
                a = inter.area
                if a < cfg.overlap_min_area_m2:
                    continue
                report.total_overlap_area_m2 += a
                sliver = _thinness(inter) < cfg.sliver_max_thinness
                report.add(Violation(
                    RuleId.SLIVER if sliver else RuleId.OVERLAP,
                    Severity.ERROR,
                    [ids[i], ids[j]],
                    (f"{'sliver ' if sliver else ''}overlap of {a:,.3f} m² between "
                     f"{ids[i]} and {ids[j]} — this is land claimed twice"),
                    geometry_wkt=inter.wkt[:2000],
                    measure=a,
                    auto_repairable=True,
                ))

    def _gaps(self, polys: dict[str, BaseGeometry], report: ValidationReport) -> None:
        """Find holes inside the union of the fabric.

        The union of a correct planar partition has no interior rings. Every interior ring
        of the union is either a legitimate excluded block — a road reservation, a tank, a
        temple — or a defect. Size and shape separate them: a defect is small and thin.
        """
        from shapely.ops import unary_union

        cfg = self.config
        try:
            merged = unary_union(list(polys.values()))
        except Exception:  # noqa: BLE001
            try:
                merged = unary_union([g.buffer(0) for g in polys.values()])
            except Exception:  # noqa: BLE001
                report.add(Violation(
                    RuleId.INVALID_GEOMETRY, Severity.WARNING, [],
                    "gap analysis skipped: the layer could not be unioned even after "
                    "coercion, which means it contains geometry GEOS cannot process",
                ))
                return
        parts = merged.geoms if isinstance(merged, MultiPolygon) else [merged]
        tree_ids = list(polys)
        tree = STRtree(list(polys.values()))
        for part in parts:
            if not isinstance(part, Polygon):
                continue
            for ring in part.interiors:
                hole = Polygon(ring)
                a = hole.area
                if a < cfg.gap_min_area_m2 or a > cfg.gap_max_area_m2:
                    continue
                report.total_gap_area_m2 += a
                neighbours = [tree_ids[int(k)] for k in tree.query(hole)][:8]
                report.add(Violation(
                    RuleId.GAP, Severity.ERROR, neighbours,
                    (f"gap of {a:,.3f} m² enclosed by the fabric — land belonging to no "
                     f"parcel, the raw material of an encroachment claim"),
                    geometry_wkt=hole.wkt[:2000],
                    measure=a,
                    auto_repairable=True,
                ))


# --------------------------------------------------------------------------------------
# network topology (utility and transport layers)
# --------------------------------------------------------------------------------------


@dataclass
class NetworkReport:
    n_edges: int
    n_nodes: int
    n_components: int
    dangles: list[tuple[str, tuple[float, float]]] = field(default_factory=list)
    undershoots: list[tuple[str, str, float]] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.n_edges} edges, {self.n_nodes} nodes, {self.n_components} connected "
            f"components, {len(self.dangles)} dangles, {len(self.undershoots)} undershoots"
        )


def validate_network(edges: dict[str, LineString],
                     config: ValidationConfig | None = None) -> NetworkReport:
    """Connectivity checks for utility and road networks.

    A water main that does not connect is not a cartographic nuisance; it is the reason a
    ward shows as unserved in a coverage analysis. Dangles and undershoots are what break
    connectivity, and both are mechanically detectable.
    """
    cfg = config or ValidationConfig()
    node_of: dict[tuple[float, float], int] = {}
    adjacency: dict[int, set[int]] = defaultdict(set)
    endpoints: list[tuple[str, tuple[float, float]]] = []
    degree: dict[tuple[float, float], int] = defaultdict(int)

    def key(pt: tuple[float, float]) -> tuple[float, float]:
        return (round(pt[0], 3), round(pt[1], 3))

    for eid, line in edges.items():
        if line is None or line.is_empty:
            continue
        a, b = key(line.coords[0]), key(line.coords[-1])
        for p in (a, b):
            if p not in node_of:
                node_of[p] = len(node_of)
            degree[p] += 1
        adjacency[node_of[a]].add(node_of[b])
        adjacency[node_of[b]].add(node_of[a])
        endpoints.append((eid, a))
        endpoints.append((eid, b))

    # connected components
    seen: set[int] = set()
    components = 0
    for n in list(adjacency):
        if n in seen:
            continue
        components += 1
        stack = [n]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adjacency[cur] - seen)

    dangles = [(eid, pt) for eid, pt in endpoints if degree[pt] == 1]

    # undershoots: a dangle that stops just short of another edge
    undershoots: list[tuple[str, str, float]] = []
    ids = list(edges)
    tree = STRtree([edges[i] for i in ids])
    for eid, pt in dangles:
        p = Point(pt)
        for j in tree.query(p.buffer(cfg.undershoot_tol_m)):
            other = ids[int(j)]
            if other == eid:
                continue
            d = p.distance(edges[other])
            if 1e-9 < d <= cfg.undershoot_tol_m:
                undershoots.append((eid, other, d))
                break

    violations = [
        Violation(RuleId.DANGLE, Severity.WARNING, [eid],
                  f"dangling end at ({pt[0]:.2f}, {pt[1]:.2f})", auto_repairable=True)
        for eid, pt in dangles
    ] + [
        Violation(RuleId.UNDERSHOOT, Severity.ERROR, [a, b],
                  f"undershoot of {d * 100:.1f} cm — the network is broken here",
                  measure=d, auto_repairable=True)
        for a, b, d in undershoots
    ]
    if components > 1:
        violations.append(Violation(
            RuleId.DISCONNECTED_NETWORK, Severity.WARNING, [],
            f"network splits into {components} disconnected components",
            measure=float(components), auto_repairable=False,
        ))

    return NetworkReport(
        n_edges=len(edges), n_nodes=len(node_of), n_components=components,
        dangles=dangles, undershoots=undershoots, violations=violations,
    )


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _coerce_valid(geom: BaseGeometry) -> BaseGeometry | None:
    """Best-effort repair for the purposes of *analysis*, never for output."""
    from shapely.ops import unary_union
    from shapely.validation import make_valid

    for attempt in (lambda g: make_valid(g), lambda g: g.buffer(0)):
        try:
            fixed = attempt(geom)
        except Exception:  # noqa: BLE001
            continue
        if fixed is None or fixed.is_empty:
            continue
        if fixed.geom_type in ("Polygon", "MultiPolygon"):
            return fixed
        if hasattr(fixed, "geoms"):
            parts = [g for g in fixed.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
            if parts:
                try:
                    return unary_union(parts)
                except Exception:  # noqa: BLE001
                    return parts[0]
    return None


def _thinness(geom: BaseGeometry) -> float:
    """Polsby-Popper compactness: 4*pi*A / P^2. A circle is 1, a hairline approaches 0."""
    p = geom.length
    if p <= 0:
        return 0.0
    return float(4 * math.pi * geom.area / (p * p))


def _count_spikes(coords: Sequence[tuple[float, ...]], min_angle_deg: float) -> int:
    n = len(coords) - 1
    if n < 3:
        return 0
    count = 0
    for i in range(n):
        a = coords[i - 1]
        b = coords[i]
        c = coords[(i + 1) % n]
        v1 = (a[0] - b[0], a[1] - b[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        n1 = math.hypot(*v1)
        n2 = math.hypot(*v2)
        if n1 < 1e-12 or n2 < 1e-12:
            continue
        cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        if math.degrees(math.acos(cos)) < min_angle_deg:
            count += 1
    return count


def _explain_invalid(geom: BaseGeometry) -> str:
    try:
        from shapely.validation import explain_validity

        return explain_validity(geom)
    except Exception:  # noqa: BLE001  # pragma: no cover
        return "unknown"
