"""Ground-control-based georeferencing: Helmert, affine, polynomial and thin-plate spline.

A correct datum transformation puts a legacy cadastral sheet in roughly the right place.
It does not make it *fit*. Scanned FMB sheets carry non-uniform distortion from paper
shrinkage, differential drying, uneven scanning and the original draughtsman's error, and
that distortion is spatially correlated but not globally describable. The only honest way
to remove it is to observe the same physical points in both frames — corner stones,
building corners fixed by DGPS, CORS-observed control — and warp locally.

The estimators here form a ladder of increasing flexibility, and the class deliberately
makes it hard to skip up the ladder without justification, because over-flexible warps hide
blunders instead of exposing them:

* **Helmert (4-parameter similarity)** — translation, rotation, uniform scale. 2 GCPs
  minimum. The right model when the sheet is geometrically sound and only misplaced.
* **Affine (6-parameter)** — adds differential scale and shear. 3 GCPs. The right model for
  a sheet that dried anisotropically.
* **Polynomial order 2/3** — smooth regional distortion. 6/10 GCPs.
* **Thin-plate spline** — exact interpolation through every GCP, minimum bending energy
  elsewhere. The right model for genuinely local distortion, and the wrong model if the
  GCPs contain a blunder, because it will happily contort the sheet to honour it.

Every fit reports per-point residuals, RMSE and a leave-one-out cross-validated RMSE. The
LOO number is the one that matters: an exact interpolator always reports zero residuals,
and reporting that as "accuracy" is the most common self-deception in georeferencing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np

Model = Literal["helmert", "affine", "polynomial2", "polynomial3", "tps"]


@dataclass(frozen=True)
class GroundControlPoint:
    """One observation of the same physical point in two frames."""

    gcp_id: str
    source_x: float
    source_y: float
    target_x: float
    target_y: float
    sigma_m: float = 0.05
    """1-sigma uncertainty of the target observation. DGPS ~0.05 m, CORS static ~0.01 m."""
    description: str = ""
    method: str = "gnss"  # "gnss" | "cors" | "total_station" | "ori_digitised"

    @property
    def weight(self) -> float:
        return 1.0 / max(self.sigma_m, 1e-3) ** 2


@dataclass
class FitReport:
    model: Model
    n_points: int
    params: dict[str, float] = field(default_factory=dict)
    residuals: dict[str, float] = field(default_factory=dict)
    rmse: float = 0.0
    max_residual: float = 0.0
    loo_rmse: float | None = None
    blunders: list[str] = field(default_factory=list)
    condition_number: float | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        loo = f", LOO RMSE {self.loo_rmse:.3f} m" if self.loo_rmse is not None else ""
        b = f", {len(self.blunders)} blunder(s) flagged" if self.blunders else ""
        return (
            f"{self.model} on {self.n_points} GCPs: RMSE {self.rmse:.3f} m, "
            f"max {self.max_residual:.3f} m{loo}{b}"
        )

    @property
    def accepted(self) -> bool:
        """Whether the fit is good enough to publish without human sign-off.

        The threshold is the NAKSHA urban cadastral specification: a residual RMSE within
        0.5 m and no single residual beyond 1.5 m.
        """
        return self.rmse <= 0.5 and self.max_residual <= 1.5 and not self.blunders


class GeoReferencer:
    """Fit and apply a transformation estimated from ground control points."""

    def __init__(self, model: Model = "affine") -> None:
        self.model: Model = model
        self._fitted = False
        self._A: np.ndarray | None = None          # linear/polynomial coefficients
        self._tps: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self.report: FitReport | None = None

    # -- fitting ------------------------------------------------------------------

    def fit(self, gcps: Sequence[GroundControlPoint], *,
            reject_blunders: bool = True, blunder_sigma: float = 3.0) -> FitReport:
        pts = list(gcps)
        self._require_minimum(len(pts))

        if reject_blunders and len(pts) > self._minimum() + 1:
            pts, dropped = self._reject(pts, blunder_sigma)
        else:
            dropped = []

        src = np.array([[g.source_x, g.source_y] for g in pts], dtype=float)
        dst = np.array([[g.target_x, g.target_y] for g in pts], dtype=float)
        w = np.array([g.weight for g in pts], dtype=float)

        if self.model == "tps":
            self._tps = _fit_tps(src, dst)
            params: dict[str, float] = {"n_centres": float(len(pts))}
            cond = None
        elif self.model == "helmert":
            self._A = _fit_helmert(src, dst, w)
            cond = float(np.linalg.cond(np.column_stack([np.ones(len(src)), src])))
            params = self._named_params(self._A)
        else:
            design = self._design(src)
            wsqrt = np.sqrt(w)[:, None]
            sol, *_ = np.linalg.lstsq(design * wsqrt, dst * wsqrt, rcond=None)
            self._A = sol
            cond = float(np.linalg.cond(design))
            params = self._named_params(sol)

        self._fitted = True

        pred = self.apply_many(src[:, 0], src[:, 1])
        res = np.hypot(pred[0] - dst[:, 0], pred[1] - dst[:, 1])
        report = FitReport(
            model=self.model,
            n_points=len(pts),
            params=params,
            residuals={g.gcp_id: float(r) for g, r in zip(pts, res)},
            rmse=float(np.sqrt(np.mean(res ** 2))) if len(res) else 0.0,
            max_residual=float(res.max()) if len(res) else 0.0,
            blunders=dropped,
            condition_number=cond,
        )
        if len(pts) > self._minimum():
            report.loo_rmse = self._loo_rmse(pts)
        if cond is not None and cond > 1e8:
            report.notes.append(
                "Design matrix is ill-conditioned; GCPs are probably collinear or clustered. "
                "Add control away from the existing points before trusting this fit."
            )
        if self.model == "tps":
            report.notes.append(
                "Thin-plate spline interpolates exactly, so in-sample residuals are ~0 by "
                "construction. Judge this fit only by the leave-one-out RMSE."
            )
        self.report = report
        return report

    # -- applying -----------------------------------------------------------------

    def apply(self, x: float, y: float) -> tuple[float, float]:
        xs, ys = self.apply_many(np.array([x]), np.array([y]))
        return float(xs[0]), float(ys[0])

    def apply_many(self, xs: np.ndarray | Sequence[float],
                   ys: np.ndarray | Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
        if not self._fitted:
            raise RuntimeError("GeoReferencer.fit() must be called before apply()")
        src = np.column_stack([np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)])
        if self.model == "tps":
            assert self._tps is not None
            out = _apply_tps(self._tps, src)
        else:
            assert self._A is not None
            out = self._design(src) @ self._A
        return out[:, 0], out[:, 1]

    def apply_geometry(self, geom):
        """Warp a shapely geometry through the fitted transformation."""
        from shapely.ops import transform as shp_transform

        def _fn(xx, yy, zz=None):
            nx, ny = self.apply_many(np.asarray(xx), np.asarray(yy))
            return nx, ny

        return shp_transform(_fn, geom)

    # -- internals ----------------------------------------------------------------

    def _minimum(self) -> int:
        return {"helmert": 2, "affine": 3, "polynomial2": 6, "polynomial3": 10, "tps": 3}[self.model]

    def _require_minimum(self, n: int) -> None:
        m = self._minimum()
        if n < m:
            raise ValueError(
                f"{self.model} needs at least {m} ground control points, got {n}. "
                f"Use a simpler model rather than fitting a flexible one to too little control."
            )

    def _design(self, src: np.ndarray) -> np.ndarray:
        x, y = src[:, 0], src[:, 1]
        one = np.ones_like(x)
        if self.model in ("helmert", "affine"):
            # Both use the same 3-term design. They differ in how the coefficients are
            # *estimated*: affine solves it unconstrained, while helmert is fitted by
            # `_fit_helmert`, which enforces the similarity constraint exactly rather than
            # producing an affine and hoping it comes out orthogonal.
            return np.column_stack([one, x, y])
        if self.model == "polynomial2":
            return np.column_stack([one, x, y, x * x, x * y, y * y])
        if self.model == "polynomial3":
            return np.column_stack(
                [one, x, y, x * x, x * y, y * y, x ** 3, x * x * y, x * y * y, y ** 3]
            )
        raise ValueError(self.model)

    def _named_params(self, sol: np.ndarray) -> dict[str, float]:
        if self.model in {"helmert", "affine"}:
            (c0x, c0y), (axx, ayx), (axy, ayy) = sol[0], sol[1], sol[2]
            scale_x = math.hypot(axx, ayx)
            scale_y = math.hypot(axy, ayy)
            rot = math.degrees(math.atan2(ayx, axx))
            shear = math.degrees(math.atan2(axy, ayy)) - rot
            return {
                "tx": float(c0x), "ty": float(c0y),
                "scale_x": float(scale_x), "scale_y": float(scale_y),
                "rotation_deg": float(rot), "shear_deg": float(shear),
                "scale_ppm": float((0.5 * (scale_x + scale_y) - 1.0) * 1e6),
            }
        return {f"c{i}": float(v) for i, v in enumerate(sol.ravel())}

    def _reject(self, pts: list[GroundControlPoint], k: float
                ) -> tuple[list[GroundControlPoint], list[str]]:
        """Iterative data snooping: drop points whose residual exceeds k-sigma, refit.

        This is Baarda's classic test in its simplest form. It is essential: a single
        mis-identified GCP silently destroys an entire sheet's georeferencing, and it is
        invisible in the RMSE once the model has enough freedom to absorb it.
        """
        keep = list(pts)
        dropped: list[str] = []
        for _ in range(max(1, len(pts) - self._minimum())):
            probe = GeoReferencer(self.model)
            src = np.array([[g.source_x, g.source_y] for g in keep])
            dst = np.array([[g.target_x, g.target_y] for g in keep])
            if self.model == "tps":
                probe._tps = _fit_tps(src, dst)
            elif self.model == "helmert":
                probe._A = _fit_helmert(src, dst, np.ones(len(src)))
            else:
                probe._A = np.linalg.lstsq(probe._design(src), dst, rcond=None)[0]
            probe._fitted = True
            px, py = probe.apply_many(src[:, 0], src[:, 1])
            res = np.hypot(px - dst[:, 0], py - dst[:, 1])
            sigma = res.std() or 1e-9
            worst = int(np.argmax(res))
            if res[worst] > k * sigma and len(keep) > self._minimum() + 1:
                dropped.append(keep[worst].gcp_id)
                keep.pop(worst)
            else:
                break
        return keep, dropped

    def _loo_rmse(self, pts: list[GroundControlPoint]) -> float:
        errs: list[float] = []
        for i in range(len(pts)):
            subset = pts[:i] + pts[i + 1:]
            if len(subset) < self._minimum():
                continue
            probe = GeoReferencer(self.model)
            src = np.array([[g.source_x, g.source_y] for g in subset])
            dst = np.array([[g.target_x, g.target_y] for g in subset])
            if self.model == "tps":
                probe._tps = _fit_tps(src, dst)
            elif self.model == "helmert":
                probe._A = _fit_helmert(src, dst, np.ones(len(src)))
            else:
                probe._A = np.linalg.lstsq(probe._design(src), dst, rcond=None)[0]
            probe._fitted = True
            px, py = probe.apply(pts[i].source_x, pts[i].source_y)
            errs.append(math.hypot(px - pts[i].target_x, py - pts[i].target_y))
        return float(np.sqrt(np.mean(np.square(errs)))) if errs else float("nan")


# --------------------------------------------------------------------------------------
# constrained similarity (Helmert)
# --------------------------------------------------------------------------------------


def _fit_helmert(src: np.ndarray, dst: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted 4-parameter similarity: translation, rotation, one uniform scale.

    Solved in the complex plane, where a similarity is exactly one multiplication and one
    addition: ``z' = a·z + b``. The weighted least-squares solution for ``a`` and ``b`` is
    then closed-form, and the resulting real 2x2 block is orthogonal *by construction*.

    This matters because an unconstrained affine fitted to control that contains a small
    blunder will happily introduce shear to absorb it, hiding the blunder. A similarity
    cannot shear, so the blunder shows up in the residuals where it belongs.
    """
    z = src[:, 0] + 1j * src[:, 1]
    zp = dst[:, 0] + 1j * dst[:, 1]
    wt = w / w.sum()
    zm = (wt * z).sum()
    zpm = (wt * zp).sum()
    zc = z - zm
    zpc = zp - zpm
    denom = (wt * (zc * np.conj(zc))).sum().real
    a = ((wt * (zpc * np.conj(zc))).sum() / denom) if denom > 0 else 1.0 + 0j
    b = zpm - a * zm
    # express as the [1, x, y] design's coefficient matrix
    return np.array([
        [b.real, b.imag],
        [a.real, a.imag],
        [-a.imag, a.real],
    ])


# --------------------------------------------------------------------------------------
# thin-plate spline
# --------------------------------------------------------------------------------------


def _tps_kernel(r2: np.ndarray) -> np.ndarray:
    """U(r) = r^2 log r, written on squared distances to avoid a sqrt."""
    out = np.zeros_like(r2)
    nz = r2 > 0
    out[nz] = 0.5 * r2[nz] * np.log(r2[nz])
    return out


def _fit_tps(src: np.ndarray, dst: np.ndarray, smoothing: float = 0.0
             ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = src.shape[0]
    d2 = ((src[:, None, :] - src[None, :, :]) ** 2).sum(-1)
    K = _tps_kernel(d2)
    if smoothing:
        K = K + np.eye(n) * smoothing
    P = np.column_stack([np.ones(n), src])
    L = np.zeros((n + 3, n + 3))
    L[:n, :n] = K
    L[:n, n:] = P
    L[n:, :n] = P.T
    Y = np.zeros((n + 3, 2))
    Y[:n] = dst
    sol = np.linalg.lstsq(L, Y, rcond=None)[0]
    return src, sol[:n], sol[n:]


def _apply_tps(model: tuple[np.ndarray, np.ndarray, np.ndarray], pts: np.ndarray) -> np.ndarray:
    centres, W, A = model
    d2 = ((pts[:, None, :] - centres[None, :, :]) ** 2).sum(-1)
    U = _tps_kernel(d2)
    affine = np.column_stack([np.ones(pts.shape[0]), pts]) @ A
    return affine + U @ W


# --------------------------------------------------------------------------------------
# model selection
# --------------------------------------------------------------------------------------


def choose_model(gcps: Sequence[GroundControlPoint]) -> tuple[Model, FitReport, dict[Model, FitReport]]:
    """Fit the whole ladder and pick the simplest model that is not significantly worse.

    Selection is on leave-one-out RMSE with a parsimony margin: a more flexible model must
    beat the simpler one by more than 15% of LOO RMSE to be chosen. This resists the strong
    temptation to reach for a spline and report its zero in-sample residuals as accuracy.
    """
    ladder: list[Model] = ["helmert", "affine", "polynomial2", "polynomial3", "tps"]
    reports: dict[Model, FitReport] = {}
    for m in ladder:
        gr = GeoReferencer(m)
        try:
            reports[m] = gr.fit(gcps)
        except (ValueError, np.linalg.LinAlgError):
            continue
    if not reports:
        raise ValueError("no model could be fitted to the supplied control")

    def score(r: FitReport) -> float:
        v = r.loo_rmse if (r.loo_rmse is not None and math.isfinite(r.loo_rmse)) else r.rmse
        return v

    best: Model = min(reports, key=lambda m: score(reports[m]))
    best_score = score(reports[best])
    for m in ladder:
        if m in reports and score(reports[m]) <= best_score * 1.15:
            return m, reports[m], reports
    return best, reports[best], reports


def synthesise_control_from_matches(
    matches, source_geoms: dict, target_geoms: dict, *, sigma_m: float = 0.5, limit: int = 400
) -> list[GroundControlPoint]:
    """Derive pseudo-GCPs from high-confidence feature matches.

    When no surveyed control exists for a legacy sheet — the normal case — confidently
    matched, well-conditioned features (small compact buildings, road junctions) act as
    control. Their uncertainty is far worse than DGPS, which is why ``sigma_m`` defaults
    to half a metre and why these points are never mixed with real control at equal weight:
    the weighting in ``GroundControlPoint.weight`` does that automatically.
    """
    out: list[GroundControlPoint] = []
    for mp in sorted(matches, key=lambda m: -m.probability)[:limit]:
        sg = source_geoms.get(mp.left_id)
        tg = target_geoms.get(mp.right_id)
        if sg is None or tg is None:
            continue
        sc, tc = sg.centroid, tg.centroid
        out.append(
            GroundControlPoint(
                gcp_id=f"auto:{mp.left_id}->{mp.right_id}",
                source_x=sc.x, source_y=sc.y,
                target_x=tc.x, target_y=tc.y,
                sigma_m=sigma_m / max(mp.probability, 1e-3),
                description="auto-derived from feature match",
                method="ori_digitised",
            )
        )
    return out
