"""End-to-end spatial matching between two layers.

This is the stage the problem statement calls "AI/ML-based spatial matching algorithms",
assembled from the parts in this package:

    prepare -> block -> featurise -> (estimate registration offset) -> learn -> predict
            -> globally assign -> detect cardinality -> report

The registration step deserves a note. Two layers of the same city produced by different
agencies are almost always *systematically* offset — a metre north-east, say, from a datum
or control difference. If that offset is not estimated and removed before matching, IoU
collapses for every pair simultaneously and the matcher's most informative feature becomes
noise. The pipeline therefore estimates the offset robustly from the confident mutual-best
pairs, reports it (it is itself a finding worth telling the department about), and
re-featurises with it removed.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from ..core.models import Cardinality, MatchPair
from .assign import AssignmentConfig, AssignmentReport, GlobalAssigner, ParcelEvent, extract_events
from .features import (BlockingConfig, CandidateGenerator, FeatureExtractor,
                       MatchableFeature, neighbourhood_context)
from .model import SpatialMatcher, TrainingReport


@dataclass
class RegistrationOffset:
    """A systematic shift between two layers, estimated from confident pairs."""

    dx_m: float = 0.0
    dy_m: float = 0.0
    n_samples: int = 0
    residual_rms_m: float = 0.0
    significant: bool = False

    @property
    def magnitude_m(self) -> float:
        return math.hypot(self.dx_m, self.dy_m)

    @property
    def bearing_deg(self) -> float:
        return (math.degrees(math.atan2(self.dx_m, self.dy_m)) + 360.0) % 360.0

    def describe(self) -> str:
        if not self.significant:
            return (f"no significant systematic offset "
                    f"({self.magnitude_m * 100:.1f} cm over {self.n_samples} samples)")
        return (
            f"systematic offset of {self.magnitude_m:.2f} m on a bearing of "
            f"{self.bearing_deg:.0f}° (dx {self.dx_m:+.2f} m, dy {self.dy_m:+.2f} m) "
            f"estimated from {self.n_samples} confident pairs, residual RMS "
            f"{self.residual_rms_m:.2f} m"
        )


@dataclass
class MatchingResult:
    pairs: list[MatchPair] = field(default_factory=list)
    events: list[ParcelEvent] = field(default_factory=list)
    offset: RegistrationOffset = field(default_factory=RegistrationOffset)
    blocking: dict[str, Any] = field(default_factory=dict)
    training: TrainingReport | None = None
    assignment: AssignmentReport | None = None
    seconds: float = 0.0

    @property
    def accepted(self) -> list[MatchPair]:
        return [p for p in self.pairs if p.accepted]

    def summary(self) -> str:
        lines = [
            f"blocking: {self.blocking.get('candidate_pairs', 0):,} candidate pairs from "
            f"{self.blocking.get('left', 0):,} x {self.blocking.get('right', 0):,} "
            f"(search radius {self.blocking.get('search_radius_m', 0)} m, "
            f"reduction {self.blocking.get('reduction_ratio', 0) * 100:.6f}%)",
            f"registration: {self.offset.describe()}",
        ]
        if self.training:
            lines.append(f"model: {self.training.summary()}")
        if self.assignment:
            lines.append(f"assignment: {self.assignment.summary()}")
        lines.append(f"elapsed {self.seconds:.1f}s")
        return "\n".join(lines)


class MatchingPipeline:
    def __init__(self, *, blocking: BlockingConfig | None = None,
                 assignment: AssignmentConfig | None = None,
                 use_learned_model: bool = True,
                 correct_registration: bool = True) -> None:
        self.blocking = blocking or BlockingConfig()
        self.assignment = assignment or AssignmentConfig()
        self.use_learned_model = use_learned_model
        self.correct_registration = correct_registration
        self.matcher = SpatialMatcher()
        self.extractor = FeatureExtractor()

    def run(self, left: Sequence[MatchableFeature], right: Sequence[MatchableFeature],
            *, acc_left_m: float = 1.0, acc_right_m: float = 1.0,
            use_context: bool = True) -> MatchingResult:
        t0 = time.time()
        result = MatchingResult()

        gen = CandidateGenerator(self.blocking)
        pairs = gen.generate(left, right, acc_left_m=acc_left_m, acc_right_m=acc_right_m)
        result.blocking = dict(gen.stats)
        if not pairs:
            result.seconds = time.time() - t0
            return result

        # -- pass 1: cheap features only, enough to estimate the systematic offset -------
        cheap = [self.extractor.extract(a, b, full=False) for a, b in pairs]

        if self.correct_registration:
            result.offset = estimate_offset(pairs, cheap)
            if result.offset.significant:
                right = [_shift(f, -result.offset.dx_m, -result.offset.dy_m) for f in right]
                pairs = gen.generate(left, right, acc_left_m=acc_left_m,
                                     acc_right_m=acc_right_m)
                result.blocking = dict(gen.stats)
                result.blocking["registration_corrected"] = True
                cheap = [self.extractor.extract(a, b, full=False) for a, b in pairs]

        # -- pass 2: the expensive descriptors, only where they can change the answer ----
        feats: list[dict[str, float]] = []
        promoted = 0
        for (a, b), f in zip(pairs, cheap):
            if self.extractor.is_plausible(f):
                feats.append(self.extractor.extract(a, b))
                promoted += 1
            else:
                feats.append(f)
        result.blocking["pairs_given_full_descriptors"] = promoted
        result.blocking["pairs_settled_on_cheap_features"] = len(pairs) - promoted

        if use_context:
            ctx = neighbourhood_context(pairs)
            for (a, b), f in zip(pairs, feats):
                f["context_agreement"] = ctx.get((a.fid, b.fid), 0.0)

        _add_ranks(pairs, feats)

        if self.use_learned_model:
            result.training = self.matcher.fit(pairs, feats)
        result.pairs = self.matcher.predict(pairs, feats)

        lmap = {f.fid: f for f in left}
        rmap = {f.fid: f for f in right}
        assigner = GlobalAssigner(self.assignment)
        assigned, rep = assigner.assign(result.pairs, lmap, rmap)
        result.pairs = assigned
        result.assignment = rep
        result.events = extract_events(assigned, lmap, rmap, rep)
        result.seconds = time.time() - t0
        return result


# --------------------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------------------


def estimate_offset(pairs: Sequence[tuple[MatchableFeature, MatchableFeature]],
                    feats: Sequence[dict[str, float]],
                    *, min_iou: float = 0.55, min_samples: int = 25,
                    significance_m: float = 0.30) -> RegistrationOffset:
    """Robustly estimate a constant translation between two layers.

    Uses the median rather than the mean of the confident pairs' centroid differences,
    because the sample is contaminated by genuine changes (a demolished building, a
    subdivided plot) and the median is unmoved by up to half the sample being wrong.
    """
    dx: list[float] = []
    dy: list[float] = []
    for (a, b), f in zip(pairs, feats):
        if f.get("iou", 0.0) >= min_iou:
            dx.append(b.centroid.x - a.centroid.x)
            dy.append(b.centroid.y - a.centroid.y)
    if len(dx) < min_samples:
        return RegistrationOffset(n_samples=len(dx))
    mx, my = float(np.median(dx)), float(np.median(dy))
    res = np.hypot(np.array(dx) - mx, np.array(dy) - my)
    off = RegistrationOffset(
        dx_m=mx, dy_m=my, n_samples=len(dx),
        residual_rms_m=float(np.sqrt(np.mean(res ** 2))),
    )
    off.significant = off.magnitude_m >= significance_m
    return off


def _shift(f: MatchableFeature, dx: float, dy: float) -> MatchableFeature:
    from shapely.affinity import translate

    return MatchableFeature(
        fid=f.fid, dataset_id=f.dataset_id,
        geometry=translate(f.geometry, xoff=dx, yoff=dy),
        attributes=f.attributes,
    )


def _add_ranks(pairs: Sequence[tuple[MatchableFeature, MatchableFeature]],
               feats: Sequence[dict[str, float]]) -> None:
    """Rank of each pair among its endpoint's candidates.

    Rank is a surprisingly strong feature: the best candidate for a feature is a different
    proposition from its fourth-best even at the same IoU, because the existence of three
    better options is evidence against this one.
    """
    by_left: dict[str, list[int]] = {}
    by_right: dict[str, list[int]] = {}
    for i, (a, b) in enumerate(pairs):
        by_left.setdefault(a.fid, []).append(i)
        by_right.setdefault(b.fid, []).append(i)
    for idxs in by_left.values():
        for r, i in enumerate(sorted(idxs, key=lambda k: -feats[k].get("iou", 0.0))):
            feats[i]["rank_left"] = float(r)
    for idxs in by_right.values():
        for r, i in enumerate(sorted(idxs, key=lambda k: -feats[k].get("iou", 0.0))):
            feats[i]["rank_right"] = float(r)


# --------------------------------------------------------------------------------------
# multi-layer matching
# --------------------------------------------------------------------------------------


@dataclass
class EntityCluster:
    """A set of features from several datasets believed to be one real-world entity."""

    cluster_id: str
    members: dict[str, str] = field(default_factory=dict)   # dataset_id -> feature_id
    support: float = 0.0
    cardinality: Cardinality = Cardinality.ONE_TO_ONE

    @property
    def n_sources(self) -> int:
        return len(self.members)


def cluster_across_layers(results: dict[tuple[str, str], MatchingResult],
                          *, all_features: dict[str, Iterable[str]] | None = None
                          ) -> list[EntityCluster]:
    """Fuse pairwise matches from N layers into entity clusters via transitive closure.

    With three or more sources, pairwise matching produces an inconsistent triangle
    sooner or later: A matches B, B matches C, but A does not match C. Union-find over the
    accepted pairs resolves this by transitivity, and the resulting cluster's ``support``
    records how much of the possible pairwise agreement was actually observed — a triangle
    with all three edges is far stronger evidence than one with two, and the confidence
    scorer downstream uses exactly that.

    ``all_features`` maps every dataset id to every feature id it contains. Supplying it is
    what makes the output *complete*: a parcel that only one source knows about is still a
    parcel, and dropping it because it had nothing to match against would silently delete
    land from the cadastre. Such features become single-member clusters with support 0,
    which is exactly what the confidence scorer needs in order to record "one source,
    uncorroborated" rather than recording nothing at all.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    edge_p: dict[tuple[str, str], float] = {}
    for (lds, rds), res in results.items():
        for p in res.accepted:
            a = f"{lds}::{p.left_id}"
            b = f"{rds}::{p.right_id}"
            union(a, b)
            edge_p[(a, b)] = p.probability

    groups: dict[str, list[str]] = {}
    for node in list(parent):
        groups.setdefault(find(node), []).append(node)

    clusters: list[EntityCluster] = []
    clustered: set[str] = set()
    for i, (root, nodes) in enumerate(sorted(groups.items())):
        members: dict[str, str] = {}
        multi = False
        for n in nodes:
            clustered.add(n)
            ds, fid = n.split("::", 1)
            if ds in members:
                multi = True
            members.setdefault(ds, fid)
        k = len(nodes)
        possible = k * (k - 1) / 2 if k > 1 else 1
        observed = sum(1 for (a, b) in edge_p if a in nodes and b in nodes)
        clusters.append(EntityCluster(
            cluster_id=f"C{i:08d}",
            members=members,
            support=round(min(1.0, observed / possible), 4) if possible else 0.0,
            cardinality=Cardinality.MANY_TO_MANY if multi else Cardinality.ONE_TO_ONE,
        ))

    if all_features:
        n = len(clusters)
        for ds, fids in all_features.items():
            for fid in fids:
                node = f"{ds}::{fid}"
                if node in clustered:
                    continue
                clusters.append(EntityCluster(
                    cluster_id=f"S{n:08d}",
                    members={ds: fid},
                    support=0.0,
                    cardinality=Cardinality.ONE_TO_ONE,
                ))
                n += 1
    return clusters
