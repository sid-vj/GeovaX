"""Footprint regularisation and AI-extraction post-processing.

Machine-extracted building footprints are geometrically *wrong* in a specific, systematic
way: they are wobbly. A segmentation network traces the roof edge pixel by pixel, and
polygonising that trace produces a 60-vertex blob where the building has four corners. That
matters for a cadastre in three concrete ways:

1. Wobbly polygons store 15x more vertices than the building has corners, which is a real
   cost at 250,000 buildings.
2. They never share an edge with their neighbour, so a terrace of houses becomes a mass of
   slivers when overlaid.
3. Their area is biased — a jagged boundary systematically over- or under-states the
   footprint, and footprint area is a tax base.

Regularisation fixes this by enforcing the prior that buildings are made of straight lines
meeting at right angles. The pipeline is:

    simplify (Douglas-Peucker) -> dominant orientation (rotating calipers over the hull)
    -> snap edge bearings to the orientation grid -> re-intersect edges -> validate

The orientation grid is the key idea: rather than forcing every edge to 0/90 degrees in map
space, the algorithm finds each building's own dominant axis and squares the building to
*that*. Buildings on a curving Chennai street are all rectangular and none of them are
axis-aligned to north.

Regularisation is refused, rather than forced, when a building genuinely is not
rectilinear — a temple gopuram, a circular tank, a curved apartment block. The
``rectilinearity`` measure decides, and buildings below the threshold pass through
simplified but unsquared.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from shapely.affinity import rotate
from shapely.geometry import LinearRing, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


@dataclass
class RegulariseConfig:
    simplify_tolerance_m: float = 0.30
    angle_snap_deg: float = 12.0
    """Edges within this of the orientation grid are snapped to it."""
    min_edge_length_m: float = 0.60
    rectilinearity_threshold: float = 0.55
    """Below this the shape is not treated as rectilinear and is left unsquared."""
    max_area_change_pct: float = 12.0
    """Regularisation that would change the footprint area by more than this is rejected;
    the area is a tax base and must not be quietly altered to make a shape look neat."""
    allowed_angles_deg: tuple[float, ...] = (0.0, 45.0, 90.0, 135.0)


@dataclass
class RegulariseReport:
    n_input: int = 0
    n_regularised: int = 0
    n_refused_non_rectilinear: int = 0
    n_refused_area_change: int = 0
    vertices_before: int = 0
    vertices_after: int = 0
    mean_area_change_pct: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def vertex_reduction(self) -> float:
        return 1.0 - (self.vertices_after / self.vertices_before) if self.vertices_before else 0.0

    def summary(self) -> str:
        return (
            f"{self.n_regularised:,} of {self.n_input:,} footprints squared "
            f"({self.n_refused_non_rectilinear:,} genuinely non-rectilinear, "
            f"{self.n_refused_area_change:,} refused because squaring would have moved the "
            f"area too far); vertices {self.vertices_before:,} -> {self.vertices_after:,} "
            f"({self.vertex_reduction * 100:.1f}% fewer); mean area change "
            f"{self.mean_area_change_pct:+.2f}%"
        )


def rectilinearity(poly: Polygon) -> float:
    """Fraction of perimeter lying on edges aligned to the dominant orientation grid.

    1.0 for a perfect rectangle or L; near 0 for a circle. This is what distinguishes a
    building that *should* be squared from one that should be left alone.
    """
    ring = poly.exterior
    coords = np.asarray(ring.coords)[:, :2]
    if len(coords) < 4:
        return 0.0
    seg = np.diff(coords, axis=0)
    lengths = np.hypot(seg[:, 0], seg[:, 1])
    total = lengths.sum()
    if total <= 0:
        return 0.0
    ang = np.degrees(np.arctan2(seg[:, 1], seg[:, 0])) % 180.0
    base = dominant_orientation(poly)
    dev = np.minimum.reduce([np.abs(((ang - base - a) % 180.0 + 90) % 180.0 - 90)
                             for a in (0.0, 90.0)])
    aligned = lengths[dev <= 12.0].sum()
    return float(aligned / total)


def dominant_orientation(poly: Polygon) -> float:
    """The building's own principal axis, in degrees, by minimum-area rectangle.

    Weighted by edge length so that a long facade determines the axis rather than a short
    porch, which is what a human would do.
    """
    try:
        rect = poly.minimum_rotated_rectangle
        coords = np.asarray(rect.exterior.coords)[:-1, :2]
        edges = np.diff(np.vstack([coords, coords[:1]]), axis=0)
        lens = np.hypot(edges[:, 0], edges[:, 1])
        i = int(np.argmax(lens))
        return float(math.degrees(math.atan2(edges[i, 1], edges[i, 0])) % 90.0)
    except Exception:  # noqa: BLE001
        return 0.0


def regularise_polygon(poly: Polygon, config: RegulariseConfig | None = None
                       ) -> tuple[Polygon, str]:
    """Square one footprint. Returns ``(geometry, outcome)``."""
    cfg = config or RegulariseConfig()
    if poly.is_empty or not poly.is_valid:
        poly = poly.buffer(0)
        if poly.is_empty or not isinstance(poly, Polygon):
            return poly, "invalid"

    simplified = poly.simplify(cfg.simplify_tolerance_m, preserve_topology=True)
    if not isinstance(simplified, Polygon) or simplified.is_empty:
        return poly, "invalid"

    rect = rectilinearity(simplified)
    if rect < cfg.rectilinearity_threshold:
        return simplified, "non_rectilinear"

    base = dominant_orientation(simplified)
    rotated = rotate(simplified, -base, origin="centroid", use_radians=False)
    squared_ring = _square_ring(np.asarray(rotated.exterior.coords)[:, :2], cfg)
    if squared_ring is None or len(squared_ring) < 4:
        return simplified, "failed"

    interiors = []
    for r in rotated.interiors:
        ir = _square_ring(np.asarray(r.coords)[:, :2], cfg)
        if ir is not None and len(ir) >= 4:
            interiors.append(ir)

    try:
        candidate = Polygon(squared_ring, interiors)
        candidate = rotate(candidate, base, origin=simplified.centroid, use_radians=False)
    except Exception:  # noqa: BLE001
        return simplified, "failed"

    if not candidate.is_valid:
        candidate = candidate.buffer(0)
    if candidate.is_empty or not isinstance(candidate, Polygon):
        return simplified, "failed"

    change = abs(candidate.area - poly.area) / max(poly.area, 1e-9) * 100.0
    if change > cfg.max_area_change_pct:
        return simplified, "area_change_refused"
    return candidate, "regularised"


def _square_ring(coords: np.ndarray, cfg: RegulariseConfig) -> list[tuple[float, float]] | None:
    """Snap edge bearings to the orientation grid and re-intersect consecutive edges."""
    pts = coords[:-1] if np.allclose(coords[0], coords[-1]) else coords
    n = len(pts)
    if n < 4:
        return None

    lines: list[tuple[float, float, float]] = []  # a*x + b*y = c
    for i in range(n):
        p0 = pts[i]
        p1 = pts[(i + 1) % n]
        d = p1 - p0
        length = math.hypot(d[0], d[1])
        if length < cfg.min_edge_length_m:
            continue
        ang = math.degrees(math.atan2(d[1], d[0])) % 180.0
        snapped = min(cfg.allowed_angles_deg,
                      key=lambda a: min(abs(ang - a), 180.0 - abs(ang - a)))
        if min(abs(ang - snapped), 180.0 - abs(ang - snapped)) > cfg.angle_snap_deg:
            snapped = ang  # too far from the grid; keep the observed bearing
        theta = math.radians(snapped)
        a, b = -math.sin(theta), math.cos(theta)
        mid = (p0 + p1) / 2.0
        lines.append((a, b, a * mid[0] + b * mid[1]))

    if len(lines) < 3:
        return None

    out: list[tuple[float, float]] = []
    m = len(lines)
    for i in range(m):
        a1, b1, c1 = lines[i]
        a2, b2, c2 = lines[(i + 1) % m]
        det = a1 * b2 - a2 * b1
        if abs(det) < 1e-9:
            continue  # parallel consecutive edges: drop the vertex
        x = (c1 * b2 - c2 * b1) / det
        y = (a1 * c2 - a2 * c1) / det
        out.append((float(x), float(y)))
    if len(out) < 3:
        return None
    out.append(out[0])
    try:
        if not LinearRing(out).is_valid:
            return None
    except Exception:  # noqa: BLE001
        return None
    return out


def regularise(features: dict[str, BaseGeometry], config: RegulariseConfig | None = None
               ) -> tuple[dict[str, BaseGeometry], RegulariseReport]:
    cfg = config or RegulariseConfig()
    rep = RegulariseReport(n_input=len(features))
    out: dict[str, BaseGeometry] = {}
    changes: list[float] = []

    for fid, g in features.items():
        rep.vertices_before += _count_vertices(g)
        if isinstance(g, MultiPolygon):
            parts, outcomes = [], []
            for p in g.geoms:
                q, o = regularise_polygon(p, cfg)
                parts.append(q)
                outcomes.append(o)
            new = MultiPolygon([p for p in parts if isinstance(p, Polygon) and not p.is_empty])
            outcome = "regularised" if "regularised" in outcomes else outcomes[0] if outcomes else "failed"
        elif isinstance(g, Polygon):
            new, outcome = regularise_polygon(g, cfg)
        else:
            new, outcome = g, "not_polygon"

        if outcome == "regularised":
            rep.n_regularised += 1
            if g.area > 0:
                changes.append((new.area - g.area) / g.area * 100.0)
        elif outcome == "non_rectilinear":
            rep.n_refused_non_rectilinear += 1
        elif outcome == "area_change_refused":
            rep.n_refused_area_change += 1

        out[fid] = new
        rep.vertices_after += _count_vertices(new)

    rep.mean_area_change_pct = float(np.mean(changes)) if changes else 0.0
    if abs(rep.mean_area_change_pct) > 1.0:
        rep.notes.append(
            f"regularisation changed footprint area by {rep.mean_area_change_pct:+.2f}% on "
            f"average. A systematic bias here directly biases any tax or coverage "
            f"calculation built on these footprints and should be investigated before use."
        )
    return out, rep


# --------------------------------------------------------------------------------------
# extraction quality control
# --------------------------------------------------------------------------------------


@dataclass
class ExtractionQC:
    """Filters applied to raw AI extraction output before it enters harmonisation.

    An extraction model emits everything it thinks is a building, including a great deal
    that is not: shadows, water tanks, tarpaulins, and the model's own hallucinations at
    low confidence. Letting those into the cadastral fabric is how an automated system
    destroys trust on its first run.
    """

    min_confidence: float = 0.65
    min_area_m2: float = 8.0
    max_area_m2: float = 60000.0
    min_compactness: float = 0.16
    max_vertices: int = 400
    min_height_m: float | None = None

    def filter(self, features: dict[str, BaseGeometry],
               attributes: dict[str, dict[str, Any]] | None = None
               ) -> tuple[dict[str, BaseGeometry], dict[str, int]]:
        attributes = attributes or {}
        kept: dict[str, BaseGeometry] = {}
        reasons: dict[str, int] = {}

        def drop(why: str) -> None:
            reasons[why] = reasons.get(why, 0) + 1

        for fid, g in features.items():
            attrs = attributes.get(fid, {})
            conf = attrs.get("confidence")
            if conf is not None and float(conf) < self.min_confidence:
                drop("low_model_confidence")
                continue
            if g is None or g.is_empty:
                drop("empty")
                continue
            a = float(g.area)
            if a < self.min_area_m2:
                drop("below_minimum_area")
                continue
            if a > self.max_area_m2:
                drop("implausibly_large")
                continue
            p = float(g.length)
            if p > 0 and (4 * math.pi * a / (p * p)) < self.min_compactness:
                drop("too_thin_to_be_a_building")
                continue
            if _count_vertices(g) > self.max_vertices:
                drop("excessive_vertices")
                continue
            if self.min_height_m is not None:
                h = attrs.get("height_m")
                if h is not None and float(h) < self.min_height_m:
                    drop("below_minimum_height")
                    continue
            kept[fid] = g
        return kept, reasons


def _count_vertices(geom: BaseGeometry) -> int:
    if geom is None or geom.is_empty:
        return 0
    if isinstance(geom, Polygon):
        return len(geom.exterior.coords) + sum(len(r.coords) for r in geom.interiors)
    if hasattr(geom, "geoms"):
        return sum(_count_vertices(g) for g in geom.geoms)
    try:
        return len(geom.coords)
    except Exception:  # noqa: BLE001
        return 0
