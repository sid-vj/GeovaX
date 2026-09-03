"""LOD1 three-dimensional city model generation.

A harmonised footprint with a measured height is, geometrically, a prism. Extruding it
produces a CityGML **LOD1** solid — the level of detail at which every building is a
flat-roofed block of its true footprint and true height. LOD1 is unglamorous and it is
exactly what urban land administration needs:

* **Floor space index** — the ratio of built floor area to plot area is the central control
  in every Indian development-control regulation, and it needs footprint × floors, not a
  pretty roof.
* **Property tax** — assessment in most Indian ULBs is on built-up area by floor.
* **Solar and daylight** — rooftop solar potential and right-to-light assessments run on
  LOD1 massing.
* **Set-back and coverage compliance** — a plan-view check plus a height check.

Nothing here is invented. The footprint comes from the harmonised fabric, the height from a
measured source (nDSM, or a per-instance height attribute where the extraction provides
one), and where the height is missing the building is emitted with a null height and marked
so, rather than being given a guessed one. A 3-D model that quietly invents heights is worse
than a 2-D map that admits it does not know them.

Output is **CityJSON 1.1**, the OGC-community encoding of CityGML, so the result opens in
QGIS, FME, Blender via the CityJSON plugins, and azul, without conversion.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

#: Storey height assumed when converting a measured height into a floor count, in metres.
#: 3.0 m is the figure the National Building Code of India uses for residential floor-to-
#: floor height and it is what ULB assessment tables assume.
DEFAULT_STOREY_HEIGHT_M = 3.0


@dataclass
class Lod1Config:
    storey_height_m: float = DEFAULT_STOREY_HEIGHT_M
    min_height_m: float = 2.2
    max_height_m: float = 300.0
    default_ground_z: float = 0.0
    simplify_m: float = 0.25
    emit_null_height: bool = True
    """Emit buildings with no measured height as zero-extrusion footprints flagged
    ``height_source: none``, rather than dropping them or inventing a height."""


@dataclass
class Lod1Report:
    buildings: int = 0
    extruded: int = 0
    no_height: int = 0
    rejected: int = 0
    total_footprint_m2: float = 0.0
    total_floor_area_m2: float = 0.0
    mean_height_m: float = 0.0
    max_height_m: float = 0.0
    floor_histogram: dict[int, int] = field(default_factory=dict)
    height_sources: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def mean_floors(self) -> float:
        total = sum(k * v for k, v in self.floor_histogram.items())
        n = sum(self.floor_histogram.values())
        return total / n if n else 0.0

    def summary(self) -> str:
        return (
            f"{self.extruded:,} of {self.buildings:,} buildings extruded to LOD1 solids "
            f"({self.no_height:,} carry no measured height and are emitted flat and "
            f"flagged); total footprint {self.total_footprint_m2:,.0f} m², estimated gross "
            f"floor area {self.total_floor_area_m2:,.0f} m² at a mean of "
            f"{self.mean_floors:.2f} storeys; tallest {self.max_height_m:.1f} m"
        )


@dataclass
class Lod1Building:
    building_id: str
    footprint: BaseGeometry
    height_m: float | None
    ground_z: float
    height_source: str
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def floors(self) -> int | None:
        if self.height_m is None:
            return None
        return max(1, int(round(self.height_m / DEFAULT_STOREY_HEIGHT_M)))

    @property
    def footprint_area_m2(self) -> float:
        return float(self.footprint.area)

    @property
    def gross_floor_area_m2(self) -> float:
        f = self.floors
        return self.footprint_area_m2 * f if f else 0.0


def build(features: dict[str, BaseGeometry],
          heights: dict[str, float] | None = None,
          attributes: dict[str, dict[str, Any]] | None = None,
          ground: dict[str, float] | None = None,
          config: Lod1Config | None = None) -> tuple[list[Lod1Building], Lod1Report]:
    """Turn harmonised footprints plus measured heights into LOD1 buildings."""
    cfg = config or Lod1Config()
    heights = heights or {}
    attributes = attributes or {}
    ground = ground or {}
    rep = Lod1Report(buildings=len(features))
    out: list[Lod1Building] = []

    for fid, geom in features.items():
        if geom is None or geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
            rep.rejected += 1
            continue
        g = geom.simplify(cfg.simplify_m, preserve_topology=True) if cfg.simplify_m else geom

        attrs = attributes.get(fid, {})
        h, source = _resolve_height(fid, heights, attrs, cfg)
        rep.height_sources[source] = rep.height_sources.get(source, 0) + 1

        if h is None:
            rep.no_height += 1
            if not cfg.emit_null_height:
                continue
        else:
            rep.extruded += 1
            rep.max_height_m = max(rep.max_height_m, h)

        b = Lod1Building(
            building_id=fid,
            footprint=g,
            height_m=h,
            ground_z=float(ground.get(fid, cfg.default_ground_z)),
            height_source=source,
            attributes=attrs,
        )
        out.append(b)
        rep.total_footprint_m2 += b.footprint_area_m2
        rep.total_floor_area_m2 += b.gross_floor_area_m2
        f = b.floors
        if f:
            rep.floor_histogram[f] = rep.floor_histogram.get(f, 0) + 1

    hs = [b.height_m for b in out if b.height_m is not None]
    rep.mean_height_m = float(sum(hs) / len(hs)) if hs else 0.0
    if rep.no_height:
        rep.notes.append(
            f"{rep.no_height:,} buildings have no measured height from any source. They are "
            f"emitted as flat footprints flagged 'height_source: none' rather than being "
            f"given an assumed height — a 3-D model that invents heights is worse than a "
            f"2-D map that admits it does not know them."
        )
    return out, rep


def _resolve_height(fid: str, heights: dict[str, float], attrs: dict[str, Any],
                    cfg: Lod1Config) -> tuple[float | None, str]:
    """Height, and where it came from. Measurement beats record; record beats nothing."""
    v = heights.get(fid)
    if v is not None and _plausible(v, cfg):
        return float(v), "measured_ndsm"
    for key, label in (("max_height_m", "measured_attribute"),
                       ("height_m", "measured_attribute"),
                       ("height", "measured_attribute")):
        v = attrs.get(key)
        if v not in (None, "") and _plausible(v, cfg):
            return float(v), label
    fl = attrs.get("floors") or attrs.get("no_floors") or attrs.get("estimated_floors")
    if fl not in (None, ""):
        n = _parse_floors(fl)
        if n:
            return n * cfg.storey_height_m, "derived_from_recorded_floors"
    return None, "none"


def _plausible(v: Any, cfg: Lod1Config) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return cfg.min_height_m <= f <= cfg.max_height_m


def _parse_floors(v: Any) -> int | None:
    """Parse the floor notations Indian municipal records actually use: 3, G+2, B+G+5."""
    s = str(v).strip().upper().replace(" ", "")
    if not s:
        return None
    if s.isdigit():
        return int(s) or None
    total = 0
    for part in s.split("+"):
        if part in ("G", "GF"):
            total += 1
        elif part in ("B", "BF", "S"):     # basement, stilt — floor area, not height above ground
            continue
        elif part.isdigit():
            total += int(part)
        elif part.startswith("G") and part[1:].isdigit():
            total += 1 + int(part[1:])
    return total or None


# --------------------------------------------------------------------------------------
# CityJSON encoding
# --------------------------------------------------------------------------------------


def to_cityjson(buildings: Sequence[Lod1Building], *, crs_epsg: int = 4326,
                title: str = "SAMANVAY LOD1 city model") -> dict[str, Any]:
    """Encode as CityJSON 1.1 with a shared, quantised vertex list."""
    vertices: list[list[float]] = []
    index: dict[tuple[float, float, float], int] = {}

    def vid(x: float, y: float, z: float) -> int:
        key = (round(x, 4), round(y, 4), round(z, 3))
        if key not in index:
            index[key] = len(vertices)
            vertices.append([key[0], key[1], key[2]])
        return index[key]

    city_objects: dict[str, Any] = {}
    for b in buildings:
        polys = b.footprint.geoms if isinstance(b.footprint, MultiPolygon) else [b.footprint]
        boundaries: list[Any] = []
        for poly in polys:
            if not isinstance(poly, Polygon) or poly.is_empty:
                continue
            ring = list(poly.exterior.coords)
            if ring[0] == ring[-1]:
                ring = ring[:-1]
            if len(ring) < 3:
                continue
            z0 = b.ground_z
            z1 = b.ground_z + (b.height_m or 0.0)

            base = [vid(x, y, z0) for x, y in ring]
            top = [vid(x, y, z1) for x, y in ring]

            boundaries.append([list(reversed(base))])   # ground, downward-facing
            if b.height_m:
                boundaries.append([top])                # roof
                n = len(ring)
                for i in range(n):
                    j = (i + 1) % n
                    boundaries.append([[base[i], base[j], top[j], top[i]]])  # wall

        if not boundaries:
            continue
        city_objects[b.building_id] = {
            "type": "Building",
            "attributes": {
                "measuredHeight": b.height_m,
                "storeysAboveGround": b.floors,
                "heightSource": b.height_source,
                "footprintArea_m2": round(b.footprint_area_m2, 2),
                "grossFloorArea_m2": round(b.gross_floor_area_m2, 2),
                **{k: v for k, v in b.attributes.items()
                   if isinstance(v, (str, int, float, bool)) and not k.startswith("_")},
            },
            "geometry": [{
                "type": "Solid" if b.height_m else "MultiSurface",
                "lod": "1",
                "boundaries": [boundaries] if b.height_m else boundaries,
            }],
        }

    return {
        "type": "CityJSON",
        "version": "1.1",
        "metadata": {
            "title": title,
            "referenceSystem": f"https://www.opengis.net/def/crs/EPSG/0/{crs_epsg}",
            "geographicalExtent": _extent(vertices),
        },
        "transform": {"scale": [1.0, 1.0, 1.0], "translate": [0.0, 0.0, 0.0]},
        "CityObjects": city_objects,
        "vertices": vertices,
    }


def _extent(vertices: list[list[float]]) -> list[float]:
    if not vertices:
        return [0, 0, 0, 0, 0, 0]
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]


def write_cityjson(path: str, buildings: Sequence[Lod1Building], **kwargs: Any) -> str:
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_cityjson(buildings, **kwargs), fh)
    return path


# --------------------------------------------------------------------------------------
# development control
# --------------------------------------------------------------------------------------


@dataclass
class DevelopmentControlCheck:
    """Compare a parcel's built form against the rules that actually bind it."""

    parcel_id: str
    plot_area_m2: float
    built_up_area_m2: float
    gross_floor_area_m2: float
    max_height_m: float | None
    building_count: int

    @property
    def ground_coverage_pct(self) -> float:
        return 100.0 * self.built_up_area_m2 / self.plot_area_m2 if self.plot_area_m2 else 0.0

    @property
    def floor_space_index(self) -> float:
        return self.gross_floor_area_m2 / self.plot_area_m2 if self.plot_area_m2 else 0.0

    def assess(self, *, max_coverage_pct: float = 60.0, max_fsi: float = 1.5,
               max_height_m: float = 18.3) -> list[str]:
        """Findings, in the language of a development-control regulation.

        Defaults are the Tamil Nadu Combined Development and Building Rules 2019 limits for
        an ordinary residential plot. They are parameters, not truths: every ULB and every
        land-use zone has its own, and the values must come from the applicable rule set.
        Findings are stated as *requiring verification*, because the platform measures the
        building envelope and cannot see an approved plan or a sanctioned deviation.
        """
        out: list[str] = []
        if self.ground_coverage_pct > max_coverage_pct:
            out.append(
                f"ground coverage {self.ground_coverage_pct:.1f}% exceeds the "
                f"{max_coverage_pct:.0f}% limit — verify against the sanctioned plan"
            )
        if self.floor_space_index > max_fsi:
            out.append(
                f"floor space index {self.floor_space_index:.2f} exceeds the {max_fsi:.2f} "
                f"limit — verify the approved FSI, including any premium FSI purchased"
            )
        if self.max_height_m and self.max_height_m > max_height_m:
            out.append(
                f"measured height {self.max_height_m:.1f} m exceeds the {max_height_m:.1f} m "
                f"limit — verify fire-service clearance and approved height"
            )
        if self.ground_coverage_pct > 100.0:
            out.append(
                "built-up area exceeds the plot area entirely: either the parcel boundary "
                "or the footprints are wrong, and this is a data finding before it is a "
                "compliance finding"
            )
        return out


def assess_parcels(parcels: dict[str, dict[str, Any]],
                   buildings: Sequence[Lod1Building],
                   assignment: dict[str, str]) -> list[tuple[DevelopmentControlCheck, list[str]]]:
    """Roll LOD1 buildings up to their parcels and run the development-control checks."""
    by_parcel: dict[str, list[Lod1Building]] = {}
    for b in buildings:
        pid = assignment.get(b.building_id)
        if pid:
            by_parcel.setdefault(pid, []).append(b)

    out: list[tuple[DevelopmentControlCheck, list[str]]] = []
    for pid, bl in by_parcel.items():
        p = parcels.get(pid)
        if not p:
            continue
        area = float(p.get("computed_extent_m2") or 0.0)
        heights = [b.height_m for b in bl if b.height_m]
        chk = DevelopmentControlCheck(
            parcel_id=pid,
            plot_area_m2=area,
            built_up_area_m2=sum(b.footprint_area_m2 for b in bl),
            gross_floor_area_m2=sum(b.gross_floor_area_m2 for b in bl),
            max_height_m=max(heights) if heights else None,
            building_count=len(bl),
        )
        out.append((chk, chk.assess()))
    return out
