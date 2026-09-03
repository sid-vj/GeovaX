"""Positional accuracy assessment against ground truth.

Every claim the platform makes about a dataset's reliability should be measurable. This
module measures them, using the statistics the surveying profession actually uses rather
than a generic RMSE:

* **RMSE_x, RMSE_y, RMSE_r** — root-mean-square error in each axis and radially.
* **CE90 / CE95** — circular error at 90% and 95%, the standard horizontal accuracy
  statement in mapping specifications (and the form NAKSHA's tolerance is written in).
* **LE90** — linear error at 90% for the vertical component.
* **NSSDA** — the US National Standard for Spatial Data Accuracy statement, which is
  RMSE_r x 1.7308, still the most widely quoted single figure.
* **Systematic bias** — the mean offset vector, separated from random scatter. This is the
  most actionable output: a 1.4 m north-east bias is a fixable datum problem, whereas 1.4 m
  of random scatter is a re-survey.

The module also implements the check that matters most and is most often skipped:
**is the residual distribution actually random?** A dataset whose residuals are spatially
correlated has a systematic distortion that no single accuracy figure describes, and
reporting one CE90 for it is misleading. Moran's I on the residuals detects that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


@dataclass
class CheckPoint:
    """A ground-truth observation paired with the dataset position being tested."""

    point_id: str
    truth_x: float
    truth_y: float
    test_x: float
    test_y: float
    truth_z: float | None = None
    test_z: float | None = None
    truth_sigma_m: float = 0.05

    @property
    def dx(self) -> float:
        return self.test_x - self.truth_x

    @property
    def dy(self) -> float:
        return self.test_y - self.truth_y

    @property
    def dr(self) -> float:
        return math.hypot(self.dx, self.dy)

    @property
    def dz(self) -> float | None:
        if self.truth_z is None or self.test_z is None:
            return None
        return self.test_z - self.truth_z


@dataclass
class AccuracyReport:
    dataset_id: str
    n: int = 0
    rmse_x: float = float("nan")
    rmse_y: float = float("nan")
    rmse_r: float = float("nan")
    ce90: float = float("nan")
    ce95: float = float("nan")
    le90: float | None = None
    nssda_95: float = float("nan")
    bias_x: float = 0.0
    bias_y: float = 0.0
    bias_magnitude: float = 0.0
    bias_bearing_deg: float = 0.0
    scatter_after_bias_removal: float = float("nan")
    morans_i: float | None = None
    spatial_correlation_note: str = ""
    outliers: list[str] = field(default_factory=list)
    meets_specification: bool | None = None
    specification_m: float | None = None

    def summary(self) -> str:
        spec = ""
        if self.specification_m is not None:
            spec = (f"; specification {self.specification_m:.2f} m CE90 — "
                    f"{'MET' if self.meets_specification else 'NOT MET'}")
        return (
            f"{self.dataset_id}: n={self.n}, RMSE_r {self.rmse_r:.3f} m, "
            f"CE90 {self.ce90:.3f} m, NSSDA95 {self.nssda_95:.3f} m, "
            f"systematic bias {self.bias_magnitude:.3f} m at {self.bias_bearing_deg:.0f}°, "
            f"residual scatter after bias removal {self.scatter_after_bias_removal:.3f} m"
            + spec
        )

    def interpretation(self) -> str:
        if self.n < 20:
            return ("Too few check points for a defensible accuracy statement. Mapping "
                    "specifications generally require at least 20 well-distributed points.")
        if self.bias_magnitude > 2 * self.scatter_after_bias_removal and self.bias_magnitude > 0.3:
            return (
                f"The error is dominated by a systematic {self.bias_magnitude:.2f} m shift on "
                f"a bearing of {self.bias_bearing_deg:.0f}°, not by random scatter. This is "
                f"almost always a datum, projection or control problem and is correctable in "
                f"software — applying the shift would reduce the error to "
                f"{self.scatter_after_bias_removal:.2f} m without any new field work."
            )
        if self.morans_i is not None and self.morans_i > 0.3:
            return (
                f"Residuals are spatially correlated (Moran's I = {self.morans_i:.2f}), so a "
                f"single accuracy figure understates the error in some parts of the area and "
                f"overstates it in others. Local rubber-sheeting against control is indicated "
                f"rather than a global transformation."
            )
        return (
            f"Errors look random with no dominant systematic component. The dataset's real "
            f"accuracy is about {self.ce90:.2f} m at 90% confidence; improving it requires "
            f"re-observation, not reprocessing."
        )


def assess(dataset_id: str, points: Sequence[CheckPoint], *,
           specification_ce90_m: float | None = None,
           outlier_sigma: float = 3.0) -> AccuracyReport:
    rep = AccuracyReport(dataset_id=dataset_id, n=len(points),
                         specification_m=specification_ce90_m)
    if not points:
        return rep

    dx = np.array([p.dx for p in points], dtype=float)
    dy = np.array([p.dy for p in points], dtype=float)
    dr = np.hypot(dx, dy)

    # blunder rejection before computing statistics: one mis-identified check point
    # inflates RMSE far more than it inflates the median, and mapping standards require
    # blunders to be removed and reported, not averaged in
    if len(points) >= 10:
        med = float(np.median(dr))
        mad = float(np.median(np.abs(dr - med))) or 1e-9
        robust_sigma = 1.4826 * mad
        keep = dr <= med + outlier_sigma * robust_sigma
        rep.outliers = [p.point_id for p, k in zip(points, keep) if not k]
        dx, dy, dr = dx[keep], dy[keep], dr[keep]
        rep.n = int(keep.sum())

    if rep.n == 0:
        return rep

    rep.rmse_x = float(np.sqrt(np.mean(dx ** 2)))
    rep.rmse_y = float(np.sqrt(np.mean(dy ** 2)))
    rep.rmse_r = float(np.sqrt(np.mean(dr ** 2)))
    rep.ce90 = float(np.percentile(dr, 90))
    rep.ce95 = float(np.percentile(dr, 95))
    rep.nssda_95 = rep.rmse_r * 1.7308

    rep.bias_x = float(np.mean(dx))
    rep.bias_y = float(np.mean(dy))
    rep.bias_magnitude = math.hypot(rep.bias_x, rep.bias_y)
    rep.bias_bearing_deg = (math.degrees(math.atan2(rep.bias_x, rep.bias_y)) + 360.0) % 360.0
    rep.scatter_after_bias_removal = float(
        np.sqrt(np.mean((dx - rep.bias_x) ** 2 + (dy - rep.bias_y) ** 2)))

    dz = [p.dz for p in points if p.dz is not None]
    if dz:
        rep.le90 = float(np.percentile(np.abs(np.array(dz)), 90))

    if specification_ce90_m is not None:
        rep.meets_specification = rep.ce90 <= specification_ce90_m

    kept = [p for p in points if p.point_id not in set(rep.outliers)]
    if len(kept) >= 12:
        rep.morans_i = morans_i(
            np.array([p.truth_x for p in kept]),
            np.array([p.truth_y for p in kept]),
            np.hypot(np.array([p.dx for p in kept]) - rep.bias_x,
                     np.array([p.dy for p in kept]) - rep.bias_y),
        )
        rep.spatial_correlation_note = (
            "residuals are spatially clustered — a global transformation will not fix this"
            if (rep.morans_i or 0) > 0.3 else "residuals show no significant spatial pattern"
        )
    return rep


def morans_i(x: np.ndarray, y: np.ndarray, values: np.ndarray, *, k: int = 6) -> float:
    """Moran's I over a k-nearest-neighbour weight matrix.

    Answers: are nearby residuals similar? Positive I means the error is a spatially
    varying distortion; near zero means it is noise. That distinction decides whether the
    fix is a transformation or a re-survey, so it is worth the twenty lines.
    """
    n = len(values)
    if n < 5:
        return float("nan")
    pts = np.column_stack([x, y])
    d = np.sqrt(((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1))
    np.fill_diagonal(d, np.inf)
    kk = min(k, n - 1)
    idx = np.argsort(d, axis=1)[:, :kk]
    w = np.zeros((n, n))
    for i in range(n):
        w[i, idx[i]] = 1.0
    w = (w + w.T) / 2.0
    s0 = w.sum()
    if s0 == 0:
        return float("nan")
    z = values - values.mean()
    denom = (z ** 2).sum()
    if denom == 0:
        return float("nan")
    num = float((w * np.outer(z, z)).sum())
    return float((n / s0) * (num / denom))


# --------------------------------------------------------------------------------------
# building the check points
# --------------------------------------------------------------------------------------


def check_points_from_matches(matches, left_geoms: dict, right_geoms: dict, *,
                              min_probability: float = 0.85,
                              truth_sigma_m: float = 0.05) -> list[CheckPoint]:
    """Derive check points from high-confidence matches between a test layer and a
    reference layer treated as truth.

    This is how the platform measures a dataset without a field campaign: take the layer
    with the best declared and demonstrated accuracy as the reference, take only matches
    the model is very sure about, and compare centroids. It is not a substitute for
    surveyed check points — the reference has its own error, which propagates — but it
    turns an unmeasurable dataset into a measured one at zero marginal cost, and the
    resulting bias estimate is correct even when the absolute scale is not.
    """
    out: list[CheckPoint] = []
    for m in matches:
        if getattr(m, "probability", 0.0) < min_probability:
            continue
        lg = left_geoms.get(m.left_id)
        rg = right_geoms.get(m.right_id)
        if lg is None or rg is None:
            continue
        lc, rc = lg.centroid, rg.centroid
        out.append(CheckPoint(
            point_id=f"{m.left_id}|{m.right_id}",
            truth_x=lc.x, truth_y=lc.y,
            test_x=rc.x, test_y=rc.y,
            truth_sigma_m=truth_sigma_m,
        ))
    return out


@dataclass
class CompletenessReport:
    """Commission and omission against a reference layer — the other half of quality."""

    dataset_id: str
    reference_id: str
    n_reference: int
    n_test: int
    matched: int
    omission: int
    """Reference features with no counterpart: what the dataset is missing."""
    commission: int
    """Test features with no counterpart: what the dataset has invented or what the
    reference is missing. Which of the two it is cannot be decided from counts alone."""

    @property
    def completeness(self) -> float:
        return self.matched / self.n_reference if self.n_reference else 0.0

    @property
    def correctness(self) -> float:
        return self.matched / self.n_test if self.n_test else 0.0

    @property
    def f1(self) -> float:
        p, r = self.correctness, self.completeness
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    def summary(self) -> str:
        return (
            f"{self.dataset_id} vs {self.reference_id}: completeness "
            f"{self.completeness * 100:.1f}% ({self.omission:,} omitted), correctness "
            f"{self.correctness * 100:.1f}% ({self.commission:,} unmatched), F1 {self.f1:.3f}"
        )


def assess_completeness(dataset_id: str, reference_id: str, assignment_report
                        ) -> CompletenessReport:
    n_ref = assignment_report.cardinality_counts.get("1:1", 0) + len(
        assignment_report.unmatched_left)
    n_test = assignment_report.cardinality_counts.get("1:1", 0) + len(
        assignment_report.unmatched_right)
    return CompletenessReport(
        dataset_id=dataset_id,
        reference_id=reference_id,
        n_reference=n_ref,
        n_test=n_test,
        matched=assignment_report.n_accepted,
        omission=len(assignment_report.unmatched_left),
        commission=len(assignment_report.unmatched_right),
    )
