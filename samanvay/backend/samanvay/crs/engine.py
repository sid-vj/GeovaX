"""Geo-referencing and coordinate transformation engine.

India is one of the harder places in the world to get coordinate transformation right, and
getting it wrong is the single largest source of systematic error in legacy cadastral data.
The reasons are specific:

1. **Everest 1830 legacy.** Village maps, FMB sheets and older revenue records are on one
   of several Everest spheroid definitions (1830, 1937 Adjustment, 1956, 1962, 1975) with
   different semi-major axes, referenced to the Indian Datum with origin at Kalianpur.
   The datum shift to WGS 84 is 150–400 m depending on zone. A file silently read as WGS 84
   is therefore displaced by up to four hundred metres.

2. **Everest units.** The Everest 1830 spheroid was defined in Indian feet; a naive
   metre/foot assumption introduces a scale error of about 1 part in 10^5.

3. **Zone systems.** Legacy maps use Lambert Conformal Conic in Indian zones (I, IIA, IIB,
   IIIA, IIIB, IVA, IVB) and Everest-based UTM in some states, while NAKSHA outputs are in
   WGS 84 / UTM zones 42N–47N, and the national frame is now EPSG:7755
   (WGS 84 / India NSF LCC).

4. **Local distortion.** Even after a correct datum shift, a scanned FMB sheet has
   non-uniform distortion from paper shrinkage and scanning. No global transformation
   removes it. That requires local rubber-sheeting from ground control, which lives in
   ``samanvay.crs.gcp``.

This module handles 1–3 rigorously and hands 4 to the GCP module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

# --------------------------------------------------------------------------------------
# the Indian CRS catalogue
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CrsInfo:
    epsg: int | None
    code: str
    title: str
    kind: str            # "geographic" | "projected"
    datum: str
    area: str
    notes: str = ""
    proj4: str | None = None


INDIAN_CRS: dict[str, CrsInfo] = {
    # modern
    "EPSG:4326": CrsInfo(4326, "EPSG:4326", "WGS 84", "geographic", "WGS 84", "World",
                         "The interchange CRS for NAKSHA deliverables and all web services."),
    "EPSG:7755": CrsInfo(7755, "EPSG:7755", "WGS 84 / India NSF LCC", "projected", "WGS 84", "India",
                         "National Spatial Framework single-zone LCC; the target CRS for a "
                         "national seamless cadastral fabric."),
    "EPSG:32643": CrsInfo(32643, "EPSG:32643", "WGS 84 / UTM zone 43N", "projected", "WGS 84", "78E-84E"),
    "EPSG:32644": CrsInfo(32644, "EPSG:32644", "WGS 84 / UTM zone 44N", "projected", "WGS 84", "84E-90E"),
    "EPSG:32645": CrsInfo(32645, "EPSG:32645", "WGS 84 / UTM zone 45N", "projected", "WGS 84", "90E-96E"),
    "EPSG:32642": CrsInfo(32642, "EPSG:32642", "WGS 84 / UTM zone 42N", "projected", "WGS 84", "72E-78E"),
    # legacy geographic
    "EPSG:4240": CrsInfo(4240, "EPSG:4240", "Indian 1975", "geographic", "Everest 1830 (1975 Def)", "Thailand/India"),
    "EPSG:4145": CrsInfo(4145, "EPSG:4145", "Kalianpur 1937", "geographic", "Everest 1830 (1937 Adj)", "India/Pakistan"),
    "EPSG:4146": CrsInfo(4146, "EPSG:4146", "Kalianpur 1962", "geographic", "Everest 1830 (1962 Def)", "Pakistan"),
    "EPSG:4147": CrsInfo(4147, "EPSG:4147", "Kalianpur 1975", "geographic", "Everest 1830 (1975 Def)", "India"),
    # legacy projected — Indian zones
    "EPSG:24378": CrsInfo(24378, "EPSG:24378", "Kalianpur 1975 / India zone I", "projected",
                          "Everest 1830 (1975 Def)", "North India"),
    "EPSG:24379": CrsInfo(24379, "EPSG:24379", "Kalianpur 1975 / India zone IIa", "projected",
                          "Everest 1830 (1975 Def)", "Central India"),
    "EPSG:24380": CrsInfo(24380, "EPSG:24380", "Kalianpur 1975 / India zone IIb", "projected",
                          "Everest 1830 (1975 Def)", "East India"),
    "EPSG:24381": CrsInfo(24381, "EPSG:24381", "Kalianpur 1975 / India zone IIIa", "projected",
                          "Everest 1830 (1975 Def)", "South-central India"),
    "EPSG:24383": CrsInfo(24383, "EPSG:24383", "Kalianpur 1975 / India zone IVa", "projected",
                          "Everest 1830 (1975 Def)", "South India (incl. Tamil Nadu)"),
    # web
    "EPSG:3857": CrsInfo(3857, "EPSG:3857", "WGS 84 / Pseudo-Mercator", "projected", "WGS 84", "World",
                         "Tile delivery only. Never used for measurement — area error at 13°N "
                         "is about 5.4 percent."),
}

#: Datum shift parameters actually in use for Indian legacy data, as
#: (dx, dy, dz) in metres for the 3-parameter Molodensky-Badekas style shift, and the
#: full 7-parameter Helmert where published. Used only when a file carries no CRS at all
#: and the operator asserts a datum.
DATUM_SHIFTS_TO_WGS84: dict[str, tuple[float, ...]] = {
    # 3-parameter, Indian subcontinent mean solution
    "everest_1830_india": (295.0, 736.0, 257.0),
    "kalianpur_1937": (282.0, 726.0, 254.0),
    "kalianpur_1962": (283.0, 682.0, 231.0),
    "kalianpur_1975": (295.0, 736.0, 257.0),
    # 7-parameter (dx,dy,dz,rx",ry",rz",scale ppm) — Indian 1975 to WGS84, southern India
    "indian_1975_7p": (293.17, 726.18, 245.36, -0.393, -2.255, -0.752, 5.51),
}

#: Indian foot to metre. Everest's original geodetic foot, not the international foot.
INDIAN_FOOT_TO_M = 0.30479951
SURVEY_FOOT_TO_M = 12.0 / 39.37


class CrsEngine:
    """Cached, thread-safe coordinate transformations with accuracy bookkeeping."""

    def __init__(self, default_target: str = "EPSG:4326") -> None:
        self.default_target = default_target

    # -- resolution ---------------------------------------------------------------

    @staticmethod
    @lru_cache(maxsize=256)
    def resolve(code: str | int) -> CRS:
        """Accept EPSG codes, WKT, PROJ strings, or a friendly Indian zone name."""
        if isinstance(code, int):
            return CRS.from_epsg(code)
        s = str(code).strip()
        alias = _ZONE_ALIASES.get(s.lower())
        if alias:
            s = alias
        try:
            return CRS.from_user_input(s)
        except CRSError:
            if s.upper().startswith("EPSG:"):
                return CRS.from_epsg(int(s.split(":")[1]))
            raise

    @staticmethod
    @lru_cache(maxsize=512)
    def _transformer(src: str, dst: str, always_xy: bool = True) -> Transformer:
        return Transformer.from_crs(
            CrsEngine.resolve(src), CrsEngine.resolve(dst), always_xy=always_xy
        )

    # -- point transforms ---------------------------------------------------------

    def transform_point(self, x: float, y: float, src: str, dst: str | None = None,
                        z: float | None = None) -> tuple[float, ...]:
        dst = dst or self.default_target
        tr = self._transformer(src, dst)
        if z is None:
            return tr.transform(x, y)
        return tr.transform(x, y, z)

    def transform_many(self, xs: Sequence[float], ys: Sequence[float], src: str,
                       dst: str | None = None) -> tuple[list[float], list[float]]:
        dst = dst or self.default_target
        tr = self._transformer(src, dst)
        nx, ny = tr.transform(list(xs), list(ys))
        return list(nx), list(ny)

    # -- geometry transforms ------------------------------------------------------

    def transform_geometry(self, geom, src: str, dst: str | None = None):
        """Transform a shapely geometry. Imported lazily so the module stays light."""
        from shapely.ops import transform as shp_transform

        dst = dst or self.default_target
        if self.resolve(src) == self.resolve(dst):
            return geom
        tr = self._transformer(src, dst)
        return shp_transform(lambda xx, yy, zz=None: tr.transform(xx, yy), geom)

    # -- decisions ----------------------------------------------------------------

    @staticmethod
    def metric_crs_for(lon: float, lat: float) -> str:
        """The CRS in which lengths and areas should be computed for this location.

        Measurement is never done in EPSG:4326 (degrees are not metres) nor in EPSG:3857
        (Mercator area error at Chennai's latitude is 5.4%). For India the platform uses
        the appropriate WGS 84 / UTM north zone, which keeps scale error under 1/2500.
        """
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError(f"lon/lat out of range: {lon}, {lat}")
        zone = int((lon + 180.0) // 6.0) + 1
        return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"

    @staticmethod
    def is_projected(code: str) -> bool:
        return bool(CrsEngine.resolve(code).is_projected)

    @staticmethod
    def axis_unit(code: str) -> str:
        crs = CrsEngine.resolve(code)
        try:
            return crs.axis_info[0].unit_name
        except Exception:  # pragma: no cover - defensive
            return "unknown"

    # -- quality ------------------------------------------------------------------

    def round_trip_error(self, x: float, y: float, src: str, dst: str) -> float:
        """Metres of error introduced by src->dst->src. A sanity check on any pipeline.

        A round-trip error above a millimetre means the transformation is not invertible at
        this location, which almost always means a missing grid shift file.
        """
        fx, fy = self.transform_point(x, y, src, dst)[:2]
        bx, by = self.transform_point(fx, fy, dst, src)[:2]
        if self.is_projected(src):
            return math.hypot(bx - x, by - y)
        return haversine_m(x, y, bx, by)

    def transformation_accuracy(self, src: str, dst: str) -> float | None:
        """The accuracy EPSG publishes for the chosen transformation path, in metres."""
        try:
            op = self._transformer(src, dst)
            return op.target_crs and getattr(op, "accuracy", None)
        except Exception:  # pragma: no cover - defensive
            return None

    def describe(self, code: str) -> dict[str, object]:
        crs = self.resolve(code)
        info = INDIAN_CRS.get(str(code).upper())
        return {
            "input": str(code),
            "name": crs.name,
            "epsg": crs.to_epsg(),
            "projected": crs.is_projected,
            "unit": self.axis_unit(code),
            "datum": crs.datum.name if crs.datum else None,
            "india_note": info.notes if info else "",
            "area_of_use": str(crs.area_of_use) if crs.area_of_use else None,
        }


_ZONE_ALIASES = {
    "india zone i": "EPSG:24378",
    "india zone iia": "EPSG:24379",
    "india zone iib": "EPSG:24380",
    "india zone iiia": "EPSG:24381",
    "india zone iva": "EPSG:24383",
    "india nsf": "EPSG:7755",
    "nsf": "EPSG:7755",
    "wgs84": "EPSG:4326",
    "web mercator": "EPSG:3857",
}


# --------------------------------------------------------------------------------------
# geodesy helpers
# --------------------------------------------------------------------------------------

_WGS84_A = 6378137.0
_WGS84_F = 1.0 / 298.257223563


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres. Adequate below a few kilometres."""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def vincenty_m(lon1: float, lat1: float, lon2: float, lat2: float,
               max_iter: int = 200, tol: float = 1e-12) -> float:
    """Vincenty inverse on the WGS 84 ellipsoid — millimetre accuracy.

    Used where the answer is a legal quantity: parcel frontage, boundary length recorded on
    a patta. Falls back to haversine on the rare non-convergent antipodal case.
    """
    a, f = _WGS84_A, _WGS84_F
    b = (1 - f) * a
    L = math.radians(lon2 - lon1)
    U1 = math.atan((1 - f) * math.tan(math.radians(lat1)))
    U2 = math.atan((1 - f) * math.tan(math.radians(lat2)))
    sinU1, cosU1 = math.sin(U1), math.cos(U1)
    sinU2, cosU2 = math.sin(U2), math.cos(U2)
    lam = L
    for _ in range(max_iter):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.hypot(cosU2 * sin_lam, cosU1 * sinU2 - sinU1 * cosU2 * cos_lam)
        if sin_sigma == 0:
            return 0.0
        cos_sigma = sinU1 * sinU2 + cosU1 * cosU2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cosU1 * cosU2 * sin_lam / sin_sigma
        cos2_alpha = 1 - sin_alpha ** 2
        cos2_sigma_m = cos_sigma - 2 * sinU1 * sinU2 / cos2_alpha if cos2_alpha != 0 else 0.0
        C = f / 16 * cos2_alpha * (4 + f * (4 - 3 * cos2_alpha))
        lam_prev = lam
        lam = L + (1 - C) * f * sin_alpha * (
            sigma + C * sin_sigma * (cos2_sigma_m + C * cos_sigma * (-1 + 2 * cos2_sigma_m ** 2))
        )
        if abs(lam - lam_prev) < tol:
            break
    else:  # pragma: no cover - antipodal
        return haversine_m(lon1, lat1, lon2, lat2)
    u2 = cos2_alpha * (a ** 2 - b ** 2) / (b ** 2)
    A = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    d_sigma = B * sin_sigma * (
        cos2_sigma_m + B / 4 * (
            cos_sigma * (-1 + 2 * cos2_sigma_m ** 2)
            - B / 6 * cos2_sigma_m * (-3 + 4 * sin_sigma ** 2) * (-3 + 4 * cos2_sigma_m ** 2)
        )
    )
    return b * A * (sigma - d_sigma)


def _authalic_q(sin_phi: float, e: float) -> float:
    """Snyder's q function — the area element integrated from the equator."""
    es = e * sin_phi
    return (1 - e * e) * (
        sin_phi / (1 - es * es)
        - (1.0 / (2 * e)) * math.log((1 - es) / (1 + es))
    )


_E = math.sqrt(_WGS84_F * (2 - _WGS84_F))
_QP = _authalic_q(1.0, _E)
#: Radius of the authalic sphere — the sphere with the same surface area as WGS 84.
WGS84_AUTHALIC_R = _WGS84_A * math.sqrt(_QP / 2.0)


def geodesic_area_m2(ring: Iterable[tuple[float, float]]) -> float:
    """Area of a lon/lat ring on the WGS 84 **ellipsoid**, in square metres.

    Computed by mapping each vertex to its **authalic latitude** — the latitude on an
    equal-area sphere that subtends the same area from the equator as the true latitude
    does on the ellipsoid — and then applying the spherical-excess formula::

        A = R_q² / 2 · | Σ (λ_{i+1} − λ_i)·(sin β_i + sin β_{i+1}) |

    Substituting the authalic latitude is what makes this an *ellipsoidal* area rather than
    a spherical approximation. Using the geodetic latitude directly with an authalic radius
    is a common shortcut and is wrong by about half a percent at Chennai's latitude — which,
    on a two-acre holding, is a hundred square metres of land that either exists or does not.

    Areas on a patta are legal quantities. Computing them from projected coordinates imports
    the projection's area distortion into the land record — 5.4% in Web Mercator at 13°N —
    which is how a citizen ends up paying tax on land they do not have.
    """
    pts = list(ring)
    if len(pts) < 3:
        return 0.0
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(pts[:-1], pts[1:]):
        dl = math.radians(lon2 - lon1)
        b1 = _authalic_q(math.sin(math.radians(lat1)), _E) / _QP
        b2 = _authalic_q(math.sin(math.radians(lat2)), _E) / _QP
        total += dl * (b1 + b2)
    return abs(total) * WGS84_AUTHALIC_R ** 2 / 2.0


# --- Indian customary area units -------------------------------------------------------

AREA_UNITS_M2 = {
    "sqm": 1.0,
    "sqft": 0.09290304,
    "acre": 4046.8564224,
    "hectare": 10000.0,
    "cent": 40.468564224,          # 1/100 acre; the working unit of Tamil Nadu revenue
    "ground": 222.967296,          # 2400 sq ft; Chennai urban conveyancing unit
    "kuzhi": 3.34450944,           # 36 sq ft, Tamil Nadu
    "ankanam": 6.6889,             # Andhra/Telangana
    "guntha": 101.17141056,        # Maharashtra/Karnataka
    "bigha_assam": 1337.8,
    "kanal": 505.857,              # Punjab/Haryana/J&K
    "marla": 25.2929,
}


def convert_area(value_m2: float, unit: str) -> float:
    u = unit.lower().replace(" ", "_")
    if u not in AREA_UNITS_M2:
        raise KeyError(f"unknown area unit {unit!r}; known: {sorted(AREA_UNITS_M2)}")
    return value_m2 / AREA_UNITS_M2[u]


def format_extent(value_m2: float, style: str = "tamil_nadu") -> str:
    """Render an area the way the record of rights renders it."""
    if style == "tamil_nadu":
        acres = value_m2 / AREA_UNITS_M2["acre"]
        whole = int(acres)
        cents = round((acres - whole) * 100)
        if cents == 100:
            whole, cents = whole + 1, 0
        return f"{whole}.{cents:02d} acre ({value_m2:,.2f} m²)"
    if style == "chennai_urban":
        return f"{value_m2 / AREA_UNITS_M2['ground']:.3f} ground ({value_m2:,.2f} m²)"
    return f"{value_m2:,.2f} m²"
