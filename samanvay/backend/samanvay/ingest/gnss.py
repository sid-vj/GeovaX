"""GNSS / CORS observation ingestion.

CORS observations are the metrological anchor of the entire land record. Everything else in
the platform is judged, in the end, against points whose coordinates were determined by
carrier-phase GNSS. This module reads the formats those observations actually arrive in and
— critically — carries their *uncertainty* through, because a control point without a
covariance is not control, it is a rumour.

Supported inputs:

* **RINEX 2.x / 3.x observation headers** — for station position, antenna and interval
  metadata. Full carrier-phase processing is out of scope for a harmonisation platform;
  what is needed is the adjusted station coordinate and its quality, which the header and
  the accompanying SINEX/position file carry.
* **NMEA 0183** streams (``$GPGGA``/``$GNGGA``) — what a field tablet or a rover logs.
  HDOP, fix quality and satellite count are parsed and converted into a usable sigma.
* **CSV control lists** — how a state survey department actually hands over control.

Everything is normalised to a ``ControlObservation`` in a declared CRS with a 1-sigma
horizontal and vertical uncertainty, ready to be used as a ground control point.
"""

from __future__ import annotations

import csv
import os
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

# NMEA fix quality -> nominal horizontal sigma in metres, before HDOP scaling.
FIX_QUALITY_SIGMA = {
    0: float("nan"),  # invalid
    1: 3.5,           # autonomous
    2: 0.8,           # DGPS / SBAS
    3: 3.5,           # PPS
    4: 0.014,         # RTK fixed
    5: 0.35,          # RTK float
    6: 1.5,           # dead reckoning
    7: 3.0,           # manual
    8: 3.0,           # simulation
}

FIX_QUALITY_NAME = {
    0: "invalid", 1: "autonomous", 2: "differential", 3: "pps",
    4: "rtk_fixed", 5: "rtk_float", 6: "dead_reckoning", 7: "manual", 8: "simulated",
}


@dataclass
class ControlObservation:
    """One GNSS-determined position, with the uncertainty that makes it usable."""

    point_id: str
    lon: float
    lat: float
    ellipsoidal_height: float | None = None
    orthometric_height: float | None = None
    crs: str = "EPSG:4326"
    sigma_h_m: float = float("nan")
    sigma_v_m: float = float("nan")
    method: str = "unknown"
    epoch: datetime | None = None
    satellites: int | None = None
    hdop: float | None = None
    pdop: float | None = None
    station: str = ""
    antenna: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def usable_as_control(self) -> bool:
        """Whether this observation is good enough to georeference a cadastral sheet.

        The bar is the NAKSHA control specification: 1-sigma horizontal at or below 10 cm.
        An autonomous single-point fix at 3.5 m is not control; used as such it drags an
        entire sheet three metres off and the error is invisible because everything moved
        together.
        """
        return math.isfinite(self.sigma_h_m) and self.sigma_h_m <= 0.10

    def to_gcp(self, target_x: float, target_y: float):
        from ..crs.gcp import GroundControlPoint

        return GroundControlPoint(
            gcp_id=self.point_id,
            source_x=target_x, source_y=target_y,
            target_x=self.lon, target_y=self.lat,
            sigma_m=self.sigma_h_m if math.isfinite(self.sigma_h_m) else 5.0,
            description=f"{self.method} @ {self.station}",
            method="cors" if "rtk" in self.method or "static" in self.method else "gnss",
        )


# --------------------------------------------------------------------------------------
# NMEA
# --------------------------------------------------------------------------------------


def _nmea_checksum_ok(sentence: str) -> bool:
    if "*" not in sentence:
        return False
    body, _, given = sentence[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(given.strip()[:2], 16)
    except ValueError:
        return False


def _dm_to_deg(value: str, hemi: str) -> float:
    if not value:
        return float("nan")
    dot = value.find(".")
    deg_len = dot - 2
    deg = float(value[:deg_len]) if deg_len > 0 else 0.0
    minutes = float(value[deg_len:])
    dec = deg + minutes / 60.0
    return -dec if hemi in ("S", "W") else dec


def read_nmea(path: str, *, point_prefix: str = "NMEA") -> Iterator[ControlObservation]:
    """Parse ``$..GGA`` sentences from a rover log."""
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("$") or "GGA" not in line[:7]:
                continue
            if not _nmea_checksum_ok(line):
                continue
            p = line.split("*")[0].split(",")
            if len(p) < 15:
                continue
            try:
                quality = int(p[6] or 0)
                lat = _dm_to_deg(p[2], p[3])
                lon = _dm_to_deg(p[4], p[5])
                sats = int(p[7]) if p[7] else None
                hdop = float(p[8]) if p[8] else None
                alt = float(p[9]) if p[9] else None
                geoid_sep = float(p[11]) if p[11] else None
            except ValueError:
                continue
            if not (math.isfinite(lat) and math.isfinite(lon)):
                continue
            base = FIX_QUALITY_SIGMA.get(quality, float("nan"))
            sigma_h = base * (hdop if hdop else 1.0)
            n += 1
            yield ControlObservation(
                point_id=f"{point_prefix}-{n:06d}",
                lon=lon, lat=lat,
                orthometric_height=alt,
                ellipsoidal_height=(alt + geoid_sep) if (alt is not None and geoid_sep is not None) else None,
                sigma_h_m=sigma_h,
                sigma_v_m=sigma_h * 1.8,
                method=FIX_QUALITY_NAME.get(quality, "unknown"),
                satellites=sats,
                hdop=hdop,
                notes=([] if quality in (2, 4, 5) else
                       [f"fix quality {quality} ({FIX_QUALITY_NAME.get(quality)}) is not "
                        f"survey grade"]),
            )


# --------------------------------------------------------------------------------------
# RINEX
# --------------------------------------------------------------------------------------

_RINEX_LABEL = re.compile(r"^(.{0,60})(.*)$")


def read_rinex_header(path: str) -> dict[str, object]:
    """Extract station metadata and the approximate position from a RINEX obs file."""
    out: dict[str, object] = {"format": "rinex", "path": path}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            label = line[60:].strip()
            value = line[:60]
            if label == "RINEX VERSION / TYPE":
                out["version"] = value[:9].strip()
                out["file_type"] = value[20:21]
                out["system"] = value[40:41]
            elif label == "MARKER NAME":
                out["station"] = value.strip()
            elif label == "MARKER NUMBER":
                out["marker_number"] = value.strip()
            elif label == "ANT # / TYPE":
                out["antenna"] = value[20:40].strip()
            elif label == "REC # / TYPE / VERS":
                out["receiver"] = value[20:40].strip()
            elif label == "APPROX POSITION XYZ":
                try:
                    out["ecef"] = tuple(float(value[i:i + 14]) for i in (0, 14, 28))
                except ValueError:
                    pass
            elif label == "ANTENNA: DELTA H/E/N":
                try:
                    out["antenna_delta_hen"] = tuple(float(value[i:i + 14]) for i in (0, 14, 28))
                except ValueError:
                    pass
            elif label == "INTERVAL":
                try:
                    out["interval_s"] = float(value[:10])
                except ValueError:
                    pass
            elif label == "TIME OF FIRST OBS":
                out["first_obs"] = value.strip()
            elif label == "END OF HEADER":
                break
    if "ecef" in out:
        x, y, z = out["ecef"]  # type: ignore[misc]
        out["llh"] = ecef_to_llh(x, y, z)
    return out


def read_rinex_as_control(path: str) -> ControlObservation:
    """Bridge a real RINEX header's marker position into the same `ControlObservation`
    model the NMEA/CSV readers already produce, so it can flow into
    `assess_control_network` and the CRS/GCP georeferencing engine like any other control
    point — previously `read_rinex_header` returned a raw metadata dict that nothing
    downstream actually consumed.

    Honest about precision: a RINEX observation header carries the receiver's *approximate*
    marker position (a real, surveyed monument coordinate) but not a formal error ellipse —
    that requires network adjustment against a SINEX/position file this reader does not have.
    `sigma_h_m`/`sigma_v_m` are therefore left as NaN (not fabricated as a plausible-looking
    but invented sub-centimetre figure) and `usable_as_control` correctly reports False for a
    point in this state, exactly as it does for any observation with unknown quality.
    """
    h = read_rinex_header(path)
    if "llh" not in h:
        raise ValueError(f"{path}: no APPROX POSITION XYZ in RINEX header — cannot derive a position")
    lon, lat, height = h["llh"]  # type: ignore[misc]
    return ControlObservation(
        point_id=str(h.get("station", os.path.basename(path))),
        lon=lon, lat=lat, ellipsoidal_height=height,
        crs="EPSG:4326",
        sigma_h_m=float("nan"), sigma_v_m=float("nan"),
        method="rinex_header_marker_position",
        station=str(h.get("station", "")),
        antenna=str(h.get("antenna", "")),
        notes=[f"receiver={h.get('receiver', '?')}",
               f"rinex_version={h.get('version', '?')}",
               "formal sigma requires network adjustment (SINEX) not performed here"],
    )


def ecef_to_llh(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Earth-centred earth-fixed to WGS 84 lon/lat/height (Bowring, one iteration).

    A RINEX header carries ECEF; every other stage of the platform speaks lon/lat. Getting
    this conversion subtly wrong is a classic source of a systematic 10–30 m offset in an
    otherwise perfect control network.
    """
    a = 6378137.0
    f = 1 / 298.257223563
    b = a * (1 - f)
    e2 = f * (2 - f)
    ep2 = (a * a - b * b) / (b * b)
    p = math.hypot(x, y)
    theta = math.atan2(z * a, p * b)
    lat = math.atan2(z + ep2 * b * math.sin(theta) ** 3,
                     p - e2 * a * math.cos(theta) ** 3)
    lon = math.atan2(y, x)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    h = p / math.cos(lat) - n
    return math.degrees(lon), math.degrees(lat), h


def llh_to_ecef(lon: float, lat: float, h: float) -> tuple[float, float, float]:
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    la, lo = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return (
        (n + h) * math.cos(la) * math.cos(lo),
        (n + h) * math.cos(la) * math.sin(lo),
        (n * (1 - e2) + h) * math.sin(la),
    )


# --------------------------------------------------------------------------------------
# CSV control lists
# --------------------------------------------------------------------------------------

_ALIASES = {
    "lon": {"lon", "long", "longitude", "x", "east", "easting", "e"},
    "lat": {"lat", "latitude", "y", "north", "northing", "n"},
    "id": {"id", "point_id", "pointid", "name", "station", "gcp", "gcp_id", "point"},
    "h": {"h", "height", "elev", "elevation", "z", "ortho_h", "msl"},
    "sigma": {"sigma", "sigma_h", "accuracy", "acc", "rms", "hrms", "precision"},
    "method": {"method", "type", "mode", "fix", "technique"},
}


def _map_columns(header: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in header:
        k = h.strip().lower().replace(" ", "_")
        for target, names in _ALIASES.items():
            if k in names and target not in out:
                out[target] = h
    return out


def read_control_csv(path: str, crs: str = "EPSG:4326",
                     default_sigma_m: float = 0.05) -> list[ControlObservation]:
    obs: list[ControlObservation] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        cols = _map_columns(rd.fieldnames or [])
        missing = {"lon", "lat"} - set(cols)
        if missing:
            raise ValueError(
                f"control file {path!r} has no recognisable {'/'.join(sorted(missing))} "
                f"column; found {rd.fieldnames}"
            )
        for i, row in enumerate(rd):
            try:
                lon = float(row[cols["lon"]])
                lat = float(row[cols["lat"]])
            except (TypeError, ValueError):
                continue
            sigma = default_sigma_m
            if "sigma" in cols and row.get(cols["sigma"]):
                try:
                    sigma = float(row[cols["sigma"]])
                except ValueError:
                    pass
            height = None
            if "h" in cols and row.get(cols["h"]):
                try:
                    height = float(row[cols["h"]])
                except ValueError:
                    pass
            obs.append(
                ControlObservation(
                    point_id=str(row.get(cols.get("id", ""), "") or f"CP-{i:05d}"),
                    lon=lon, lat=lat,
                    orthometric_height=height,
                    crs=crs,
                    sigma_h_m=sigma,
                    sigma_v_m=sigma * 1.8,
                    method=str(row.get(cols.get("method", ""), "") or "surveyed"),
                    epoch=datetime.now(timezone.utc),
                )
            )
    return obs


# --------------------------------------------------------------------------------------
# network quality
# --------------------------------------------------------------------------------------


@dataclass
class ControlNetworkReport:
    n_points: int
    n_usable: int
    mean_sigma_h: float
    convex_hull_area_km2: float
    max_gap_km: float
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.n_usable}/{self.n_points} observations are survey grade; "
            f"mean sigma {self.mean_sigma_h * 100:.1f} cm; control covers "
            f"{self.convex_hull_area_km2:.2f} km² with a largest gap of {self.max_gap_km:.2f} km"
        )


def assess_control_network(obs: list[ControlObservation]) -> ControlNetworkReport:
    """Judge whether a control network is fit to georeference an area.

    Two failure modes matter and both are geometric rather than statistical: control that
    is *clustered* (fits beautifully in one corner and diverges everywhere else) and
    control that *does not enclose* the area being warped, so every point of interest is
    an extrapolation. Both are reported here rather than discovered later as a mysterious
    systematic error.
    """
    from shapely.geometry import MultiPoint

    usable = [o for o in obs if o.usable_as_control]
    sig = [o.sigma_h_m for o in obs if math.isfinite(o.sigma_h_m)]
    warnings: list[str] = []

    if not obs:
        return ControlNetworkReport(0, 0, float("nan"), 0.0, 0.0, ["no control supplied"])

    pts = MultiPoint([(o.lon, o.lat) for o in obs])
    hull = pts.convex_hull
    # crude but adequate degree->km at Indian latitudes
    km_per_deg_lat = 110.574
    km_per_deg_lon = 111.320 * math.cos(math.radians(sum(o.lat for o in obs) / len(obs)))
    area_km2 = hull.area * km_per_deg_lat * km_per_deg_lon if hull.geom_type == "Polygon" else 0.0

    max_gap = 0.0
    coords = [(o.lon, o.lat) for o in obs]
    for i, (x1, y1) in enumerate(coords):
        nearest = min(
            (math.hypot((x2 - x1) * km_per_deg_lon, (y2 - y1) * km_per_deg_lat)
             for j, (x2, y2) in enumerate(coords) if j != i),
            default=0.0,
        )
        max_gap = max(max_gap, nearest)

    if len(usable) < 4:
        warnings.append(
            f"only {len(usable)} survey-grade points; a similarity fit needs 2 and an "
            f"affine fit needs 3, but with fewer than 4 there is no redundancy and no "
            f"blunder can be detected"
        )
    if area_km2 < 0.1:
        warnings.append(
            "control is clustered into a very small area; any transformation fitted to it "
            "will extrapolate wildly outside that cluster"
        )
    if max_gap > 2.0:
        warnings.append(
            f"largest inter-point gap is {max_gap:.1f} km; local distortion between "
            f"control points cannot be modelled at that spacing"
        )

    return ControlNetworkReport(
        n_points=len(obs),
        n_usable=len(usable),
        mean_sigma_h=float(sum(sig) / len(sig)) if sig else float("nan"),
        convex_hull_area_km2=area_km2,
        max_gap_km=max_gap,
        warnings=warnings,
    )
