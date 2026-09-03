"""Global assignment and cardinality resolution.

A per-pair probability is not an answer. Taking the argmax for each feature independently
produces contradictions: two parcels both claiming the same counterpart, chains of
half-matches, and — worst — a "match" that is locally best but globally impossible. A
cadastre must be assigned *globally*.

Two things happen here.

**Global optimal assignment.** The accepted pairs are chosen to maximise total match
probability subject to each feature being used once, solved exactly as a rectangular
linear-sum assignment (Jonker-Volgenant via SciPy) on each connected component of the
candidate graph. Working per component rather than on the whole layer keeps the cost
manageable: a 250,000-feature layer decomposes into tens of thousands of components of a
handful of features each.

**Cardinality detection.** One-to-one is the exception, not the rule, in cadastral
harmonisation. Subdivision, amalgamation and boundary reorganisation are the substance of
what changes in a land record between two epochs, and they present as 1:N, N:1 and N:M
groups. The module detects these from the residual structure the assignment leaves behind,
using containment and area conservation:

* **1:N (subdivision)** — one left feature is covered, to within tolerance, by the union of
  several right features, and their areas sum to its area.
* **N:1 (amalgamation)** — the mirror image.
* **N:M** — a component where neither holds; a genuine reorganisation that a human must
  look at.

Area conservation is the discriminator that makes this trustworthy. A left parcel split
into three right parcels whose areas sum to 98% of it is a subdivision; one whose parts sum
to 40% is a bad match set dressed up as a subdivision.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from ..core.models import Cardinality, MatchPair
from .features import MatchableFeature


@dataclass
class AssignmentConfig:
    accept_threshold: float = 0.55
    """Minimum probability for a pair to be considered at all."""
    review_threshold: float = 0.35
    """Between review and accept, a pair is proposed but flagged for adjudication."""
    group_containment_tol: float = 0.80
    """Fraction of the parent that must be covered by the children to call it a split."""
    group_area_tol: float = 0.25
    """Allowed relative discrepancy between parent area and summed child areas."""
    max_component_size: int = 400
    """Above this, the component is split by proximity rather than solved exactly."""


@dataclass
class AssignmentReport:
    n_pairs_considered: int = 0
    n_accepted: int = 0
    n_flagged: int = 0
    n_components: int = 0
    largest_component: int = 0
    cardinality_counts: dict[str, int] = field(default_factory=dict)
    unmatched_left: list[str] = field(default_factory=list)
    unmatched_right: list[str] = field(default_factory=list)
    mean_accepted_probability: float = 0.0

    def summary(self) -> str:
        cards = ", ".join(f"{k} {v:,}" for k, v in sorted(self.cardinality_counts.items()))
        return (
            f"{self.n_accepted:,} accepted of {self.n_pairs_considered:,} candidate pairs "
            f"across {self.n_components:,} components (largest {self.largest_component}); "
            f"cardinality: {cards}; unmatched {len(self.unmatched_left):,} left / "
            f"{len(self.unmatched_right):,} right; mean p={self.mean_accepted_probability:.3f}"
        )


class GlobalAssigner:
    def __init__(self, config: AssignmentConfig | None = None) -> None:
        self.config = config or AssignmentConfig()

    def assign(self, pairs: Sequence[MatchPair],
               left: dict[str, MatchableFeature],
               right: dict[str, MatchableFeature]) -> tuple[list[MatchPair], AssignmentReport]:
        cfg = self.config
        rep = AssignmentReport(n_pairs_considered=len(pairs))
        live = [p for p in pairs if p.probability >= cfg.review_threshold]

        components = self._components(live)
        rep.n_components = len(components)
        rep.largest_component = max((len(c) for c in components), default=0)

        accepted: list[MatchPair] = []
        for comp in components:
            accepted.extend(self._solve_component(comp, left, right))

        for p in accepted:
            p.accepted = p.probability >= cfg.accept_threshold
            if not p.accepted:
                rep.n_flagged += 1

        accepted = [p for p in accepted if p.probability >= cfg.review_threshold]
        rep.n_accepted = sum(1 for p in accepted if p.accepted)
        probs = [p.probability for p in accepted if p.accepted]
        rep.mean_accepted_probability = float(np.mean(probs)) if probs else 0.0

        matched_left = {p.left_id for p in accepted if p.accepted}
        matched_right = {p.right_id for p in accepted if p.accepted}
        rep.unmatched_left = sorted(set(left) - matched_left)
        rep.unmatched_right = sorted(set(right) - matched_right)

        for p in accepted:
            rep.cardinality_counts[p.cardinality.value] = (
                rep.cardinality_counts.get(p.cardinality.value, 0) + 1
            )
        rep.cardinality_counts[Cardinality.UNMATCHED_LEFT.value] = len(rep.unmatched_left)
        rep.cardinality_counts[Cardinality.UNMATCHED_RIGHT.value] = len(rep.unmatched_right)
        return accepted, rep

    # -- graph --------------------------------------------------------------------

    @staticmethod
    def _components(pairs: Sequence[MatchPair]) -> list[list[MatchPair]]:
        """Connected components of the bipartite candidate graph, via union-find."""
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

        for p in pairs:
            union("L:" + p.left_id, "R:" + p.right_id)

        groups: dict[str, list[MatchPair]] = defaultdict(list)
        for p in pairs:
            groups[find("L:" + p.left_id)].append(p)
        return list(groups.values())

    # -- per-component solving -----------------------------------------------------

    def _solve_component(self, comp: list[MatchPair],
                         left: dict[str, MatchableFeature],
                         right: dict[str, MatchableFeature]) -> list[MatchPair]:
        cfg = self.config
        lids = sorted({p.left_id for p in comp})
        rids = sorted({p.right_id for p in comp})

        if len(lids) == 1 and len(rids) == 1:
            comp[0].cardinality = Cardinality.ONE_TO_ONE
            return list(comp)

        group = self._detect_group(comp, lids, rids, left, right)
        if group is not None:
            return group

        if len(lids) * len(rids) > cfg.max_component_size ** 2:
            return self._greedy(comp)

        return self._hungarian(comp, lids, rids)

    def _hungarian(self, comp: list[MatchPair], lids: list[str],
                   rids: list[str]) -> list[MatchPair]:
        from scipy.optimize import linear_sum_assignment

        li = {v: i for i, v in enumerate(lids)}
        ri = {v: i for i, v in enumerate(rids)}
        cost = np.full((len(lids), len(rids)), 1.0, dtype=np.float64)
        lookup: dict[tuple[int, int], MatchPair] = {}
        for p in comp:
            i, j = li[p.left_id], ri[p.right_id]
            cost[i, j] = 1.0 - p.probability
            lookup[(i, j)] = p
        rows, cols = linear_sum_assignment(cost)
        out: list[MatchPair] = []
        for i, j in zip(rows, cols):
            p = lookup.get((int(i), int(j)))
            if p is None:
                continue
            p.cardinality = Cardinality.ONE_TO_ONE
            out.append(p)
        return out

    def _greedy(self, comp: list[MatchPair]) -> list[MatchPair]:
        used_l: set[str] = set()
        used_r: set[str] = set()
        out: list[MatchPair] = []
        for p in sorted(comp, key=lambda x: -x.probability):
            if p.left_id in used_l or p.right_id in used_r:
                continue
            used_l.add(p.left_id)
            used_r.add(p.right_id)
            p.cardinality = Cardinality.ONE_TO_ONE
            out.append(p)
        return out

    def _detect_group(self, comp: list[MatchPair], lids: list[str], rids: list[str],
                      left: dict[str, MatchableFeature],
                      right: dict[str, MatchableFeature]) -> list[MatchPair] | None:
        """Recognise subdivision / amalgamation before falling back to 1:1 assignment."""
        cfg = self.config

        if len(lids) == 1 and len(rids) > 1:
            parent = left.get(lids[0])
            children = [right[r] for r in rids if r in right]
            if parent and children and self._is_partition(parent, children):
                gid = f"split:{lids[0]}"
                for p in comp:
                    p.cardinality = Cardinality.ONE_TO_MANY
                    p.group_id = gid
                return list(comp)

        if len(rids) == 1 and len(lids) > 1:
            parent = right.get(rids[0])
            children = [left[l] for l in lids if l in left]
            if parent and children and self._is_partition(parent, children):
                gid = f"merge:{rids[0]}"
                for p in comp:
                    p.cardinality = Cardinality.MANY_TO_ONE
                    p.group_id = gid
                return list(comp)

        if len(lids) > 1 and len(rids) > 1:
            la = sum(left[i].area for i in lids if i in left)
            ra = sum(right[i].area for i in rids if i in right)
            if la > 0 and abs(la - ra) / max(la, ra) <= cfg.group_area_tol:
                gid = f"reorg:{lids[0]}|{rids[0]}"
                for p in comp:
                    p.cardinality = Cardinality.MANY_TO_MANY
                    p.group_id = gid
                return list(comp)
        return None

    def _is_partition(self, parent: MatchableFeature,
                      children: Sequence[MatchableFeature]) -> bool:
        cfg = self.config
        if parent.area <= 0:
            return False
        try:
            from shapely.ops import unary_union

            union = unary_union([c.geometry for c in children])
            covered = parent.geometry.intersection(union).area / parent.area
        except Exception:  # noqa: BLE001
            return False
        summed = sum(c.area for c in children)
        conserved = abs(summed - parent.area) / parent.area
        return covered >= cfg.group_containment_tol and conserved <= cfg.group_area_tol


# --------------------------------------------------------------------------------------
# genealogy
# --------------------------------------------------------------------------------------


@dataclass
class ParcelEvent:
    """A recognised change in the parcel fabric between two epochs."""

    kind: str  # subdivision | amalgamation | reorganisation | new | retired
    parents: list[str]
    children: list[str]
    parent_area_m2: float
    child_area_m2: float
    confidence: float

    @property
    def area_conserved_pct(self) -> float:
        if self.parent_area_m2 <= 0:
            return 0.0
        return 100.0 * self.child_area_m2 / self.parent_area_m2

    def describe(self) -> str:
        return (
            f"{self.kind}: {len(self.parents)} -> {len(self.children)} parcels, "
            f"area conserved {self.area_conserved_pct:.1f}%, confidence {self.confidence:.2f}"
        )


def extract_events(pairs: Sequence[MatchPair],
                   left: dict[str, MatchableFeature],
                   right: dict[str, MatchableFeature],
                   report: AssignmentReport) -> list[ParcelEvent]:
    """Turn the assignment into the mutation events a land registry actually records."""
    groups: dict[str, list[MatchPair]] = defaultdict(list)
    events: list[ParcelEvent] = []
    for p in pairs:
        if p.group_id:
            groups[p.group_id].append(p)

    for gid, members in groups.items():
        lids = sorted({m.left_id for m in members})
        rids = sorted({m.right_id for m in members})
        pa = sum(left[i].area for i in lids if i in left)
        ca = sum(right[i].area for i in rids if i in right)
        conf = float(np.mean([m.probability for m in members]))
        kind = ("subdivision" if gid.startswith("split")
                else "amalgamation" if gid.startswith("merge")
                else "reorganisation")
        events.append(ParcelEvent(kind, lids, rids, pa, ca, conf))

    for lid in report.unmatched_left:
        f = left.get(lid)
        if f:
            events.append(ParcelEvent("retired", [lid], [], f.area, 0.0, 0.6))
    for rid in report.unmatched_right:
        f = right.get(rid)
        if f:
            events.append(ParcelEvent("new", [], [rid], 0.0, f.area, 0.6))
    return events
