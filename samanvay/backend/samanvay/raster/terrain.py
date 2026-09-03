"""Terrain processing: DSM to DTM, normalised DSM, and structure extraction from height.

The problem statement lists "DSM/DTM datasets" as an input. In practice a drone survey
delivers a DSM — the top surface, including every roof and tree — and a DTM is either
absent, or produced by a black-box step nobody can audit. The DTM is what matters for land
administration, because ``nDSM = DSM - DTM`` is the height of *things standing on the land*,
and that is the direct measurement of built form: which parcels are built on, how tall, how
much floor area, and what changed.

This module derives the DTM from the DSM with a **progressive morphological filter** (Zhang
et al., 2003, adapted for gridded surfaces rather than point clouds). The algorithm is:

1. Open the surface with a small structuring element. Anything the opening removes was a
   local maximum smaller than the element — a chimney, a tree crown, a car.
2. Grow the element and repeat. Each pass removes larger objects.
3. At each pass, only accept a point as ground if the drop it would take is within an
   elevation threshold that scales with the window size and the terrain slope.

The slope-scaled threshold is what makes it work on real terrain: a fixed threshold either
shaves hilltops or leaves buildings standing, and Indian urban ground is rarely flat.

Everything here operates on ordinary numpy arrays so it runs anywhere, and every parameter
that affects the outcome is named and reported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class GroundFilterConfig:
    cell_size_m: float = 0.5
    max_window_m: float = 40.0
    """Largest object to be removed. Must exceed the largest building footprint width."""
    initial_window_m: float = 1.0
    window_growth: float = 2.0
    initial_threshold_m: float = 0.30
    max_threshold_m: float = 6.0
    slope_tolerance: float = 0.30
    """Metres of elevation change per metre of window, allowed before a point is judged
    non-ground. 0.3 is a 17-degree slope, generous for urban terrain."""
    fill_nodata: bool = True
    smooth_ground: bool = True


@dataclass
class TerrainReport:
    passes: int = 0
    windows_m: list[float] = field(default_factory=list)
    thresholds_m: list[float] = field(default_factory=list)
    ground_fraction: float = 0.0
    ndsm_min: float = 0.0
    ndsm_max: float = 0.0
    ndsm_mean_above_ground: float = 0.0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"progressive morphological filter: {self.passes} passes, windows "
            f"{[round(w, 1) for w in self.windows_m]} m; {self.ground_fraction * 100:.1f}% "
            f"of cells classified as ground; nDSM range "
            f"{self.ndsm_min:.2f} to {self.ndsm_max:.2f} m"
        )


# --------------------------------------------------------------------------------------
# morphology (implemented directly so scipy.ndimage is the only heavy dependency)
# --------------------------------------------------------------------------------------


def _erode(a: np.ndarray, size: int) -> np.ndarray:
    from scipy.ndimage import minimum_filter

    return minimum_filter(a, size=size, mode="nearest")


def _dilate(a: np.ndarray, size: int) -> np.ndarray:
    from scipy.ndimage import maximum_filter

    return maximum_filter(a, size=size, mode="nearest")


def _open(a: np.ndarray, size: int) -> np.ndarray:
    return _dilate(_erode(a, size), size)


def fill_nodata(a: np.ndarray, max_iter: int = 64) -> np.ndarray:
    """Fill NaN holes by iterative nearest-valid diffusion.

    Photogrammetric DSMs are full of holes — water, shadow, featureless roofs. Ground
    filtering on an array with holes produces artefacts that look exactly like buildings,
    so the holes have to go first, and they have to be filled with something defensible:
    the local mean of valid neighbours, iterated, which is a discrete harmonic
    interpolation and does not invent structure.
    """
    from scipy.ndimage import uniform_filter

    out = a.copy()
    mask = np.isnan(out)
    if not mask.any():
        return out
    out[mask] = 0.0
    valid = (~mask).astype(np.float32)
    for _ in range(max_iter):
        if not mask.any():
            break
        num = uniform_filter(out * valid, size=5, mode="nearest")
        den = uniform_filter(valid, size=5, mode="nearest")
        filled = np.where(den > 0, num / np.maximum(den, 1e-9), 0.0)
        newly = mask & (den > 0)
        out[newly] = filled[newly]
        valid[newly] = 1.0
        mask = mask & ~newly
    return out


# --------------------------------------------------------------------------------------
# the filter
# --------------------------------------------------------------------------------------


def dsm_to_dtm(dsm: np.ndarray, config: GroundFilterConfig | None = None
               ) -> tuple[np.ndarray, np.ndarray, TerrainReport]:
    """Return ``(dtm, ground_mask, report)``."""
    cfg = config or GroundFilterConfig()
    rep = TerrainReport()

    surface = dsm.astype(np.float32)
    if cfg.fill_nodata and np.isnan(surface).any():
        rep.notes.append(
            f"{np.isnan(surface).mean() * 100:.2f}% of cells were nodata and were filled by "
            f"harmonic diffusion before filtering; heights there are interpolated, not observed"
        )
        surface = fill_nodata(surface)

    current = surface.copy()
    ground = np.ones(surface.shape, dtype=bool)

    window_m = cfg.initial_window_m
    last_window_m = 0.0
    while window_m <= cfg.max_window_m:
        size = max(3, int(round(window_m / cfg.cell_size_m)) | 1)  # odd
        opened = _open(current, size)
        dh = window_m - last_window_m
        threshold = min(
            cfg.max_threshold_m,
            cfg.initial_threshold_m + cfg.slope_tolerance * dh,
        )
        removed = (current - opened) > threshold
        ground &= ~removed
        current = opened
        rep.passes += 1
        rep.windows_m.append(window_m)
        rep.thresholds_m.append(threshold)
        last_window_m = window_m
        window_m *= cfg.window_growth

    dtm = np.where(ground, surface, np.nan).astype(np.float32)
    dtm = fill_nodata(dtm)
    if cfg.smooth_ground:
        from scipy.ndimage import uniform_filter

        dtm = uniform_filter(dtm, size=max(3, int(3.0 / cfg.cell_size_m) | 1), mode="nearest")

    # the DTM must never sit above the DSM: an interpolation artefact that does so
    # produces negative object heights, which then read as demolitions in change detection
    dtm = np.minimum(dtm, surface)

    rep.ground_fraction = float(ground.mean())
    _n = np.clip(surface - dtm, 0.0, None)
    rep.ndsm_min = float(np.nanmin(_n))
    rep.ndsm_max = float(np.nanmax(_n))
    above = _n[_n > 0.5]
    rep.ndsm_mean_above_ground = float(above.mean()) if above.size else 0.0
    if rep.ground_fraction < 0.15:
        rep.notes.append(
            "under 15% of the area was classified as ground. Either the site is extremely "
            "dense, or max_window_m is too small for the buildings present and their "
            "interiors are being treated as terrain."
        )
    return dtm, ground, rep


def normalised_dsm(dsm: np.ndarray, dtm: np.ndarray) -> np.ndarray:
    """nDSM: height above ground. The single most useful derived surface in this domain."""
    n = (dsm - dtm).astype(np.float32)
    return np.clip(n, 0.0, None)


# --------------------------------------------------------------------------------------
# derived products
# --------------------------------------------------------------------------------------


def slope_aspect(dem: np.ndarray, cell_size_m: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Slope in degrees and aspect in degrees from north, by Horn's method."""
    dy, dx = np.gradient(dem.astype(np.float32), cell_size_m)
    slope = np.degrees(np.arctan(np.hypot(dx, dy)))
    aspect = (np.degrees(np.arctan2(-dx, dy)) + 360.0) % 360.0
    return slope, aspect


def hillshade(dem: np.ndarray, cell_size_m: float = 0.5,
              azimuth_deg: float = 315.0, altitude_deg: float = 45.0) -> np.ndarray:
    slope, aspect = slope_aspect(dem, cell_size_m)
    z = math.radians(90.0 - altitude_deg)
    a = math.radians(360.0 - azimuth_deg + 90.0)
    s = np.radians(slope)
    asp = np.radians(aspect)
    shade = (math.cos(z) * np.cos(s)) + (math.sin(z) * np.sin(s) * np.cos(a - asp))
    return np.clip(shade, 0.0, 1.0)


@dataclass
class StructureCandidate:
    label: int
    area_m2: float
    mean_height_m: float
    max_height_m: float
    estimated_floors: int
    compactness: float
    geometry: Any = None


def extract_structures(ndsm: np.ndarray, *, cell_size_m: float = 0.5,
                       min_height_m: float = 2.2, min_area_m2: float = 12.0,
                       max_height_m: float = 300.0,
                       floor_height_m: float = 3.0) -> tuple[np.ndarray, list[StructureCandidate]]:
    """Segment above-ground structures from the normalised DSM.

    Purely geometric: no imagery, no training data, no model. It is included because it is
    the honest baseline that any learned footprint extractor has to beat, and because in a
    district with a drone survey but no labelled imagery it is the only extractor available.

    ``min_height_m`` of 2.2 m is deliberate — below that the returns are walls, vehicles,
    hedges and compound gates, and above it almost everything is a building in Indian urban
    fabric.
    """
    from scipy import ndimage

    mask = (ndsm >= min_height_m) & (ndsm <= max_height_m)
    # closing removes the holes that internal courtyards and roof gaps punch in the mask
    mask = ndimage.binary_closing(mask, structure=np.ones((3, 3)), iterations=2)
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3)), iterations=1)
    labels, n = ndimage.label(mask)

    cell_area = cell_size_m ** 2
    out: list[StructureCandidate] = []
    if n == 0:
        return labels, out

    objects = ndimage.find_objects(labels)
    for i in range(1, n + 1):
        sl = objects[i - 1]
        if sl is None:
            continue
        sub = labels[sl] == i
        area = float(sub.sum()) * cell_area
        if area < min_area_m2:
            labels[sl][sub] = 0
            continue
        heights = ndsm[sl][sub]
        perim = float(_perimeter(sub)) * cell_size_m
        compact = (4 * math.pi * area / (perim ** 2)) if perim > 0 else 0.0
        mean_h = float(np.nanmean(heights))
        max_h = float(np.nanmax(heights))
        out.append(StructureCandidate(
            label=i,
            area_m2=round(area, 2),
            mean_height_m=round(mean_h, 2),
            max_height_m=round(max_h, 2),
            estimated_floors=max(1, int(round(mean_h / floor_height_m))),
            compactness=round(compact, 3),
        ))
    return labels, out


def _perimeter(mask: np.ndarray) -> int:
    p = 0
    p += int(np.abs(np.diff(mask.astype(np.int8), axis=0)).sum())
    p += int(np.abs(np.diff(mask.astype(np.int8), axis=1)).sum())
    p += int(mask[0].sum() + mask[-1].sum() + mask[:, 0].sum() + mask[:, -1].sum())
    return p


def polygonise(labels: np.ndarray, transform, *, crs: str = "EPSG:3857",
               simplify_m: float = 0.35) -> dict[int, Any]:
    """Vectorise a labelled raster into shapely polygons in the raster's CRS."""
    from rasterio.features import shapes as rio_shapes
    from shapely.geometry import shape as shp_shape

    out: dict[int, Any] = {}
    for geom, value in rio_shapes(labels.astype(np.int32), mask=labels > 0,
                                  transform=transform):
        v = int(value)
        g = shp_shape(geom)
        if simplify_m > 0:
            g = g.simplify(simplify_m, preserve_topology=True)
        if v in out:
            out[v] = out[v].union(g)
        else:
            out[v] = g
    return out


def height_statistics_for(polygons: dict[str, Any], ndsm: np.ndarray, transform
                          ) -> dict[str, dict[str, float]]:
    """Per-polygon height statistics from the nDSM.

    This is what lets the platform attach a measured height to a footprint that came from a
    completely different source: the municipal survey gives the outline, the drone gives the
    height, and the harmonised record has both — which neither source could have produced
    alone. That is the whole point of the exercise.
    """
    from rasterio.features import geometry_mask

    out: dict[str, dict[str, float]] = {}
    h, w = ndsm.shape
    for fid, geom in polygons.items():
        try:
            m = geometry_mask([geom], out_shape=(h, w), transform=transform, invert=True)
        except Exception:  # noqa: BLE001
            continue
        vals = ndsm[m]
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            continue
        out[fid] = {
            "mean_height_m": round(float(vals.mean()), 3),
            "max_height_m": round(float(vals.max()), 3),
            "p95_height_m": round(float(np.percentile(vals, 95)), 3),
            "std_height_m": round(float(vals.std()), 3),
            "coverage_cells": int(vals.size),
        }
    return out
