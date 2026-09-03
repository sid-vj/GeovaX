"""The learned spatial matcher.

There is no labelled training set for "is this TNGIS parcel the same parcel as that NCSCM
parcel". Nobody has ever produced one, and waiting for one is how this problem stays
unsolved. So the matcher is trained by **programmatic weak supervision**: a set of
labelling functions vote on the easy cases, a classifier learns the decision surface those
votes imply, and the classifier then generalises to the hard cases the labelling functions
abstain on.

The labelling functions are deliberately high-precision and low-recall:

* ``lf_overlap_anchor`` — mutual nearest neighbours with IoU above 0.80 are the same
  object. At that overlap, with both features being each other's best candidate, the
  alternative explanations have essentially no probability mass.
* ``lf_identity_anchor`` — identical normalised survey number *and* overlapping geometry.
* ``lf_disjoint_negative`` — no intersection and centroid separation beyond five times the
  combined feature radius.
* ``lf_scale_negative`` — an order-of-magnitude area difference with low containment.
* ``lf_competitor_negative`` — a pair where a competitor pair exists with far higher IoU
  for both endpoints.

Only pairs on which the functions agree become training data; contested pairs are exactly
the pairs the model is needed for and are withheld.

The classifier is a histogram gradient-boosted tree. That choice is not arbitrary: the
features are heterogeneous in scale, contain informative missing values, and the decision
surface is strongly non-linear in ``iou`` and ``centroid_distance_norm``. Boosted trees
handle all three natively, train in seconds on a laptop, and — critically for a government
system — can be explained per prediction by feature contribution.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from ..core.models import MatchPair
from .features import FEATURE_NAMES, FeatureExtractor, MatchableFeature

Label = int  # 1 = match, 0 = non-match, -1 = abstain


# --------------------------------------------------------------------------------------
# labelling functions
# --------------------------------------------------------------------------------------


@dataclass
class LabelVote:
    name: str
    label: Label
    reason: str = ""


def lf_overlap_anchor(f: dict[str, float], ctx: dict[str, Any]) -> LabelVote:
    if f["iou"] >= 0.62 and ctx.get("mutual_best"):
        return LabelVote("overlap_anchor", 1, f"IoU {f['iou']:.3f} and mutual best candidate")
    return LabelVote("overlap_anchor", -1)


def lf_containment_anchor(f: dict[str, float], ctx: dict[str, Any]) -> LabelVote:
    """One footprint entirely inside the other, mutually best, with consistent shape.

    This is the case that pure IoU handles badly and that dominates real municipal-versus-
    machine-extraction comparisons: the ML extractor traces the roof, the municipal survey
    traces the plinth, so one is reliably inside the other at an IoU of 0.5-0.7. Treating
    those as non-matches is what makes an IoU threshold under-report agreement by half.
    """
    cont = max(f["containment_left"], f["containment_right"])
    if (cont >= 0.85 and ctx.get("mutual_best") and f["area_ratio"] >= 0.35
            and f["centroid_distance_norm"] <= 0.9):
        return LabelVote("containment_anchor", 1,
                         f"containment {cont:.2f} with mutual best and consistent scale")
    return LabelVote("containment_anchor", -1)


def lf_shadow_negative(f: dict[str, float], ctx: dict[str, Any]) -> LabelVote:
    """A candidate living in the shadow of a decisively better one.

    Deliberately generates *hard* negatives — pairs with real overlap that are still wrong
    — because a training set whose negatives all have zero IoU teaches the model nothing
    except to threshold IoU, which is exactly the degenerate solution to avoid.
    """
    best = max(ctx.get("best_iou_left", 0.0), ctx.get("best_iou_right", 0.0))
    if (not ctx.get("mutual_best") and best >= 0.55 and 0.05 <= f["iou"] <= 0.65
            and f["iou"] <= 0.55 * best):
        return LabelVote("shadow_negative", 0,
                         f"IoU {f['iou']:.2f} against a competitor at {best:.2f}")
    return LabelVote("shadow_negative", -1)


def lf_identity_anchor(f: dict[str, float], ctx: dict[str, Any]) -> LabelVote:
    if f["attr_survey_number"] >= 0.99 and f["iou"] >= 0.30:
        return LabelVote("identity_anchor", 1, "identical survey number with real overlap")
    return LabelVote("identity_anchor", -1)


def lf_disjoint_negative(f: dict[str, float], ctx: dict[str, Any]) -> LabelVote:
    if f["iou"] <= 1e-9 and f["centroid_distance_norm"] > 5.0:
        return LabelVote("disjoint_negative", 0,
                         f"no overlap and {f['centroid_distance_norm']:.1f} radii apart")
    return LabelVote("disjoint_negative", -1)


def lf_scale_negative(f: dict[str, float], ctx: dict[str, Any]) -> LabelVote:
    if f["log_area_ratio"] > 2.3 and max(f["containment_left"], f["containment_right"]) < 0.55:
        return LabelVote("scale_negative", 0, "order-of-magnitude size gap without containment")
    return LabelVote("scale_negative", -1)


def lf_competitor_negative(f: dict[str, float], ctx: dict[str, Any]) -> LabelVote:
    best_l = ctx.get("best_iou_left", 0.0)
    best_r = ctx.get("best_iou_right", 0.0)
    if f["iou"] < 0.15 and best_l > 0.65 and best_r > 0.65:
        return LabelVote("competitor_negative", 0,
                         "both endpoints have a far better partner elsewhere")
    return LabelVote("competitor_negative", -1)


def lf_identity_conflict_negative(f: dict[str, float], ctx: dict[str, Any]) -> LabelVote:
    if f["attr_survey_number"] <= 0.0 and f["iou"] < 0.25 and f["attr_admin_agreement"] < 0.5:
        return LabelVote("identity_conflict_negative", 0,
                         "different survey numbers, little overlap, different admin unit")
    return LabelVote("identity_conflict_negative", -1)


LABELLING_FUNCTIONS = (
    lf_overlap_anchor,
    lf_containment_anchor,
    lf_identity_anchor,
    lf_disjoint_negative,
    lf_scale_negative,
    lf_competitor_negative,
    lf_shadow_negative,
    lf_identity_conflict_negative,
)


def weak_label(f: dict[str, float], ctx: dict[str, Any]) -> tuple[Label, list[LabelVote]]:
    """Combine labelling-function votes. Any disagreement abstains."""
    votes = [fn(f, ctx) for fn in LABELLING_FUNCTIONS]
    active = [v for v in votes if v.label != -1]
    if not active:
        return -1, votes
    labels = {v.label for v in active}
    if len(labels) > 1:
        return -1, votes  # contested: precisely the case the model must decide
    return active[0].label, votes


# --------------------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------------------


@dataclass
class TrainingReport:
    n_candidates: int = 0
    n_labelled: int = 0
    n_positive: int = 0
    n_negative: int = 0
    n_abstained: int = 0
    n_pseudo_positive: int = 0
    n_pseudo_negative: int = 0
    lf_coverage: dict[str, float] = field(default_factory=dict)
    lf_conflict_rate: float = 0.0
    cv_accuracy: float = 0.0
    cv_precision: float = 0.0
    cv_recall: float = 0.0
    cv_auc: float = 0.0
    calibration_error: float = 0.0
    feature_importance: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        pseudo = ""
        if self.n_pseudo_positive or self.n_pseudo_negative:
            pseudo = (f" Self-training added {self.n_pseudo_positive:,} positive and "
                      f"{self.n_pseudo_negative:,} negative pseudo-labels.")
        return (
            f"trained on {self.n_labelled:,} weakly-labelled pairs "
            f"({self.n_positive:,} positive / {self.n_negative:,} negative) drawn from "
            f"{self.n_candidates:,} candidates; {self.n_abstained:,} abstained.{pseudo} "
            f"5-fold CV: accuracy {self.cv_accuracy:.4f}, precision {self.cv_precision:.4f}, "
            f"recall {self.cv_recall:.4f}, AUC {self.cv_auc:.4f}, "
            f"calibration error {self.calibration_error:.4f}"
        )


class SpatialMatcher:
    """Learns and applies a match probability over candidate feature pairs."""

    def __init__(self, *, random_state: int = 20260213) -> None:
        self.random_state = random_state
        self._all_X: np.ndarray | None = None
        self._all_X_key: int | None = None
        self._labelled_index: np.ndarray = np.zeros(0, dtype=int)
        self.model: Any = None
        self.calibrator: Any = None
        self.report = TrainingReport()
        self.extractor = FeatureExtractor()

    # -- training -----------------------------------------------------------------

    def build_training_set(self, pairs: Sequence[tuple[MatchableFeature, MatchableFeature]],
                           feats: Sequence[dict[str, float]]
                           ) -> tuple[np.ndarray, np.ndarray, TrainingReport]:
        ctx = self._pair_context(pairs, feats)
        X: list[np.ndarray] = []
        y: list[int] = []
        rep = TrainingReport(n_candidates=len(pairs))
        coverage: dict[str, int] = {}
        conflicts = 0

        for i, (f, (lf_, rf_)) in enumerate(zip(feats, pairs)):
            c = ctx[i]
            label, votes = weak_label(f, c)
            for v in votes:
                if v.label != -1:
                    coverage[v.name] = coverage.get(v.name, 0) + 1
            active = {v.label for v in votes if v.label != -1}
            if len(active) > 1:
                conflicts += 1
            if label == -1:
                rep.n_abstained += 1
                continue
            X.append(i)
            y.append(label)

        rep.n_labelled = len(y)
        rep.n_positive = int(sum(y))
        rep.n_negative = len(y) - rep.n_positive
        rep.lf_coverage = {k: round(v / max(len(pairs), 1), 4) for k, v in coverage.items()}
        rep.lf_conflict_rate = round(conflicts / max(len(pairs), 1), 4)
        # The design matrix for *all* candidates is built once and cached: the labelled
        # subset, the self-training pool and inference all index into the same array
        # instead of each rebuilding it.
        self._all_X = self.extractor.matrix(feats)
        self._all_X_key = id(feats)
        rows = self._all_X[np.array(X, dtype=int)] if X else np.zeros(
            (0, len(FEATURE_NAMES)), np.float32)
        self._labelled_index = np.array(X, dtype=int)
        return rows, np.array(y, dtype=np.int32), rep

    def fit(self, pairs: Sequence[tuple[MatchableFeature, MatchableFeature]],
            feats: Sequence[dict[str, float]],
            *, self_training_rounds: int = 2,
            pseudo_high: float = 0.92, pseudo_low: float = 0.08) -> TrainingReport:
        """Fit on weak labels, then self-train on confident predictions.

        Weak labelling covers only the pairs the labelling functions are sure about, which
        by design is a small and unrepresentative slice — mostly the easy ones. Training on
        that slice alone yields a model that has never seen an ambiguous pair and therefore
        collapses onto whichever single feature separates the easy cases (here, IoU).

        Self-training fixes it: the model labels the abstained pairs it is now confident
        about, those become additional training data, and the decision surface is refitted
        over the fuller distribution. Only very confident pseudo-labels are admitted, and
        the original weak labels are never overwritten, which bounds the drift that makes
        naive self-training dangerous.
        """
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import StratifiedKFold
        from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

        X, y, rep = self.build_training_set(pairs, feats)
        if len(np.unique(y)) < 2 or len(y) < 40:
            rep.notes.append(
                "not enough separable weak labels to train; falling back to the "
                "deterministic geometric scorer, which is weaker on offset layers"
            )
            self.model = None
            self.report = rep
            return rep

        # class balance: negatives vastly outnumber positives in a candidate set
        pos_w = float(len(y) / (2.0 * max(rep.n_positive, 1)))
        neg_w = float(len(y) / (2.0 * max(rep.n_negative, 1)))
        w = np.where(y == 1, pos_w, neg_w)

        clf = HistGradientBoostingClassifier(
            max_iter=260, learning_rate=0.07, max_depth=None, max_leaf_nodes=31,
            min_samples_leaf=25, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.15, n_iter_no_change=25,
            random_state=self.random_state,
        )

        # cross-validated quality on the weak labels
        try:
            skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=self.random_state)
            accs, precs, recs, aucs = [], [], [], []
            for tr, te in skf.split(X, y):
                m = HistGradientBoostingClassifier(
                    max_iter=180, learning_rate=0.08, min_samples_leaf=25,
                    l2_regularization=1.0, random_state=self.random_state,
                )
                m.fit(X[tr], y[tr], sample_weight=w[tr])
                p = m.predict_proba(X[te])[:, 1]
                pred = (p >= 0.5).astype(int)
                accs.append(accuracy_score(y[te], pred))
                precs.append(precision_score(y[te], pred, zero_division=0))
                recs.append(recall_score(y[te], pred, zero_division=0))
                if len(np.unique(y[te])) > 1:
                    aucs.append(roc_auc_score(y[te], p))
            rep.cv_accuracy = float(np.mean(accs))
            rep.cv_precision = float(np.mean(precs))
            rep.cv_recall = float(np.mean(recs))
            rep.cv_auc = float(np.mean(aucs)) if aucs else float("nan")
        except Exception as exc:  # noqa: BLE001
            rep.notes.append(f"cross-validation skipped: {exc}")

        clf.fit(X, y, sample_weight=w)
        self.model = clf

        # -- self-training over the abstained pairs --------------------------------
        if self_training_rounds > 0:
            all_X = self._all_X
            labelled = set(self._labelled_index.tolist())
            abstained_idx = [i for i in range(len(feats)) if i not in labelled]
            X_aug, y_aug, w_aug = X, y, w
            for round_i in range(self_training_rounds):
                if not abstained_idx:
                    break
                probs = self.model.predict_proba(all_X[abstained_idx])[:, 1]
                take_pos = [i for i, p in zip(abstained_idx, probs) if p >= pseudo_high]
                take_neg = [i for i, p in zip(abstained_idx, probs) if p <= pseudo_low]
                # keep the pseudo-set balanced so it cannot swamp the weak labels
                cap = max(200, 4 * len(y))
                take_pos = take_pos[:cap]
                take_neg = take_neg[:cap]
                if len(take_pos) + len(take_neg) < 50:
                    rep.notes.append(
                        f"self-training round {round_i + 1} added too few confident "
                        f"pseudo-labels to be worth a refit; stopping"
                    )
                    break
                add_idx = take_pos + take_neg
                add_y = np.array([1] * len(take_pos) + [0] * len(take_neg), dtype=np.int32)
                # pseudo-labels carry half the weight of a weak label
                add_w = np.where(add_y == 1, pos_w, neg_w) * 0.5
                X_aug = np.vstack([X_aug, all_X[add_idx]])
                y_aug = np.concatenate([y_aug, add_y])
                w_aug = np.concatenate([w_aug, add_w])
                self.model = HistGradientBoostingClassifier(
                    max_iter=260, learning_rate=0.07, max_leaf_nodes=31,
                    min_samples_leaf=25, l2_regularization=1.0,
                    early_stopping=True, validation_fraction=0.15, n_iter_no_change=25,
                    random_state=self.random_state,
                ).fit(X_aug, y_aug, sample_weight=w_aug)
                rep.n_pseudo_positive += len(take_pos)
                rep.n_pseudo_negative += len(take_neg)
                # build the exclusion set once: constructing it inside the comprehension
                # made this line quadratic and, at ~30,000 candidates, it was the single
                # most expensive operation in the entire matcher
                consumed = set(add_idx)
                abstained_idx = [i for i in abstained_idx if i not in consumed]
            X, y = X_aug, y_aug

        rep.feature_importance = self._permutation_importance(X, y)
        rep.calibration_error = self._calibration_error(X, y)
        self.report = rep
        return rep

    def _permutation_importance(self, X: np.ndarray, y: np.ndarray,
                                repeats: int = 3) -> dict[str, float]:
        """Which features the decision actually rests on.

        Reported to the operator because 'the model said so' is not an acceptable basis
        for moving a boundary in a land record. If the importance is concentrated in
        ``attr_survey_number`` the matcher is really doing attribute linkage; if it is in
        ``iou`` it is doing geometry. Those are different failure modes and the operator
        deserves to know which one they have.
        """
        from sklearn.inspection import permutation_importance

        if self.model is None or len(y) < 60:
            return {}
        try:
            n = min(len(y), 6000)
            idx = np.random.default_rng(self.random_state).choice(len(y), n, replace=False)
            r = permutation_importance(self.model, X[idx], y[idx], n_repeats=repeats,
                                       random_state=self.random_state, scoring="roc_auc")
            order = np.argsort(-r.importances_mean)
            return {FEATURE_NAMES[i]: round(float(r.importances_mean[i]), 5)
                    for i in order[:12]}
        except Exception:  # noqa: BLE001
            return {}

    def _calibration_error(self, X: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
        """Expected calibration error: does a 0.7 prediction come true 70% of the time?

        A confidence score that is not calibrated is worse than no score, because the
        adjudication threshold downstream is set in probability units.
        """
        if self.model is None or len(y) < 50:
            return float("nan")
        p = self.model.predict_proba(X)[:, 1]
        edges = np.linspace(0, 1, bins + 1)
        ece = 0.0
        for i in range(bins):
            m = (p >= edges[i]) & (p < edges[i + 1])
            if m.sum() == 0:
                continue
            ece += (m.sum() / len(p)) * abs(p[m].mean() - y[m].mean())
        return float(ece)

    # -- inference ----------------------------------------------------------------

    def predict(self, pairs: Sequence[tuple[MatchableFeature, MatchableFeature]],
                feats: Sequence[dict[str, float]]) -> list[MatchPair]:
        if self.model is not None:
            X = (self._all_X if getattr(self, "_all_X_key", None) == id(feats)
                 else self.extractor.matrix(feats))
            probs = self.model.predict_proba(X)[:, 1]
        else:
            probs = np.array([geometric_score(f) for f in feats], dtype=np.float32)
        out: list[MatchPair] = []
        for (lf, rf), f, p in zip(pairs, feats, probs):
            out.append(MatchPair(
                left_id=lf.fid, right_id=rf.fid,
                left_dataset=lf.dataset_id, right_dataset=rf.dataset_id,
                features={k: round(float(v), 6) for k, v in f.items()},
                probability=float(p),
            ))
        return out

    def explain(self, feats: dict[str, float]) -> list[tuple[str, float]]:
        """Per-prediction feature contribution, for the adjudication UI."""
        if self.model is None:
            return sorted(
                ((k, float(v)) for k, v in feats.items() if k in
                 {"iou", "centroid_distance_norm", "attr_survey_number", "area_ratio"}),
                key=lambda kv: -abs(kv[1]),
            )
        base = np.array([self.extractor.vector(feats)], dtype=np.float32)
        p0 = float(self.model.predict_proba(base)[0, 1])
        out: list[tuple[str, float]] = []
        for i, name in enumerate(FEATURE_NAMES):
            probe = base.copy()
            probe[0, i] = 0.0
            delta = p0 - float(self.model.predict_proba(probe)[0, 1])
            out.append((name, round(delta, 5)))
        return sorted(out, key=lambda kv: -abs(kv[1]))[:10]

    # -- context ------------------------------------------------------------------

    @staticmethod
    def _pair_context(pairs: Sequence[tuple[MatchableFeature, MatchableFeature]],
                      feats: Sequence[dict[str, float]]) -> list[dict[str, Any]]:
        best_left: dict[str, float] = {}
        best_right: dict[str, float] = {}
        arg_left: dict[str, str] = {}
        arg_right: dict[str, str] = {}
        for (lf, rf), f in zip(pairs, feats):
            if f["iou"] > best_left.get(lf.fid, -1.0):
                best_left[lf.fid] = f["iou"]
                arg_left[lf.fid] = rf.fid
            if f["iou"] > best_right.get(rf.fid, -1.0):
                best_right[rf.fid] = f["iou"]
                arg_right[rf.fid] = lf.fid
        out = []
        for (lf, rf), f in zip(pairs, feats):
            out.append({
                "best_iou_left": best_left.get(lf.fid, 0.0),
                "best_iou_right": best_right.get(rf.fid, 0.0),
                "mutual_best": arg_left.get(lf.fid) == rf.fid and arg_right.get(rf.fid) == lf.fid,
            })
        return out

    # -- persistence --------------------------------------------------------------

    def save(self, path: str) -> None:
        import pickle

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump({"model": self.model, "report": self.report,
                         "features": FEATURE_NAMES}, fh)
        with open(path + ".json", "w", encoding="utf-8") as fh:
            json.dump({
                "features": list(FEATURE_NAMES),
                "report": {k: v for k, v in self.report.__dict__.items()},
            }, fh, indent=2, default=str)

    @classmethod
    def load(cls, path: str) -> "SpatialMatcher":
        import pickle

        m = cls()
        with open(path, "rb") as fh:
            blob = pickle.load(fh)
        m.model = blob["model"]
        m.report = blob["report"]
        return m


# --------------------------------------------------------------------------------------
# deterministic fallback
# --------------------------------------------------------------------------------------


def geometric_score(f: dict[str, float]) -> float:
    """A transparent, tunable score used when there is not enough data to learn.

    It is intentionally simple and monotone so that its behaviour is obvious to an
    operator. It is measurably worse than the learned model when the two layers have a
    systematic offset, which is the normal case — that gap is the argument for learning.
    """
    iou = f.get("iou", 0.0)
    cont = max(f.get("containment_left", 0.0), f.get("containment_right", 0.0))
    dist = f.get("centroid_distance_norm", 9.0)
    shape = 1.0 - min(f.get("turning_distance", 3.0) / 3.0, 1.0)
    area = f.get("area_ratio", 0.0)
    attr = f.get("attr_survey_number", 0.5)
    prox = math.exp(-max(dist, 0.0) / 1.5)
    raw = (0.34 * iou + 0.16 * cont + 0.18 * prox + 0.12 * shape
           + 0.10 * area + 0.10 * attr)
    return float(min(1.0, max(0.0, raw)))
