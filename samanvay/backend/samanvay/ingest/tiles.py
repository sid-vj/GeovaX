"""XYZ tile-pyramid ingestion.

A great deal of real, publicly released photogrammetric output — orthophotos, DSMs,
hillshades — is distributed only as a rendered XYZ tile pyramid, because that is what a web
map needs. A harmonisation platform cannot work on tiles: it needs a georeferenced raster
it can resample, difference and measure on.

This module rebuilds a georeferenced GeoTIFF from a tile directory. The georeferencing is
exact rather than approximate, because the Web Mercator tile scheme is an exact
specification: tile ``(z, x, y)`` covers a known rectangle in EPSG:3857, so mosaicking
tiles at a fixed zoom and writing the corresponding affine transform reproduces the source
raster's geometry to within the resampling the tiler itself performed.

It also handles the annoying-but-common case of a **colour-ramped DSM**: an elevation
surface published as an RGB image through a matplotlib-style colour map. The elevation is
recoverable by inverting the ramp — build a lookup table of the colour map, match each
pixel to its nearest ramp colour, and read back the normalised height. That recovers a
faithful *relative* surface; the absolute datum offset has to come from metadata or
control, and the module is explicit about which of the two it produced.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Iterable, Literal

import numpy as np

TILE_SIZE = 256
ORIGIN_SHIFT = 2 * math.pi * 6378137 / 2.0  # 20037508.342789244


# --------------------------------------------------------------------------------------
# tile maths
# --------------------------------------------------------------------------------------


def tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Exact EPSG:3857 bounds of an XYZ tile."""
    n = 2 ** z
    span = 2 * ORIGIN_SHIFT / n
    minx = -ORIGIN_SHIFT + x * span
    maxx = minx + span
    maxy = ORIGIN_SHIFT - y * span
    miny = maxy - span
    return minx, miny, maxx, maxy


def resolution_at(z: int) -> float:
    """Metres per pixel in EPSG:3857 at zoom ``z``."""
    return 2 * ORIGIN_SHIFT / (TILE_SIZE * 2 ** z)


def ground_resolution(z: int, latitude: float) -> float:
    """True ground resolution in metres, correcting Mercator's latitude stretch."""
    return resolution_at(z) * math.cos(math.radians(latitude))


@dataclass
class TilePyramid:
    root: str
    scheme: Literal["xyz", "tms"] = "xyz"

    def zooms(self) -> list[int]:
        out = []
        for name in os.listdir(self.root):
            if name.isdigit() and os.path.isdir(os.path.join(self.root, name)):
                out.append(int(name))
        return sorted(out)

    def tiles(self, z: int) -> list[tuple[int, int, str]]:
        base = os.path.join(self.root, str(z))
        out: list[tuple[int, int, str]] = []
        if not os.path.isdir(base):
            return out
        for xd in os.listdir(base):
            if not xd.isdigit():
                continue
            xdir = os.path.join(base, xd)
            for fn in os.listdir(xdir):
                m = re.match(r"^(\d+)\.(png|jpg|jpeg|webp)$", fn)
                if not m:
                    continue
                y = int(m.group(1))
                if self.scheme == "tms":
                    y = (2 ** z - 1) - y
                out.append((int(xd), y, os.path.join(xdir, fn)))
        return out

    def extent(self, z: int) -> tuple[int, int, int, int]:
        ts = self.tiles(z)
        if not ts:
            raise ValueError(f"no tiles at zoom {z} under {self.root}")
        xs = [t[0] for t in ts]
        ys = [t[1] for t in ts]
        return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------------------
# mosaicking
# --------------------------------------------------------------------------------------


def mosaic_to_array(pyramid: TilePyramid, z: int, *, bands: int = 3
                    ) -> tuple[np.ndarray, np.ndarray, tuple[float, float, float, float]]:
    """Return ``(image, alpha, bounds_3857)`` for a whole zoom level."""
    from PIL import Image

    x0, y0, x1, y1 = pyramid.extent(z)
    w = (x1 - x0 + 1) * TILE_SIZE
    h = (y1 - y0 + 1) * TILE_SIZE
    img = np.zeros((h, w, bands), dtype=np.uint8)
    alpha = np.zeros((h, w), dtype=np.uint8)

    for tx, ty, path in pyramid.tiles(z):
        try:
            tile = Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            continue
        arr = np.asarray(tile)
        ox = (tx - x0) * TILE_SIZE
        oy = (ty - y0) * TILE_SIZE
        img[oy:oy + TILE_SIZE, ox:ox + TILE_SIZE, :] = arr[..., :bands]
        alpha[oy:oy + TILE_SIZE, ox:ox + TILE_SIZE] = arr[..., 3]

    minx, _, _, maxy = tile_bounds_3857(z, x0, y0)
    _, miny, maxx, _ = tile_bounds_3857(z, x1, y1)
    return img, alpha, (minx, miny, maxx, maxy)


def write_geotiff(path: str, array: np.ndarray, bounds: tuple[float, float, float, float],
                  crs: str = "EPSG:3857", nodata: float | None = None,
                  dtype: str | None = None) -> str:
    """Write a mosaic as a tiled, compressed, overview-bearing GeoTIFF (a valid COG)."""
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.enums import Resampling

    if array.ndim == 2:
        array = array[None, ...]
    elif array.shape[-1] <= 4:
        array = np.moveaxis(array, -1, 0)

    count, h, w = array.shape
    transform = from_bounds(bounds[0], bounds[1], bounds[2], bounds[3], w, h)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "count": count,
        "dtype": dtype or array.dtype.name,
        "crs": crs,
        "transform": transform,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "deflate",
        "predictor": 2 if np.issubdtype(array.dtype, np.floating) else 2,
        "BIGTIFF": "IF_SAFER",
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(profile["dtype"]))
        factors = [f for f in (2, 4, 8, 16) if min(h, w) // f >= 256]
        if factors:
            dst.build_overviews(factors, Resampling.average)
            dst.update_tags(ns="rio_overview", resampling="average")
    return path


# --------------------------------------------------------------------------------------
# colour-ramp inversion for published DSM tiles
# --------------------------------------------------------------------------------------


class ColourRampInverter:
    """Recover a scalar field from an image rendered through a known colour map.

    The inversion is a nearest-neighbour lookup in RGB space against a densely sampled
    ramp. Two properties make it trustworthy for elevation:

    * Perceptually-uniform ramps (viridis, magma, cividis) are injective in RGB, so the
      inverse is single-valued and stable.
    * Ramps that are *not* injective (jet, rainbow) are detected by measuring how many
      distinct ramp positions share a near-identical colour, and the inverter refuses to
      run rather than silently producing a folded surface.

    The recovered field is normalised 0..1. Absolute elevation requires the value range,
    which is taken from metadata or from control points.
    """

    def __init__(self, cmap_name: str = "viridis", samples: int = 1024) -> None:
        from matplotlib import colormaps

        self.cmap_name = cmap_name
        cmap = colormaps[cmap_name]
        t = np.linspace(0.0, 1.0, samples)
        self.ramp = (np.asarray(cmap(t))[:, :3] * 255.0).astype(np.float32)
        self.positions = t.astype(np.float32)
        self._check_injective()

    def _check_injective(self, tol: float = 6.0) -> None:
        d = np.linalg.norm(self.ramp[1:] - self.ramp[:-1], axis=1)
        # A ramp that revisits a colour will show near-zero steps far apart; detect by
        # comparing every 32nd entry pairwise.
        coarse = self.ramp[::32]
        dist = np.linalg.norm(coarse[:, None, :] - coarse[None, :, :], axis=-1)
        np.fill_diagonal(dist, 1e9)
        for i in range(dist.shape[0]):
            for j in range(i + 4, dist.shape[0]):
                if dist[i, j] < tol:
                    raise ValueError(
                        f"colour map {self.cmap_name!r} is not injective (positions "
                        f"{i * 32} and {j * 32} render to near-identical colours); "
                        f"elevation cannot be recovered from it without ambiguity"
                    )
        # A dense sampling of an 8-bit colour map inevitably has adjacent samples that
        # round to the same RGB triple; that is quantisation, not non-injectivity. What
        # actually breaks the inverse is a *sustained* flat run, so the test is on the
        # longest constant stretch rather than on individual steps.
        flat = d < 0.5
        longest = run = 0
        for f in flat:
            run = run + 1 if f else 0
            longest = max(longest, run)
        if longest > 0.05 * self.ramp.shape[0]:
            raise ValueError(
                f"colour map {self.cmap_name!r} is constant over a run of {longest} of "
                f"{self.ramp.shape[0]} samples; the inverse would be ambiguous across that "
                f"whole value range"
            )
        self.quantisation_ratio = float(
            len(np.unique(self.ramp.astype(np.uint8), axis=0)) / self.ramp.shape[0]
        )

    def invert(self, rgb: np.ndarray, alpha: np.ndarray | None = None) -> np.ndarray:
        """RGB image -> normalised scalar field, NaN where transparent.

        Nearest-neighbour lookup against the ramp, done through a k-d tree over the unique
        colours actually present. A 4-megapixel orthophoto against a 1024-sample ramp is
        four billion distance evaluations done naively; de-duplicating first (a colour-ramped
        image has at most a few thousand distinct colours) and using a tree reduces that to
        a few thousand queries, which is the difference between four minutes and a tenth of
        a second.
        """
        from scipy.spatial import cKDTree

        h, w = rgb.shape[:2]
        flat = rgb.reshape(-1, 3).astype(np.uint8)
        uniq, inverse = np.unique(flat, axis=0, return_inverse=True)
        tree = getattr(self, "_tree", None)
        if tree is None:
            tree = cKDTree(self.ramp)
            self._tree = tree
        _, idx = tree.query(uniq.astype(np.float32), k=1, workers=-1)
        field = self.positions[idx][inverse].reshape(h, w).astype(np.float32)
        if alpha is not None:
            field = np.where(alpha > 8, field, np.nan)
        return field

    #: Mean RGB residual above which a recovered surface must not be treated as elevation.
    #: A correct ramp fits observed pixels to within a few RGB units; a residual in the
    #: tens means the image was rendered with a ramp the platform does not have, and the
    #: recovered values would be a plausible-looking fiction.
    MAX_TRUSTWORTHY_RESIDUAL = 25.0

    @staticmethod
    def detect(rgb: np.ndarray, alpha: np.ndarray | None = None,
               candidates: Iterable[str] = ("viridis", "magma", "inferno", "plasma",
                                            "cividis", "terrain", "gist_earth", "gray")
               ) -> tuple[str, float]:
        """Guess which colour map an image was rendered with.

        Scores each candidate by the mean RGB distance from the observed pixels to the
        nearest colour on that ramp. The correct ramp fits by construction; a wrong one
        leaves a large residual. Returns ``(name, mean_residual)``.
        """
        from matplotlib import colormaps

        mask = alpha > 8 if alpha is not None else np.ones(rgb.shape[:2], bool)
        px = rgb[mask].astype(np.float32)
        if px.size == 0:
            return "gray", float("inf")
        if px.shape[0] > 20000:
            idx = np.random.default_rng(7).choice(px.shape[0], 20000, replace=False)
            px = px[idx]
        from scipy.spatial import cKDTree

        best, best_score = "gray", float("inf")
        for name in candidates:
            try:
                cmap = colormaps[name]
            except KeyError:
                continue
            ramp = (np.asarray(cmap(np.linspace(0, 1, 512)))[:, :3] * 255.0).astype(np.float32)
            d, _ = cKDTree(ramp).query(px, k=1, workers=-1)
            score = float(d.mean())
            if score < best_score:
                best, best_score = name, score
        return best, best_score


def dsm_from_tiles(pyramid: TilePyramid, z: int, out_path: str, *,
                   value_range: tuple[float, float] | None = None,
                   cmap: str | None = None) -> dict[str, object]:
    """Rebuild an elevation raster from a colour-ramped DSM tile pyramid."""
    rgb, alpha, bounds = mosaic_to_array(pyramid, z, bands=3)
    name, residual = ColourRampInverter.detect(rgb, alpha) if cmap is None else (cmap, 0.0)
    inv = ColourRampInverter(name)
    field = inv.invert(rgb, alpha)
    absolute = value_range is not None
    if absolute:
        lo, hi = value_range
        field = lo + field * (hi - lo)
    write_geotiff(out_path, field.astype(np.float32), bounds, nodata=float("nan"),
                  dtype="float32")
    return {
        "path": out_path,
        "colormap": name,
        "fit_residual_rgb": round(residual, 3),
        "absolute": absolute,
        "units": "metres" if absolute else "normalised 0..1",
        "bounds_3857": bounds,
        "resolution_m": resolution_at(z),
        "caveat": (
            "" if absolute else
            "Vertical datum offset and scale unknown: this surface is faithful in relative "
            "height only. Absolute elevations require the publisher's value range or "
            "vertical control."
        ),
    }
