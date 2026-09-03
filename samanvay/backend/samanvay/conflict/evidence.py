"""Evidence fusion — Dempster-Shafer combination over source claims.

When four datasets disagree about a parcel boundary, the platform has to choose. The naive
options are all wrong in instructive ways:

* *Trust the newest* ignores that a recent low-quality survey is worse than an old good one.
* *Trust the highest authority* ignores that the revenue department is authoritative about
  ownership and hopeless about geometry.
* *Average the geometries* invents a boundary that no source asserts and no surveyor
  observed — the worst outcome of all, because it is unattributable.
* *Majority vote* treats three copies of the same underlying survey as three independent
  witnesses, which is the single most common error in data fusion.

SAMANVAY uses Dempster-Shafer evidence theory, for one specific reason: it distinguishes
*disagreement* from *ignorance*. Probability theory forces the mass not assigned to A onto
not-A. DS lets mass sit on "don't know", which is the correct state when only one source has
an opinion. That distinction is what produces the platform's two separate outputs — belief
(what is supported) and plausibility (what is not contradicted) — whose gap is the honest
measure of how much is unknown, and is what drives the adjudication queue.

Conflict mass K is retained rather than being normalised away. Dempster's rule divides it
out, which famously produces absurd certainty when sources strongly disagree (the Zadeh
counter-example). Here, a high K is *the signal that a human is needed*, so throwing it away
would discard the most valuable output.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Iterable, Sequence

from ..core.models import Claim, Evidence

Hypothesis = Hashable


@dataclass
class MassFunction:
    """A basic probability assignment over singleton hypotheses plus the frame Θ."""

    masses: dict[Hypothesis, float] = field(default_factory=dict)
    theta: float = 1.0
    """Mass on 'I don't know' — the whole frame of discernment."""

    def normalise(self) -> "MassFunction":
        total = sum(self.masses.values()) + self.theta
        if total <= 0:
            return MassFunction({}, 1.0)
        return MassFunction({k: v / total for k, v in self.masses.items()}, self.theta / total)

    def belief(self, h: Hypothesis) -> float:
        return self.masses.get(h, 0.0)

    def plausibility(self, h: Hypothesis) -> float:
        return self.masses.get(h, 0.0) + self.theta

    def best(self) -> tuple[Hypothesis | None, float, float]:
        if not self.masses:
            return None, 0.0, self.theta
        h = max(self.masses, key=self.masses.get)  # type: ignore[arg-type]
        return h, self.belief(h), self.plausibility(h)

    @property
    def entropy(self) -> float:
        """Shannon entropy over the mass distribution, including Θ.

        High entropy with low conflict means "nobody knows"; low entropy with high conflict
        means "two confident sources contradict each other". Very different problems.
        """
        vals = list(self.masses.values()) + [self.theta]
        return -sum(v * math.log2(v) for v in vals if v > 0)


def combine(m1: MassFunction, m2: MassFunction) -> tuple[MassFunction, float]:
    """Unnormalised Dempster combination. Returns ``(combined, conflict_mass)``.

    The conflict mass K is returned rather than divided out. Callers decide what to do with
    it, and in this platform the decision is: above a threshold, escalate to a human.
    """
    out: dict[Hypothesis, float] = defaultdict(float)
    conflict = 0.0
    for h1, v1 in m1.masses.items():
        for h2, v2 in m2.masses.items():
            if h1 == h2:
                out[h1] += v1 * v2
            else:
                conflict += v1 * v2
        out[h1] += v1 * m2.theta
    for h2, v2 in m2.masses.items():
        out[h2] += v2 * m1.theta
    theta = m1.theta * m2.theta
    return MassFunction(dict(out), theta), conflict


def combine_all(masses: Sequence[MassFunction]) -> tuple[MassFunction, float]:
    if not masses:
        return MassFunction({}, 1.0), 0.0
    acc = masses[0]
    total_conflict = 0.0
    for m in masses[1:]:
        acc, k = combine(acc, m)
        total_conflict = total_conflict + k - total_conflict * k  # noisy-or accumulation
    return acc, total_conflict


# --------------------------------------------------------------------------------------
# turning claims into evidence
# --------------------------------------------------------------------------------------


def evidence_to_mass(ev: Evidence, hypothesis: Hypothesis) -> MassFunction:
    """One piece of evidence becomes mass on its hypothesis, the rest on Θ.

    This is the crucial modelling step. A source with reliability 0.9 asserting X does not
    put 0.1 on not-X; it puts 0.1 on *ignorance*. That is what allows a single source to be
    a weak witness rather than an implicit refutation of everything else.
    """
    m = min(max(ev.mass, 0.0), 0.999)
    return MassFunction({hypothesis: m}, 1.0 - m)


def independence_discount(evidences: Sequence[Evidence],
                          lineage: dict[str, set[str]] | None = None) -> list[float]:
    """Down-weight sources that are not actually independent.

    Three datasets derived from the same 1970s village map are one witness, not three.
    Naive fusion treats them as three and produces spurious confidence. The discount is
    computed from a declared lineage graph: datasets sharing an ancestor split their weight.
    """
    lineage = lineage or {}
    groups: dict[frozenset[str], list[int]] = defaultdict(list)
    for i, e in enumerate(evidences):
        anc = frozenset(lineage.get(e.claim.dataset_id, {e.claim.dataset_id}))
        groups[anc].append(i)
    factors = [1.0] * len(evidences)
    for idxs in groups.values():
        if len(idxs) > 1:
            f = 1.0 / math.sqrt(len(idxs))
            for i in idxs:
                factors[i] = f
    return factors


def fuse(evidences: Sequence[Evidence],
         *,
         key: Callable[[Any], Hypothesis] | None = None,
         lineage: dict[str, set[str]] | None = None
         ) -> tuple[Hypothesis | None, float, float, float, dict[str, Any]]:
    """Fuse competing claims. Returns ``(value, belief, plausibility, conflict, detail)``."""
    if not evidences:
        return None, 0.0, 1.0, 0.0, {"reason": "no evidence"}

    key = key or (lambda v: v)
    factors = independence_discount(evidences, lineage)
    masses: list[MassFunction] = []
    per_hypothesis: dict[Hypothesis, list[str]] = defaultdict(list)

    for e, f in zip(evidences, factors):
        h = key(e.claim.value)
        per_hypothesis[h].append(e.claim.dataset_id)
        scaled = Evidence(
            claim=e.claim, reliability=e.reliability * f,
            recency_weight=e.recency_weight, accuracy_weight=e.accuracy_weight,
            corroboration=e.corroboration, penalty=e.penalty,
        )
        masses.append(evidence_to_mass(scaled, h))

    combined, conflict = combine_all(masses)
    combined = combined.normalise() if combined.theta + sum(combined.masses.values()) > 0 else combined
    best, bel, pl = combined.best()
    detail = {
        "n_evidence": len(evidences),
        "n_hypotheses": len(per_hypothesis),
        "supporters": {str(k): v for k, v in per_hypothesis.items()},
        "independence_factors": [round(f, 3) for f in factors],
        "entropy": round(combined.entropy, 4),
        "mass": {str(k): round(v, 4) for k, v in combined.masses.items()},
        "theta": round(combined.theta, 4),
    }
    return best, bel, pl, conflict, detail


# --------------------------------------------------------------------------------------
# geometric fusion
# --------------------------------------------------------------------------------------


def fuse_geometry(evidences: Sequence[Evidence], *, tolerance_m: float = 0.5
                  ) -> tuple[str | None, float, float, float, dict[str, Any]]:
    """Fuse competing geometries.

    Geometries are continuous, so the discrete DS machinery needs a bridge: geometries that
    agree to within a tolerance are treated as the *same* hypothesis. Agreement is measured
    by intersection-over-union, and clustering is single-linkage at the tolerance.

    The platform never averages geometries. The output is always one source's actual
    observed boundary, chosen by evidence — because a boundary in a land record must be
    attributable to a survey somebody performed and can be asked to repeat.
    """
    from shapely import wkt as shp_wkt

    geoms: list[tuple[int, Any]] = []
    for i, e in enumerate(evidences):
        try:
            geoms.append((i, shp_wkt.loads(str(e.claim.value))))
        except Exception:  # noqa: BLE001
            continue
    if not geoms:
        return None, 0.0, 1.0, 0.0, {"reason": "no parseable geometry"}

    # Cluster geometries that agree *within the stated tolerance*.
    #
    # Agreement has to be judged in metres, not in IoU. Two surveys of the same 90 m²
    # house whose walls differ by 40 cm have an IoU near 0.82, and calling that a
    # disagreement escalates essentially every small building to a human — which makes the
    # adjudication queue useless. Conversely two surveys of a 4,000 m² compound differing
    # by 3 m have an IoU near 0.97 and genuinely do disagree. Distance is scale-honest
    # where IoU is not, so the primary test is separation in metres, with a generous IoU
    # test retained as a cheap shortcut for the obvious cases.
    index = dict(geoms)
    clusters: list[list[int]] = []
    for i, g in geoms:
        placed = False
        for c in clusters:
            gj = index[c[0]]
            try:
                sep = _boundary_separation_m(g, gj)
                inter = g.intersection(gj).area
                union = g.union(gj).area
                iou = inter / union if union > 0 else 0.0
            except Exception:  # noqa: BLE001
                sep, iou = 1e9, 0.0
            if sep <= tolerance_m or iou >= 0.90:
                c.append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])

    cluster_of = {i: ci for ci, c in enumerate(clusters) for i in c}
    scaled: list[Evidence] = []
    for i, _ in geoms:
        e = evidences[i]
        corro = (len(clusters[cluster_of[i]]) - 1) / max(len(geoms) - 1, 1)
        scaled.append(Evidence(
            claim=e.claim, reliability=e.reliability, recency_weight=e.recency_weight,
            accuracy_weight=e.accuracy_weight, corroboration=corro, penalty=e.penalty,
        ))

    masses = [evidence_to_mass(e, cluster_of[i]) for (i, _), e in zip(geoms, scaled)]
    combined, conflict = combine_all(masses)
    combined = combined.normalise()
    best_cluster, bel, pl = combined.best()
    if best_cluster is None:
        return None, 0.0, 1.0, conflict, {"reason": "no dominant geometry"}

    members = clusters[int(best_cluster)]  # type: ignore[arg-type]
    winner = max(members, key=lambda i: scaled[[g[0] for g in geoms].index(i)].mass
                 if i in [g[0] for g in geoms] else 0.0)
    detail = {
        "n_geometries": len(geoms),
        "n_clusters": len(clusters),
        "cluster_sizes": [len(c) for c in clusters],
        "winning_dataset": evidences[winner].claim.dataset_id,
        "agreeing_datasets": [evidences[i].claim.dataset_id for i in members],
        "note": "the emitted geometry is one source's observed boundary, never an average",
    }
    return str(evidences[winner].claim.value), bel, pl, conflict, detail


def _boundary_separation_m(a: Any, b: Any, n: int = 16) -> float:
    """Mean distance from sampled points on A's boundary to B's boundary, in map units.

    Used instead of Hausdorff because Hausdorff is set by the single worst vertex, and a
    single worst vertex is usually a digitising spike rather than a real disagreement.
    """
    import numpy as _np

    try:
        ba, bb = a.boundary, b.boundary
        length = ba.length
        if length <= 0 or bb.is_empty:
            return 1e9
        pts = [ba.interpolate(length * k / n) for k in range(n)]
        return float(_np.mean([p.distance(bb) for p in pts]))
    except Exception:  # noqa: BLE001
        return 1e9


def build_evidence(claims: Iterable[Claim], registry) -> list[Evidence]:
    """Attach reliability, recency and accuracy weights to raw claims."""
    out: list[Evidence] = []
    for c in claims:
        out.append(Evidence(
            claim=c,
            reliability=registry.reliability(c.dataset_id, c.property_path),
            recency_weight=registry.recency_weight(c.dataset_id),
            accuracy_weight=registry.accuracy_weight(c.dataset_id),
            penalty=0.0 if c.extraction_confidence is None
            else max(0.0, 1.0 - float(c.extraction_confidence)) * 0.6,
        ))
    return out
