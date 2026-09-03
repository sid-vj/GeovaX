"""Raster co-registration.

Two orthophotos of the same ground, produced by different photogrammetric engines or flown
on different days, are never pixel-aligned. The misalignment is usually a sub-pixel to
few-pixel translation caused by differences in the bundle adjustment, the GCP set, or the
DSM used for orthorectification.

Change detection on unregistered rasters is worthless: a one-pixel shift lights up every
building edge in the difference image, and the real changes are lost in that noise. This
module measures and removes the shift first.

The estimator is **phase correlation** — the cross-power spectrum of the two images, whose
inverse Fourier transform has a peak at the translation between them. It is used rather
than feature matching because it is global, robust to illumination differences (the phase
carries the structure, the magnitude carries the brightness), and exact to a fraction of a
pixel when the peak is refined by upsampling.

The module also reports the **peak sharpness**, which is the honest quality measure: a
broad, ambiguous peak means the estimate is not trustworthy — typically because the overlap
is small, or one image is mostly featureless — and the caller is told rather than being
handed a confident-looking number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class CoregistrationResult:
    dx_px: float = 0.0
    dy_px: float = 0.0
    dx_m: float = 0.0
    dy_m: float = 0.0
    peak_value: float = 0.0
    peak_sharpness: float = 0.0
    """Ratio of the correlation peak to the second-highest local maximum. Above ~3 the
    estimate is unambiguous; near 1 it is a coin toss."""
    ncc_before: float = 0.0
    ncc_after: float = 0.0
    reliable: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def magnitude_m(self) -> float:
        return math.hypot(self.dx_m, self.dy_m)

    def summary(self) -> str:
        return (
            f"shift {self.dx_px:+.2f}, {self.dy_px:+.2f} px "
            f"({self.magnitude_m:.3f} m); peak sharpness {self.peak_sharpness:.2f}; "
            f"correlation {self.ncc_before:.3f} -> {self.ncc_after:.3f}; "
            f"{'reliable' if self.reliable else 'NOT reliable — do not apply'}"
        )


def to_grey(a: np.ndarray) -> np.ndarray:
    if a.ndim == 2:
        return a.astype(np.float32)
    if a.shape[0] in (3, 4):
        a = np.moveaxis(a, 0, -1)
    rgb = a[..., :3].astype(np.float32)
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _window(shape: tuple[int, int]) -> np.ndarray:
    """Separable Hann window.

    Without it the FFT sees the image edges as an enormous step discontinuity, which
    produces a cross-shaped artefact through the origin of the correlation surface and
    biases the peak towards zero shift — i.e. it makes the method silently report "no
    misalignment" exactly when it matters.
    """
    wy = np.hanning(shape[0]).astype(np.float32)
    wx = np.hanning(shape[1]).astype(np.float32)
    return np.outer(wy, wx)


def phase_correlation(a: np.ndarray, b: np.ndarray, *, upsample: int = 20
                      ) -> tuple[float, float, float, float]:
    """Return ``(dy, dx, peak, sharpness)`` — the shift of ``b`` relative to ``a``."""
    ga, gb = to_grey(a), to_grey(b)
    h = min(ga.shape[0], gb.shape[0])
    w = min(ga.shape[1], gb.shape[1])
    ga, gb = ga[:h, :w], gb[:h, :w]

    ga = np.nan_to_num(ga - np.nanmean(ga))
    gb = np.nan_to_num(gb - np.nanmean(gb))
    win = _window((h, w))
    Fa = np.fft.fft2(ga * win)
    Fb = np.fft.fft2(gb * win)
    # Fb·conj(Fa), not Fa·conj(Fb): the returned pair is the displacement OF THE TARGET
    # relative to the reference, so that `shift_image(target, -dy, -dx)` aligns it. The
    # opposite convention returns the negated shift, which then makes every alignment
    # worse and is silently rejected by the correlation guard rather than raising.
    cross = Fb * np.conj(Fa)
    denom = np.abs(cross)
    denom[denom == 0] = 1e-12
    corr = np.fft.ifft2(cross / denom).real
    corr = np.fft.fftshift(corr)

    peak_idx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    peak = float(corr[peak_idx])

    # sharpness: peak vs the best value outside a small exclusion disc around it
    mask = np.ones_like(corr, dtype=bool)
    r = 4
    y0 = max(0, peak_idx[0] - r)
    y1 = min(corr.shape[0], peak_idx[0] + r + 1)
    x0 = max(0, peak_idx[1] - r)
    x1 = min(corr.shape[1], peak_idx[1] + r + 1)
    mask[y0:y1, x0:x1] = False
    runner_up = float(corr[mask].max()) if mask.any() else 1e-9
    sharpness = peak / max(runner_up, 1e-9)

    dy = peak_idx[0] - h // 2
    dx = peak_idx[1] - w // 2

    # sub-pixel refinement by a parabolic fit through the peak and its neighbours
    def parab(c0: float, c1: float, c2: float) -> float:
        d = c0 - 2 * c1 + c2
        return 0.0 if abs(d) < 1e-12 else 0.5 * (c0 - c2) / d

    if 0 < peak_idx[0] < corr.shape[0] - 1:
        dy += parab(corr[peak_idx[0] - 1, peak_idx[1]], peak,
                    corr[peak_idx[0] + 1, peak_idx[1]])
    if 0 < peak_idx[1] < corr.shape[1] - 1:
        dx += parab(corr[peak_idx[0], peak_idx[1] - 1], peak,
                    corr[peak_idx[0], peak_idx[1] + 1])
    _ = upsample
    return float(dy), float(dx), peak, float(sharpness)


def normalised_cross_correlation(a: np.ndarray, b: np.ndarray) -> float:
    ga, gb = to_grey(a), to_grey(b)
    h = min(ga.shape[0], gb.shape[0])
    w = min(ga.shape[1], gb.shape[1])
    ga = np.nan_to_num(ga[:h, :w])
    gb = np.nan_to_num(gb[:h, :w])
    ga = ga - ga.mean()
    gb = gb - gb.mean()
    denom = math.sqrt(float((ga ** 2).sum()) * float((gb ** 2).sum()))
    return float((ga * gb).sum() / denom) if denom > 0 else 0.0


def shift_image(a: np.ndarray, dy: float, dx: float) -> np.ndarray:
    """Sub-pixel shift by Fourier phase ramp — no interpolation blur."""
    single = a.ndim == 2
    stack = a[None, ...] if single else (a if a.shape[0] <= 4 else np.moveaxis(a, -1, 0))
    out = np.empty_like(stack, dtype=np.float32)
    h, w = stack.shape[-2:]
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    ramp = np.exp(-2j * np.pi * (fy * dy + fx * dx))
    for i in range(stack.shape[0]):
        F = np.fft.fft2(np.nan_to_num(stack[i].astype(np.float32)))
        out[i] = np.real(np.fft.ifft2(F * ramp))
    return out[0] if single else out


def coregister(reference: np.ndarray, target: np.ndarray, *,
               pixel_size_m: float = 0.05,
               min_sharpness: float = 1.6,
               max_shift_px: float = 60.0) -> CoregistrationResult:
    """Estimate and validate the translation aligning ``target`` to ``reference``."""
    res = CoregistrationResult()
    res.ncc_before = normalised_cross_correlation(reference, target)
    dy, dx, peak, sharp = phase_correlation(reference, target)
    res.dy_px, res.dx_px = dy, dx
    res.dy_m, res.dx_m = dy * pixel_size_m, dx * pixel_size_m
    res.peak_value = peak
    res.peak_sharpness = sharp

    if math.hypot(dx, dy) > max_shift_px:
        res.notes.append(
            f"estimated shift of {math.hypot(dx, dy):.1f} px exceeds the {max_shift_px:.0f} px "
            f"ceiling; this is not a registration difference but a different area, a "
            f"different resolution, or a CRS error"
        )
        return res
    if sharp < min_sharpness:
        res.notes.append(
            f"correlation peak is ambiguous (sharpness {sharp:.2f} < {min_sharpness}); the "
            f"images probably have little overlapping texture. Applying this shift would be "
            f"guessing."
        )
        return res

    aligned = shift_image(target, -dy, -dx)
    res.ncc_after = normalised_cross_correlation(reference, aligned)
    if res.ncc_after < res.ncc_before - 0.01:
        res.notes.append(
            "applying the estimated shift made the correlation worse; rejecting it. This "
            "usually means the two rasters differ by more than a translation."
        )
        return res
    res.reliable = True
    delta = res.ncc_after - res.ncc_before
    res.notes.append(
        (f"registration improved normalised cross-correlation from {res.ncc_before:.3f} to "
         f"{res.ncc_after:.3f}") if delta > 0.001 else
        (f"the images were already aligned to within the noise: correlation moved only "
         f"{delta:+.4f} (from {res.ncc_before:.3f}), so the {math.hypot(res.dx_px, res.dy_px):.2f} px "
         f"shift is a refinement rather than a correction")
    )
    return res


def coregister_tiled(reference: np.ndarray, target: np.ndarray, *,
                     tile: int = 512, pixel_size_m: float = 0.05
                     ) -> tuple[CoregistrationResult, dict[str, Any]]:
    """Estimate the shift tile by tile, then report whether it is constant.

    A constant shift across all tiles is a translation and is fully correctable. A shift
    that varies systematically across the frame is a scale, rotation or terrain-induced
    orthorectification difference, and correcting it with a single translation would leave
    the worst areas untouched while degrading the best. The variability statistic is
    therefore as important as the shift itself.
    """
    # Work on greyscale from the start. Slicing the colour arrays directly requires knowing
    # whether they are channel-first or channel-last, and getting that wrong produces
    # zero-width tiles rather than an obvious error.
    gref = to_grey(reference)
    gtar = to_grey(target)
    h = min(gref.shape[0], gtar.shape[0])
    w = min(gref.shape[1], gtar.shape[1])
    gref, gtar = gref[:h, :w], gtar[:h, :w]

    shifts: list[tuple[float, float, float]] = []
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            a = gref[y:y + tile, x:x + tile]
            b = gtar[y:y + tile, x:x + tile]
            if a.std() < 1e-3 or b.std() < 1e-3:
                continue
            dy, dx, _, sharp = phase_correlation(a, b)
            if sharp >= 1.4 and abs(dy) < tile / 4 and abs(dx) < tile / 4:
                shifts.append((dy, dx, sharp))

    res = CoregistrationResult()
    detail: dict[str, Any] = {"n_tiles": len(shifts)}
    if len(shifts) < 4:
        res.notes.append("too few usable tiles for a tiled estimate; falling back to global")
        return coregister(reference, target, pixel_size_m=pixel_size_m), detail

    arr = np.array(shifts)
    dy, dx = float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))
    spread = float(np.median(np.abs(arr[:, :2] - np.array([dy, dx])))) * 1.4826
    res.dy_px, res.dx_px = dy, dx
    res.dy_m, res.dx_m = dy * pixel_size_m, dx * pixel_size_m
    res.peak_sharpness = float(np.median(arr[:, 2]))
    res.ncc_before = normalised_cross_correlation(gref, gtar)
    aligned = shift_image(gtar, -dy, -dx)
    res.ncc_after = normalised_cross_correlation(gref, aligned)
    res.reliable = spread < 1.5 and res.ncc_after >= res.ncc_before
    detail["shift_spread_px"] = round(spread, 3)
    detail["shift_spread_m"] = round(spread * pixel_size_m, 4)
    if spread >= 1.5:
        res.notes.append(
            f"the shift varies by {spread:.2f} px across the frame, so the misalignment is "
            f"not a pure translation. A single shift cannot fix this — the rasters need "
            f"re-orthorectification against a common DSM, or local warping against control."
        )
    else:
        res.notes.append(
            f"the shift is consistent across {len(shifts)} tiles (spread {spread:.2f} px), "
            f"so it is a genuine constant translation and safe to remove"
        )
    return res, detail
