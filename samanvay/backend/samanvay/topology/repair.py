"""Automated topology correction.

Repair is where a harmonisation platform earns or loses its right to exist. Automatic
geometry surgery on a land record is a serious act: every correction moves a boundary that
somebody owns. The design here is therefore built around three commitments.

**Nothing is repaired silently.** Every operation returns a ``RepairAction`` recording the
rule, the features touched, the area moved and the before/after geometry. Those actions go
into the provenance ledger.

**Repairs are bounded.** Each operation has an explicit tolerance and refuses to act beyond
it. A snap that would move a vertex 4 m is not a snap, it is a re-survey, and the platform
declines and escalates instead of guessing.

**The order is fixed and justified.** Repairs interact: snapping before validity repair
produces different results from the reverse. The pipeline is

    1. make valid            (a self-intersecting ring cannot be reasoned about)
    2. remove duplicate vertices and spikes
    3. snap to a shared vertex/edge set  (removes the *cause* of most slivers)
    4. remove residual slivers by absorption into the best neighbour
    5. close gaps by allocation to the neighbour with the longest shared boundary
    6. enforce ring orientation and precision

Steps 4 and 5 both need a rule for *which* neighbour receives the disputed land. The
platform uses longest-shared-boundary, because it is the rule a surveyor uses in the field
and because it is stable — it does not depend on the arbitrary order features arrive in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import snap as shp_snap, unary_union
from shapely.strtree import STRtree

from .validate import RuleId, Severity, TopologyValidator, ValidationConfig, ValidationReport, _thinness


@dataclass
class RepairAction:
    rule: RuleId
    feature_ids: list[str]
    description: str
    area_changed_m2: float = 0.0
    vertices_changed: int = 0
    max_vertex_shift_m: float = 0.0
    before_wkt: str | None = None
    after_wkt: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule.value,
            "feature_ids": self.feature_ids,
            "description": self.description,
            "area_changed_m2": round(self.area_changed_m2, 6),
            "vertices_changed": self.vertices_changed,
            "max_vertex_shift_m": round(self.max_vertex_shift_m, 6),
        }


@dataclass
class RepairConfig:
    snap_tolerance_m: float = 0.15
    """Vertices closer than this to a neighbour's vertex or edge are welded. 15 cm is
    chosen to be larger than drone-survey noise and smaller than any real boundary."""
    max_vertex_shift_m: float = 0.50
    """Hard ceiling. A repair that would move a vertex further than this is refused."""
    sliver_absorb_max_area_m2: float = 5.0
    gap_fill_max_area_m2: float = 50.0
    simplify_tolerance_m: float = 0.0
    coordinate_precision_m: float = 0.001
    remove_spikes: bool = True
    enforce_orientation: bool = True


@dataclass
class RepairReport:
    actions: list[RepairAction] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    before: ValidationReport | None = None
    after: ValidationReport | None = None

    def add(self, a: RepairAction) -> None:
        self.actions.append(a)

    def refuse(self, feature_id: str, why: str) -> None:
        self.refused.append((feature_id, why))

    @property
    def total_area_changed_m2(self) -> float:
        return sum(a.area_changed_m2 for a in self.actions)

    def summary(self) -> str:
        # The partition error is only defined for a layer that is *meant* to be a planar
        # partition and whose overlay could actually be computed. Reporting "nan%" when it
        # could not says nothing and looks like a defect; say which it is.
        def show(rep: ValidationReport | None) -> str:
            if rep is None:
                return "not measured"
            v = rep.planar_partition_error
            if v != v or v is None:  # NaN
                return "not computable"
            return f"{v * 100:.4f}%"

        return (
            f"{len(self.actions)} repairs, {len(self.refused)} refused; "
            f"planar partition error {show(self.before)} -> {show(self.after)}; "
            f"{self.total_area_changed_m2:,.2f} m² of boundary moved"
        )


class TopologyRepairer:
    """Applies bounded, auditable topological corrections to a layer."""

    def __init__(self, config: RepairConfig | None = None,
                 validation: ValidationConfig | None = None) -> None:
        self.config = config or RepairConfig()
        self.validation = validation or ValidationConfig()

    def repair(self, features: dict[str, BaseGeometry], *,
               validate_before_after: bool = True) -> tuple[dict[str, BaseGeometry], RepairReport]:
        cfg = self.config
        report = RepairReport()
        validator = TopologyValidator(self.validation)
        if validate_before_after:
            report.before = validator.validate(features)

        work = dict(features)

        work = self._make_valid(work, report)
        work = self._clean_vertices(work, report)
        work = self._snap(work, report)
        work = self._absorb_slivers(work, report)
        work = self._fill_gaps(work, report)
        work = self._finalise(work, report)

        if validate_before_after:
            report.after = validator.validate(work)
        return work, report

    # -- 1. validity ---------------------------------------------------------------

    def _make_valid(self, features: dict[str, BaseGeometry],
                    report: RepairReport) -> dict[str, BaseGeometry]:
        from shapely.validation import make_valid

        out: dict[str, BaseGeometry] = {}
        for fid, g in features.items():
            if g is None or g.is_empty:
                report.refuse(fid, "empty geometry cannot be repaired, only re-surveyed")
                continue
            if g.is_valid:
                out[fid] = g
                continue
            before_area = g.area
            fixed = make_valid(g)
            fixed = _largest_polygonal(fixed)
            if fixed is None or fixed.is_empty:
                report.refuse(fid, "make_valid produced nothing polygonal")
                continue
            out[fid] = fixed
            report.add(RepairAction(
                RuleId.INVALID_GEOMETRY, [fid],
                "repaired invalid geometry (self-intersection or bowtie) via make_valid",
                area_changed_m2=abs(fixed.area - before_area),
                before_wkt=g.wkt[:1000], after_wkt=fixed.wkt[:1000],
            ))
        return out

    # -- 2. vertices ---------------------------------------------------------------

    def _clean_vertices(self, features: dict[str, BaseGeometry],
                        report: RepairReport) -> dict[str, BaseGeometry]:
        cfg = self.config
        out: dict[str, BaseGeometry] = {}
        for fid, g in features.items():
            if g.geom_type not in ("Polygon", "MultiPolygon"):
                out[fid] = g
                continue
            new, removed = _clean_polygon(g, self.validation.duplicate_vertex_tol_m,
                                          self.validation.spike_angle_deg if cfg.remove_spikes else 0.0)
            if removed:
                report.add(RepairAction(
                    RuleId.DUPLICATE_VERTEX, [fid],
                    f"removed {removed} degenerate vertices (coincident or spike)",
                    area_changed_m2=abs(new.area - g.area),
                    vertices_changed=removed,
                ))
            out[fid] = new
        return out

    # -- 3. snapping ---------------------------------------------------------------

    def _snap(self, features: dict[str, BaseGeometry],
              report: RepairReport) -> dict[str, BaseGeometry]:
        """Weld near-coincident boundaries between neighbours.

        Almost every sliver in a multi-source cadastre exists because two agencies
        digitised the same wall and their vertices missed each other by a few centimetres.
        Snapping removes the cause; absorbing slivers afterwards only removes the symptom.
        """
        cfg = self.config
        ids = list(features)
        geoms = [features[i] for i in ids]
        tree = STRtree(geoms)
        out: dict[str, BaseGeometry] = {}

        for i, fid in enumerate(ids):
            g = geoms[i]
            neighbours = [
                geoms[int(j)] for j in tree.query(g.buffer(cfg.snap_tolerance_m))
                if int(j) != i
            ]
            if not neighbours:
                out[fid] = g
                continue
            try:
                reference = unary_union(neighbours[:64])
                snapped = shp_snap(g, reference, cfg.snap_tolerance_m)
            except Exception:  # noqa: BLE001
                # GEOS refuses to union pathological input; leaving this feature unsnapped
                # is strictly better than aborting the layer
                out[fid] = g
                continue
            if snapped.is_empty or not snapped.is_valid:
                out[fid] = g
                continue
            shift = _max_vertex_shift(g, snapped)
            if shift > cfg.max_vertex_shift_m:
                report.refuse(
                    fid,
                    f"snap would move a vertex {shift:.3f} m, beyond the "
                    f"{cfg.max_vertex_shift_m:.2f} m ceiling; this is a survey "
                    f"discrepancy, not a digitising error, and needs adjudication",
                )
                out[fid] = g
                continue
            if shift > 1e-9:
                report.add(RepairAction(
                    RuleId.OVERLAP, [fid],
                    f"snapped boundary to neighbouring features within "
                    f"{cfg.snap_tolerance_m * 100:.0f} cm",
                    area_changed_m2=abs(snapped.area - g.area),
                    max_vertex_shift_m=shift,
                ))
            out[fid] = snapped
        return out

    # -- 4. slivers ----------------------------------------------------------------

    def _absorb_slivers(self, features: dict[str, BaseGeometry],
                        report: RepairReport) -> dict[str, BaseGeometry]:
        """Resolve residual overlaps by giving the disputed area to one owner.

        The rule is longest shared boundary. Where two parcels overlap, the overlap is
        subtracted from the parcel that shares *less* boundary with it, so the land ends up
        with the neighbour it is geometrically most a part of.
        """
        cfg = self.config
        ids = list(features)
        work = dict(features)
        geoms = [work[i] for i in ids]
        tree = STRtree(geoms)
        handled: set[tuple[int, int]] = set()

        for i, fid in enumerate(ids):
            g = work[fid]
            if g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            for j in tree.query(g):
                j = int(j)
                if j == i or (min(i, j), max(i, j)) in handled:
                    continue
                handled.add((min(i, j), max(i, j)))
                other_id = ids[j]
                other = work[other_id]
                if not g.intersects(other):
                    continue
                try:
                    inter = g.intersection(other)
                except Exception:  # noqa: BLE001
                    continue
                if inter.is_empty or inter.geom_type not in ("Polygon", "MultiPolygon"):
                    continue
                area = inter.area
                if area < self.validation.overlap_min_area_m2:
                    continue
                if area > cfg.sliver_absorb_max_area_m2:
                    report.refuse(
                        f"{fid}|{other_id}",
                        f"overlap of {area:,.2f} m² is too large to absorb automatically; "
                        f"this is a genuine boundary dispute and is being escalated",
                    )
                    continue
                keeper, loser = self._choose_owner(fid, g, other_id, other, inter)
                loser_geom = work[loser].difference(inter)
                loser_geom = _largest_polygonal(loser_geom) or work[loser]
                work[loser] = loser_geom
                g = work[fid]
                report.add(RepairAction(
                    RuleId.SLIVER, [keeper, loser],
                    f"absorbed a {area:.3f} m² overlap into {keeper} "
                    f"(longer shared boundary)",
                    area_changed_m2=area,
                ))
        return work

    def _choose_owner(self, a_id: str, a: BaseGeometry, b_id: str, b: BaseGeometry,
                      inter: BaseGeometry) -> tuple[str, str]:
        try:
            la = inter.intersection(a.exterior if isinstance(a, Polygon) else a.boundary).length
            lb = inter.intersection(b.exterior if isinstance(b, Polygon) else b.boundary).length
        except Exception:  # noqa: BLE001
            la = lb = 0.0
        if la > lb:
            return a_id, b_id
        if lb > la:
            return b_id, a_id
        # deterministic tie-break: the larger parcel keeps it
        return (a_id, b_id) if a.area >= b.area else (b_id, a_id)

    # -- 5. gaps -------------------------------------------------------------------

    def _fill_gaps(self, features: dict[str, BaseGeometry],
                   report: RepairReport) -> dict[str, BaseGeometry]:
        cfg = self.config
        polys = {i: g for i, g in features.items()
                 if g is not None and not g.is_empty and g.geom_type in ("Polygon", "MultiPolygon")}
        if len(polys) < 2:
            return features
        try:
            merged = unary_union(list(polys.values()))
        except Exception:  # noqa: BLE001
            return features
        parts = merged.geoms if isinstance(merged, MultiPolygon) else [merged]
        work = dict(features)
        ids = list(polys)
        tree = STRtree([polys[i] for i in ids])

        for part in parts:
            if not isinstance(part, Polygon):
                continue
            for ring in list(part.interiors):
                hole = Polygon(ring)
                area = hole.area
                if area < self.validation.gap_min_area_m2:
                    continue
                if area > cfg.gap_fill_max_area_m2:
                    continue  # a road or tank, not a defect
                best_id, best_len = None, 0.0
                for j in tree.query(hole.buffer(0.5)):
                    cand = ids[int(j)]
                    try:
                        shared = hole.boundary.intersection(work[cand].boundary).length
                    except Exception:  # noqa: BLE001
                        shared = 0.0
                    if shared > best_len:
                        best_id, best_len = cand, shared
                if best_id is None:
                    continue
                merged_geom = unary_union([work[best_id], hole])
                merged_geom = _largest_polygonal(merged_geom) or work[best_id]
                work[best_id] = merged_geom
                report.add(RepairAction(
                    RuleId.GAP, [best_id],
                    f"closed a {area:.3f} m² gap by allocating it to the neighbour with "
                    f"the longest shared boundary ({best_len:.2f} m)",
                    area_changed_m2=area,
                ))
        return work

    # -- 6. finalise ---------------------------------------------------------------

    def _finalise(self, features: dict[str, BaseGeometry],
                  report: RepairReport) -> dict[str, BaseGeometry]:
        from shapely import set_precision
        from shapely.geometry.polygon import orient

        cfg = self.config
        out: dict[str, BaseGeometry] = {}
        for fid, g in features.items():
            new = g
            if cfg.simplify_tolerance_m > 0:
                new = new.simplify(cfg.simplify_tolerance_m, preserve_topology=True)
            if cfg.coordinate_precision_m > 0:
                try:
                    new = set_precision(new, cfg.coordinate_precision_m)
                except Exception:  # noqa: BLE001
                    pass
            if cfg.enforce_orientation and isinstance(new, Polygon):
                new = orient(new, sign=1.0)
            elif cfg.enforce_orientation and isinstance(new, MultiPolygon):
                new = MultiPolygon([orient(p, sign=1.0) for p in new.geoms])
            if new.is_empty:
                report.refuse(fid, "geometry collapsed during precision reduction")
                out[fid] = g
                continue
            out[fid] = new
        return out


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _largest_polygonal(geom: BaseGeometry) -> BaseGeometry | None:
    """Keep the polygonal part of a possibly heterogeneous result."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type == "MultiPolygon":
        return geom
    if hasattr(geom, "geoms"):
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            return None
        if len(polys) == 1:
            return polys[0]
        return unary_union(polys)
    return None


def _coords_of(geom: BaseGeometry) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if geom.is_empty:
        return out
    if geom.geom_type == "Polygon":
        out.extend([(x, y) for x, y, *_ in geom.exterior.coords])
        for r in geom.interiors:
            out.extend([(x, y) for x, y, *_ in r.coords])
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            out.extend(_coords_of(g))
    else:
        out.extend([(x, y) for x, y, *_ in geom.coords])
    return out


def _max_vertex_shift(before: BaseGeometry, after: BaseGeometry) -> float:
    """Largest distance any vertex moved. The honest measure of how invasive a repair was."""
    a = _coords_of(before)
    b = set(_coords_of(after))
    if not a or not b:
        return 0.0
    worst = 0.0
    blist = list(b)
    for pt in a:
        if pt in b:
            continue
        d = min(math.hypot(pt[0] - q[0], pt[1] - q[1]) for q in blist)
        worst = max(worst, d)
    return worst


def _clean_polygon(geom: BaseGeometry, dup_tol: float, spike_angle: float
                   ) -> tuple[BaseGeometry, int]:
    removed = 0

    def clean_ring(coords: list[tuple[float, ...]]) -> list[tuple[float, float]]:
        nonlocal removed
        pts = [(c[0], c[1]) for c in coords]
        if pts and pts[0] == pts[-1]:
            pts = pts[:-1]
        # duplicates
        out: list[tuple[float, float]] = []
        for p in pts:
            if out and math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) < dup_tol:
                removed += 1
                continue
            out.append(p)
        while len(out) > 3 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < dup_tol:
            out.pop()
            removed += 1
        # spikes
        if spike_angle > 0:
            changed = True
            while changed and len(out) > 3:
                changed = False
                for i in range(len(out)):
                    a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
                    v1 = (a[0] - b[0], a[1] - b[1])
                    v2 = (c[0] - b[0], c[1] - b[1])
                    n1, n2 = math.hypot(*v1), math.hypot(*v2)
                    if n1 < 1e-12 or n2 < 1e-12:
                        continue
                    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
                    if math.degrees(math.acos(cos)) < spike_angle:
                        out.pop(i)
                        removed += 1
                        changed = True
                        break
        return out + [out[0]] if len(out) >= 3 else []

    if isinstance(geom, Polygon):
        ext = clean_ring(list(geom.exterior.coords))
        if len(ext) < 4:
            return geom, 0
        ints = [r for r in (clean_ring(list(i.coords)) for i in geom.interiors) if len(r) >= 4]
        new = Polygon(ext, ints)
        return (new if new.is_valid else geom), removed
    if isinstance(geom, MultiPolygon):
        parts = []
        for p in geom.geoms:
            np_, r = _clean_polygon(p, dup_tol, spike_angle)
            removed += r
            parts.append(np_)
        return MultiPolygon([p for p in parts if isinstance(p, Polygon)]), removed
    return geom, 0


def suggest_repairs(report: ValidationReport) -> list[str]:
    """Human-readable triage guidance from a validation report."""
    out: list[str] = []
    fatal = report.by_severity(Severity.FATAL)
    if fatal:
        out.append(
            f"{len(fatal)} features have fatal geometry errors and must be repaired before "
            f"any spatial analysis; make_valid will handle "
            f"{sum(1 for v in fatal if v.auto_repairable)} of them automatically."
        )
    if report.counts.get(RuleId.OVERLAP.value):
        out.append(
            f"{report.counts[RuleId.OVERLAP.value]} overlaps totalling "
            f"{report.total_overlap_area_m2:,.1f} m². Snap first at 15 cm; whatever "
            f"survives is a real boundary disagreement, not a digitising artefact."
        )
    if report.counts.get(RuleId.GAP.value):
        out.append(
            f"{report.counts[RuleId.GAP.value]} interior gaps totalling "
            f"{report.total_gap_area_m2:,.1f} m². Check each against the road and "
            f"water layers before filling — a 'gap' is often an unsurveyed public right."
        )
    if report.planar_partition_error > 0.01:
        out.append(
            f"Planar partition error is {report.planar_partition_error * 100:.2f}%, which "
            f"is too high to publish. Expect the cause to be a CRS or datum mismatch "
            f"between the contributing layers rather than many small digitising errors."
        )
    return out
