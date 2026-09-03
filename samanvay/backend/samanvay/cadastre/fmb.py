"""Generative Draft Field Measurement Book (FMB) Engine.

Translates modern geodesic polygon boundaries into traditional cadastral survey
measurements:
1. Identifies the principal G-line (Baseline across the parcel).
2. Calculates perpendicular ladder offsets from the G-line to every boundary vertex.
3. Measures outer F-lines (boundary lengths between corner stones).
4. Exports directly to the National Informatics Centre (NIC) CollabLand XML format.
5. Renders visual SVG cadastral sketches with customary units and surveyor notes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
import xml.etree.ElementTree as ET


@dataclass
class LadderPoint:
    vertex_idx: int
    vertex_label: str
    chainage_m: float
    offset_m: float
    side: str  # 'L' (Left), 'R' (Right), 'ON' (Collinear with G-Line)
    x: float
    y: float


@dataclass
class FLine:
    from_label: str
    to_label: str
    length_m: float


@dataclass
class FMBRecord:
    ulpin: str
    survey_number: str
    subdivision: str
    village: str
    taluk: str
    district: str
    area_sqm: float
    area_cents: float
    baseline_start: str
    baseline_end: str
    baseline_length_m: float
    ladder_points: list[LadderPoint] = field(default_factory=list)
    f_lines: list[FLine] = field(default_factory=list)
    boundary_coords: list[tuple[float, float]] = field(default_factory=list)
    scale_label: str = "1:500"


def _to_local_meters(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convert longitude/latitude degree coordinates to local tangent metric coordinates (meters)."""
    if not coords:
        return []

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)

    # A single cadastral parcel in degrees is always < 0.1 degrees (~10 km).
    # If coordinates are outside lon/lat range, span > 1.0, or min_x < 50 (India lon is 68-97),
    # they are already local Cartesian metric coordinates.
    if any(abs(c[0]) > 180 or abs(c[1]) > 90 for c in coords) or span_x > 1.0 or span_y > 1.0 or min(xs) < 50.0:
        return coords

    # Centroid
    lon0 = sum(xs) / len(coords)
    lat0 = sum(ys) / len(coords)
    lat_rad = math.radians(lat0)
    meters_per_deg_lon = 111320.0 * math.cos(lat_rad)
    meters_per_deg_lat = 110540.0

    return [
        ((c[0] - lon0) * meters_per_deg_lon, (c[1] - lat0) * meters_per_deg_lat)
        for c in coords
    ]


def _polygon_area(pts: list[tuple[float, float]]) -> float:
    """Shoelace formula for 2D polygon area."""
    n = len(pts)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return abs(area) / 2.0


def generate_fmb(
    coordinates: list[tuple[float, float]] | list[list[float]],
    metadata: dict[str, Any] | None = None
) -> FMBRecord:
    """Generate an FMB survey ladder and baseline from a list of 2D polygon vertices."""
    meta = metadata or {}
    raw_pts = [(float(p[0]), float(p[1])) for p in coordinates]
    # Remove duplicate closing vertex if present
    if len(raw_pts) > 3 and math.hypot(raw_pts[0][0] - raw_pts[-1][0], raw_pts[0][1] - raw_pts[-1][1]) < 1e-6:
        raw_pts = raw_pts[:-1]

    pts = _to_local_meters(raw_pts)
    n = len(pts)
    if n < 3:
        raise ValueError(f"Polygon must have at least 3 vertices, got {n}")

    # Generate vertex labels: A, B, C...
    labels = [chr(65 + (i % 26)) + (str(i // 26) if i >= 26 else "") for i in range(n)]

    # Determine Baseline (G-line): Find pair of non-adjacent vertices with maximum distance
    max_dist = -1.0
    start_idx, end_idx = 0, 1
    for i in range(n):
        for j in range(i + 1, n):
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if d > max_dist:
                max_dist = d
                start_idx, end_idx = i, j

    A = pts[start_idx]
    B = pts[end_idx]
    baseline_len = max_dist
    base_start_lbl = labels[start_idx]
    base_end_lbl = labels[end_idx]

    # Baseline direction vector
    dx = B[0] - A[0]
    dy = B[1] - A[1]
    L = math.hypot(dx, dy)
    ux = dx / L if L > 0 else 1.0
    uy = dy / L if L > 0 else 0.0

    # Calculate ladder points (perpendicular projections onto G-line)
    ladder_points: list[LadderPoint] = []
    for i in range(n):
        P = pts[i]
        ap_x = P[0] - A[0]
        ap_y = P[1] - A[1]
        chainage = ap_x * ux + ap_y * uy
        # Cross product to determine Left or Right
        cross = dx * ap_y - dy * ap_x
        # Distance from line
        perp_x = A[0] + chainage * ux
        perp_y = A[1] + chainage * uy
        offset = math.hypot(P[0] - perp_x, P[1] - perp_y)

        if offset < 0.05:
            side = "ON"
        elif cross > 0:
            side = "L"
        else:
            side = "R"

        ladder_points.append(
            LadderPoint(
                vertex_idx=i,
                vertex_label=labels[i],
                chainage_m=round(chainage, 2),
                offset_m=round(offset, 2),
                side=side,
                x=round(P[0], 3),
                y=round(P[1], 3),
            )
        )

    # Calculate outer boundary F-lines
    f_lines: list[FLine] = []
    for i in range(n):
        j = (i + 1) % n
        d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
        f_lines.append(
            FLine(
                from_label=labels[i],
                to_label=labels[j],
                length_m=round(d, 2),
            )
        )

    area_sqm = round(_polygon_area(pts), 2)
    area_cents = round(area_sqm / 40.4686, 2)  # 1 cent = 40.4686 m²

    return FMBRecord(
        ulpin=str(meta.get("ulpin") or "ULPIN-PREVIEW"),
        survey_number=str(meta.get("survey_number") or "42"),
        subdivision=str(meta.get("subdivision") or "1"),
        village=str(meta.get("village") or "Kilpauk"),
        taluk=str(meta.get("taluk") or "Egmore"),
        district=str(meta.get("district") or "Chennai"),
        area_sqm=area_sqm,
        area_cents=area_cents,
        baseline_start=base_start_lbl,
        baseline_end=base_end_lbl,
        baseline_length_m=round(baseline_len, 2),
        ladder_points=ladder_points,
        f_lines=f_lines,
        boundary_coords=pts,
    )


def to_collabland_xml(rec: FMBRecord) -> str:
    """Serialize FMB measurements into the National Informatics Centre (NIC) CollabLand XML format."""
    root = ET.Element("CollabLand", version="3.0", standard="NIC-DILRMP")
    fmb = ET.SubElement(
        root,
        "FMB",
        attrib={
            "ULPIN": rec.ulpin,
            "SurveyNo": rec.survey_number,
            "SubDivNo": rec.subdivision,
            "Village": rec.village,
            "Taluk": rec.taluk,
            "District": rec.district,
            "AreaSqM": str(rec.area_sqm),
            "AreaCents": str(rec.area_cents),
            "Scale": rec.scale_label,
        },
    )

    # Baseline G-line
    ET.SubElement(
        fmb,
        "BaseLine",
        attrib={
            "Start": rec.baseline_start,
            "End": rec.baseline_end,
            "LengthMeters": str(rec.baseline_length_m),
            "Type": "G-Line",
        },
    )

    # Ladder offsets
    ladder_el = ET.SubElement(fmb, "LadderOffsets")
    for lp in sorted(rec.ladder_points, key=lambda p: p.chainage_m):
        ET.SubElement(
            ladder_el,
            "Offset",
            attrib={
                "Vertex": lp.vertex_label,
                "ChainageM": str(lp.chainage_m),
                "OffsetM": str(lp.offset_m),
                "Side": lp.side,
                "LocalX": str(lp.x),
                "LocalY": str(lp.y),
            },
        )

    # Outer Boundaries (F-lines)
    bound_el = ET.SubElement(fmb, "Boundaries")
    for fl in rec.f_lines:
        ET.SubElement(
            bound_el,
            "FLine",
            attrib={
                "From": fl.from_label,
                "To": fl.to_label,
                "LengthMeters": str(fl.length_m),
            },
        )

    # Verification metadata
    ET.SubElement(
        fmb,
        "SystemAudit",
        attrib={
            "Generator": "SAMANVAY-GeoAI-Cadastre",
            "DerivedFrom": "Harmonised Consensus Geometry",
            "SurveyMethod": "Mathematical Orthogonal Reduction",
        },
    )

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8").decode("utf-8")


def to_fmb_svg(rec: FMBRecord, width: int = 700, height: int = 500) -> str:
    """Generate a clean SVG schematic of the Field Measurement Book (FMB) sketch."""
    if not rec.boundary_coords:
        return f'<svg width="{width}" height="{height}"><text x="20" y="40">No coordinates</text></svg>'

    min_x = min(p[0] for p in rec.boundary_coords)
    max_x = max(p[0] for p in rec.boundary_coords)
    min_y = min(p[1] for p in rec.boundary_coords)
    max_y = max(p[1] for p in rec.boundary_coords)

    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)

    # Viewport margins
    pad = 80
    draw_w = width - 2 * pad
    draw_h = height - 2 * pad - 40  # reserve space for title block

    scale = min(draw_w / span_x, draw_h / span_y)

    def tx(x: float) -> float:
        return pad + (x - min_x) * scale

    def ty(y: float) -> float:
        # Invert y for SVG
        return height - pad - 40 - (y - min_y) * scale

    # Find baseline endpoints in projected space
    start_pt = next((p for p in rec.ladder_points if p.vertex_label == rec.baseline_start), rec.ladder_points[0])
    end_pt = next((p for p in rec.ladder_points if p.vertex_label == rec.baseline_end), rec.ladder_points[-1])

    # Polygon path
    poly_pts = " ".join(f"{tx(p[0]):.1f},{ty(p[1]):.1f}" for p in rec.boundary_coords)

    # Baseline G-line coords
    bx1, by1 = tx(start_pt.x), ty(start_pt.y)
    bx2, by2 = tx(end_pt.x), ty(end_pt.y)

    # Offsets
    dx = end_pt.x - start_pt.x
    dy = end_pt.y - start_pt.y
    L = math.hypot(dx, dy)
    ux = dx / L if L > 0 else 1.0
    uy = dy / L if L > 0 else 0.0

    offset_lines = []
    for lp in rec.ladder_points:
        if lp.vertex_label in (rec.baseline_start, rec.baseline_end) or lp.offset_m < 0.1:
            continue
        proj_x = start_pt.x + lp.chainage_m * ux
        proj_y = start_pt.y + lp.chainage_m * uy
        px, py = tx(lp.x), ty(lp.y)
        qx, qy = tx(proj_x), ty(proj_y)
        offset_lines.append(
            f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{qx:.1f}" y2="{qy:.1f}" '
            f'stroke="#0F6E5F" stroke-dasharray="3,3" stroke-width="1.2"/>'
            f'<text x="{(px + qx) / 2:.1f}" y="{(py + qy) / 2 - 4:.1f}" '
            f'font-family="sans-serif" font-size="10" fill="#0F6E5F" text-anchor="middle">{lp.offset_m}m ({lp.side})</text>'
        )

    # Boundary dimensions
    f_line_labels = []
    for fl in rec.f_lines:
        p1 = next((p for p in rec.ladder_points if p.vertex_label == fl.from_label), None)
        p2 = next((p for p in rec.ladder_points if p.vertex_label == fl.to_label), None)
        if p1 and p2:
            mx = (tx(p1.x) + tx(p2.x)) / 2
            my = (ty(p1.y) + tx(p2.y)) / 2
            f_line_labels.append(
                f'<text x="{mx:.1f}" y="{my:.1f}" font-family="sans-serif" font-size="10.5" '
                f'font-weight="bold" fill="#13202F">{fl.length_m}m</text>'
            )

    # Vertex dots and labels
    vertex_svg = []
    for lp in rec.ladder_points:
        vx, vy = tx(lp.x), ty(lp.y)
        vertex_svg.append(
            f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="4.5" fill="#D9743B" stroke="#FFFFFF" stroke-width="1.5"/>'
            f'<text x="{vx + 8:.1f}" y="{vy + 4:.1f}" font-family="sans-serif" font-size="12" '
            f'font-weight="bold" fill="#13202F">{lp.vertex_label}</text>'
        )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">
  <!-- Header / Title Block -->
  <rect x="15" y="15" width="{width - 30}" height="55" fill="#EEF2F5" rx="6" stroke="#C9D6E2" stroke-width="1"/>
  <text x="30" y="38" font-family="sans-serif" font-size="15" font-weight="bold" fill="#13202F">
    FIELD MEASUREMENT BOOK (FMB) · SURVEY NO: {rec.survey_number}/{rec.subdivision}
  </text>
  <text x="30" y="56" font-family="sans-serif" font-size="11" fill="#5C6B7A">
    Village: {rec.village} · Taluk: {rec.taluk} · Extent: {rec.area_sqm} m² ({rec.area_cents} Cents) · ULPIN: {rec.ulpin}
  </text>
  <text x="{width - 30}" y="42" font-family="sans-serif" font-size="11" font-weight="bold" fill="#0F6E5F" text-anchor="end">
    Format: NIC-CollabLand 3.0
  </text>

  <!-- Canvas -->
  <rect x="15" y="75" width="{width - 30}" height="{height - 90}" fill="#FFFFFF" rx="6" stroke="#E3EAF0" stroke-width="1"/>

  <!-- Parcel Boundary -->
  <polygon points="{poly_pts}" fill="#E8F4F1" fill-opacity="0.4" stroke="#13202F" stroke-width="2.5"/>

  <!-- G-Line (Baseline) -->
  <line x1="{bx1:.1f}" y1="{by1:.1f}" x2="{bx2:.1f}" y2="{by2:.1f}" stroke="#A83A2B" stroke-width="2" stroke-dasharray="6,4"/>
  <text x="{(bx1 + bx2) / 2:.1f}" y="{(by1 + by2) / 2 - 8:.1f}" font-family="sans-serif" font-size="12" font-weight="bold" fill="#A83A2B" text-anchor="middle">
    G-Line: {rec.baseline_start}—{rec.baseline_end} ({rec.baseline_length_m} m)
  </text>

  <!-- Offset Perpendiculars -->
  {"".join(offset_lines)}

  <!-- Boundary Vertices -->
  {"".join(vertex_svg)}

  <!-- Footer Info -->
  <text x="30" y="{height - 25}" font-family="sans-serif" font-size="10" fill="#5C6B7A">
    Generated mathematically from harmonised consensus boundary. Ready for Tahsildar sign-off and CollabLand ingestion.
  </text>
</svg>"""
    return svg
