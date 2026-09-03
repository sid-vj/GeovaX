"""Raster change detection between two epochs of orthoimagery and surface models.

Vector change detection (``samanvay.change.vector_change``) answers "what changed in the
records". Raster change detection answers the prior question: "what changed on the ground,
including the things no record knows about". For a land administration the second is the
one that finds unauthorised construction, because unauthorised construction by definition
does not appear in anyone's vector layer.

The detector fuses three independent signals, which is what separates it from a naive
image difference:

**Spectral.** Change Vector Analysis over the ortho pair — magnitude and direction of the
per-pixel spectral change. Sensitive to everything, including everything irrelevant:
shadows, seasonal vegetation, a repainted roof, different sun angle.

**Structural.** Local normalised cross-correlation over a moving window. Measures whether
the *texture* changed rather than the brightness, so it survives illumination differences
that defeat spectral differencing.

**Geometric.** nDSM differencing. A new building adds height; a repainted roof does not.
This is the decisive signal, and it is the reason the platform insists on DSM alongside
imagery: without height, distinguishing a new building from a new car park is guesswork.

Pixels are flagged only where the evidence agrees, and the flagged regions are then vectorised,
filtered by size and shape, and typed by the sign of the height change. Every output carries
the three component scores so an analyst can see *why* something was flagged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core.models import ChangeType


@dataclass
class RasterChangeConfig:
    cell_size_m: float = 0.5
    spectral_threshold_sigma: float = 2.5
    """Change-vector magnitude in robust standard deviations above the scene median."""
    correlation_window: int = 9
    correlation_threshold: float = 0.55
    """Local NCC below this counts as structural change."""
    height_threshold_m: float = 1.8
    min_region_m2: float = 12.0
    min_compactness: float = 0.14
    require_agreeing_signals: int = 2
    """How many of the three signals must agree before a pixel is flagged."""
    shadow_guard: bool = True


@dataclass
class RasterChangeRegion:
    region_id: int
    area_m2: float
    change_type: ChangeType
    mean_height_delta_m: float
    max_height_delta_m: float
    spectral_score: float
    structural_score: float
    geometric_score: float
    confidence: float
    compactness: float
    geometry: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "area_m2": round(self.area_m2, 2),
            "change_type": self.change_type.value,
            "mean_height_delta_m": round(self.mean_height_delta_m, 2),
            "max_height_delta_m": round(self.max_height_delta_m, 2),
            "spectral_score": round(self.spectral_score, 4),
            "structural_score": round(self.structural_score, 4),
            "geometric_score": round(self.geometric_score, 4),
            "confidence": round(self.confidence, 4),
            "compactness": round(self.compactness, 3),
        }


@dataclass
class RasterChangeReport:
    n_regions: int = 0
    total_change_area_m2: float = 0.0
    new_construction_m2: float = 0.0
    demolition_m2: float = 0.0
    flagged_pixel_fraction: float = 0.0
    signals_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.n_regions:,} change regions covering "
            f"{self.total_change_area_m2:,.0f} m² "
            f"({self.new_construction_m2:,.0f} m² new build, "
            f"{self.demolition_m2:,.0f} m² removed); "
            f"{self.flagged_pixel_fraction * 100:.2f}% of pixels flagged; signals: "
            f"{', '.join(self.signals_used)}"
        )


# --------------------------------------------------------------------------------------


def change_vector_magnitude(before: np.ndarray, after: np.ndarray
                            ) -> tuple[np.ndarray, np.ndarray]:
    """Change Vector Analysis: per-pixel magnitude and direction over the band stack."""
    b = _as_bands(before).astype(np.float32)
    a = _as_bands(after).astype(np.float32)
    n = min(b.shape[0], a.shape[0])
    b, a = b[:n], a[:n]
    # radiometric normalisation: match the later image's histogram to the earlier one's
    # first two moments, so a different sun angle does not read as change everywhere
    for i in range(n):
        bi, ai = b[i], a[i]
        sb, sa = bi.std(), ai.std()
        if sa > 1e-6:
            a[i] = (ai - ai.mean()) * (sb / sa) + bi.mean()
    d = a - b
    mag = np.sqrt((d ** 2).sum(axis=0))
    direction = np.arctan2(d[min(1, n - 1)], d[0]) if n >= 2 else np.zeros_like(mag)
    return mag, direction


def local_correlation(before: np.ndarray, after: np.ndarray, window: int = 9) -> np.ndarray:
    """Moving-window normalised cross-correlation. High where texture is preserved."""
    from scipy.ndimage import uniform_filter

    b = _grey(before).astype(np.float32)
    a = _grey(after).astype(np.float32)
    k = dict(size=window, mode="nearest")
    mb = uniform_filter(b, **k)
    ma = uniform_filter(a, **k)
    bb = uniform_filter(b * b, **k) - mb * mb
    aa = uniform_filter(a * a, **k) - ma * ma
    ba = uniform_filter(b * a, **k) - mb * ma
    denom = np.sqrt(np.maximum(bb, 0) * np.maximum(aa, 0))
    return np.where(denom > 1e-6, ba / np.maximum(denom, 1e-6), 0.0)


def detect(before_ortho: np.ndarray, after_ortho: np.ndarray, *,
           before_ndsm: np.ndarray | None = None,
           after_ndsm: np.ndarray | None = None,
           transform=None,
           config: RasterChangeConfig | None = None
           ) -> tuple[np.ndarray, list[RasterChangeRegion], RasterChangeReport]:
    """Return ``(label_raster, regions, report)``."""
    from scipy import ndimage

    cfg = config or RasterChangeConfig()
    rep = RasterChangeReport()

    mag, _ = change_vector_magnitude(before_ortho, after_ortho)
    med = float(np.nanmedian(mag))
    mad = float(np.nanmedian(np.abs(mag - med))) or 1e-6
    robust_sigma = 1.4826 * mad
    spectral = mag > (med + cfg.spectral_threshold_sigma * robust_sigma)
    rep.signals_used.append("spectral (change vector analysis)")

    ncc = local_correlation(before_ortho, after_ortho, cfg.correlation_window)
    structural = ncc < cfg.correlation_threshold
    rep.signals_used.append("structural (local normalised cross-correlation)")

    if before_ndsm is not None and after_ndsm is not None:
        dh = np.nan_to_num(after_ndsm.astype(np.float32) - before_ndsm.astype(np.float32))
        geometric = np.abs(dh) >= cfg.height_threshold_m
        rep.signals_used.append("geometric (nDSM differencing)")
    else:
        dh = np.zeros_like(mag, dtype=np.float32)
        geometric = np.zeros_like(spectral, dtype=bool)
        rep.notes.append(
            "no surface models supplied, so change cannot be separated from re-surfacing. "
            "A repainted roof and a new floor look identical without height."
        )

    if cfg.shadow_guard:
        # a pixel that got much darker in every band with unchanged texture is a shadow
        b = _grey(before_ortho)
        a = _grey(after_ortho)
        shadow = (a < b * 0.55) & (ncc > 0.35)
        spectral &= ~shadow
        rep.notes.append(f"{float(shadow.mean()) * 100:.2f}% of pixels suppressed as shadow")

    votes = spectral.astype(np.int8) + structural.astype(np.int8) + geometric.astype(np.int8)
    flagged = votes >= cfg.require_agreeing_signals
    rep.flagged_pixel_fraction = float(flagged.mean())

    flagged = ndimage.binary_opening(flagged, np.ones((3, 3)))
    flagged = ndimage.binary_closing(flagged, np.ones((5, 5)))
    labels, n = ndimage.label(flagged)

    cell_area = cfg.cell_size_m ** 2
    regions: list[RasterChangeRegion] = []
    slices = ndimage.find_objects(labels)
    for i in range(1, n + 1):
        sl = slices[i - 1]
        if sl is None:
            continue
        sub = labels[sl] == i
        area = float(sub.sum()) * cell_area
        if area < cfg.min_region_m2:
            labels[sl][sub] = 0
            continue
        perim = _perimeter(sub) * cfg.cell_size_m
        compact = (4 * math.pi * area / (perim ** 2)) if perim > 0 else 0.0
        if compact < cfg.min_compactness:
            labels[sl][sub] = 0
            continue

        dh_sub = dh[sl][sub]
        mean_dh = float(np.mean(dh_sub)) if dh_sub.size else 0.0
        max_dh = float(np.max(np.abs(dh_sub))) if dh_sub.size else 0.0
        sp = float(np.mean(spectral[sl][sub]))
        st = float(np.mean(structural[sl][sub]))
        ge = float(np.mean(geometric[sl][sub]))

        if mean_dh >= cfg.height_threshold_m:
            ctype = ChangeType.NEW_CONSTRUCTION
        elif mean_dh <= -cfg.height_threshold_m:
            ctype = ChangeType.DEMOLITION
        else:
            ctype = ChangeType.RECLASSIFICATION

        conf = min(0.98, 0.30 * sp + 0.25 * st + 0.45 * ge)
        regions.append(RasterChangeRegion(
            region_id=i, area_m2=area, change_type=ctype,
            mean_height_delta_m=mean_dh, max_height_delta_m=max_dh,
            spectral_score=sp, structural_score=st, geometric_score=ge,
            confidence=conf, compactness=compact,
        ))

    if transform is not None and regions:
        polys = _polygonise(labels, transform)
        for r in regions:
            r.geometry = polys.get(r.region_id)

    rep.n_regions = len(regions)
    rep.total_change_area_m2 = sum(r.area_m2 for r in regions)
    rep.new_construction_m2 = sum(r.area_m2 for r in regions
                                  if r.change_type is ChangeType.NEW_CONSTRUCTION)
    rep.demolition_m2 = sum(r.area_m2 for r in regions
                            if r.change_type is ChangeType.DEMOLITION)
    return labels, regions, rep


# --------------------------------------------------------------------------------------


def _as_bands(a: np.ndarray) -> np.ndarray:
    if a.ndim == 2:
        return a[None, ...]
    if a.shape[0] <= 4:
        return a
    return np.moveaxis(a, -1, 0)


def _grey(a: np.ndarray) -> np.ndarray:
    b = _as_bands(a).astype(np.float32)
    if b.shape[0] >= 3:
        return 0.2126 * b[0] + 0.7152 * b[1] + 0.0722 * b[2]
    return b[0]


def _perimeter(mask: np.ndarray) -> int:
    p = int(np.abs(np.diff(mask.astype(np.int8), axis=0)).sum())
    p += int(np.abs(np.diff(mask.astype(np.int8), axis=1)).sum())
    p += int(mask[0].sum() + mask[-1].sum() + mask[:, 0].sum() + mask[:, -1].sum())
    return p


def _polygonise(labels: np.ndarray, transform) -> dict[int, Any]:
    from rasterio.features import shapes as rio_shapes
    from shapely.geometry import shape as shp_shape

    out: dict[int, Any] = {}
    for geom, value in rio_shapes(labels.astype(np.int32), mask=labels > 0,
                                  transform=transform):
        v = int(value)
        g = shp_shape(geom).simplify(0.4, preserve_topology=True)
        out[v] = out[v].union(g) if v in out else g
    return out


def validate_against_null(before: np.ndarray, after: np.ndarray, *,
                          config: RasterChangeConfig | None = None) -> dict[str, Any]:
    """Run the detector on two rasters known to contain no real change.

    Two independent photogrammetric reconstructions of the *same* flight are the ideal null
    test: every difference between them is a processing artefact, so anything the detector
    flags is by construction a false positive. Running this before trusting any real change
    map is the difference between a system with a measured false-positive rate and one with
    an unknown one.
    """
    cfg = config or RasterChangeConfig()
    _, regions, rep = detect(before, after, config=cfg)
    total_px = _grey(before).size
    return {
        "false_positive_regions": len(regions),
        "false_positive_area_m2": round(sum(r.area_m2 for r in regions), 1),
        "flagged_pixel_fraction": round(rep.flagged_pixel_fraction, 6),
        "scene_area_m2": round(total_px * cfg.cell_size_m ** 2, 1),
        "false_positive_rate_pct": round(
            100.0 * sum(r.area_m2 for r in regions)
            / max(total_px * cfg.cell_size_m ** 2, 1e-9), 4),
        "interpretation": (
            "Both inputs depict the same ground at the same instant, so every flagged "
            "region is a false positive attributable to reconstruction differences. This "
            "figure is the detector's noise floor on this sensor and terrain."
        ),
    }
