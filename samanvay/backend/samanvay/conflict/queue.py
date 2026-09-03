"""Adjudication queue and the active-learning loop.

Escalation is only useful if the escalated cases reach a human in an order that respects
their time, arrive with everything needed to decide, and — crucially — feed back into the
system so the same class of case stops arriving.

**Priority.** Cases are ranked by expected value of resolution, not by severity alone. A
conflict over a 4,000 m² commercial parcel with high uncertainty is worth an officer's
attention before a 20 m² ambiguity, and a case that is representative of a large cluster of
similar cases is worth more than an isolated one because resolving it teaches the model the
most.

**Batching.** Cases that share a cause — the same pair of datasets, the same rule, the same
ward — are grouped, because deciding twenty instances of one systematic problem takes an
officer a fraction of the time of twenty unrelated decisions.

**Feedback.** Every human decision becomes a labelled training example. Over a campaign the
matcher and the reliability priors converge on the department's actual practice rather than
on the platform author's guesses, which is the only way a system like this stops needing
tuning.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from ..core.models import AdjudicationState, Conflict, Resolution


@dataclass
class AdjudicationCase:
    case_id: str
    entity_id: str
    property_path: str
    conflict: Conflict
    proposed: Resolution
    priority: float = 0.0
    batch_key: str = ""
    area_m2: float = 0.0
    ward: str = ""
    state: AdjudicationState = AdjudicationState.QUEUED
    assigned_to: str | None = None
    decided_value: Any = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str = ""

    def brief(self) -> dict[str, Any]:
        """Exactly what an officer needs on screen to decide, and nothing else."""
        return {
            "case_id": self.case_id,
            "entity_id": self.entity_id,
            "property": self.property_path,
            "question": self._question(),
            "options": [
                {
                    "dataset": e.claim.dataset_id,
                    "source_type": e.claim.source_type.value,
                    "value": _short(e.claim.value),
                    "weight": round(e.mass, 3),
                    "observed": e.claim.observed_on.isoformat() if e.claim.observed_on else None,
                    "declared_accuracy_m": e.claim.accuracy_m,
                }
                for e in self.conflict.evidences
            ],
            "platform_proposal": _short(self.proposed.chosen_value),
            "why": self.proposed.rationale,
            "belief": self.proposed.belief,
            "uncertainty": round(self.proposed.uncertainty, 4),
            "severity": self.conflict.severity,
            "priority": round(self.priority, 4),
            "context": {"area_m2": round(self.area_m2, 2), "ward": self.ward},
        }

    def _question(self) -> str:
        if self.property_path == "geometry":
            return ("Which surveyed boundary is correct for this parcel? The sources "
                    "disagree beyond the tolerance that automated fusion can resolve.")
        return (f"Which value of '{self.property_path}' should the harmonised record carry? "
                f"{len(self.conflict.competing_datasets())} datasets disagree.")


@dataclass
class QueueStats:
    total: int = 0
    by_property: dict[str, int] = field(default_factory=dict)
    by_batch: dict[str, int] = field(default_factory=dict)
    decided: int = 0
    agreement_with_platform: float = 0.0
    estimated_officer_hours: float = 0.0

    def summary(self) -> str:
        return (
            f"{self.total:,} cases queued in {len(self.by_batch):,} batches "
            f"(~{self.estimated_officer_hours:.1f} officer-hours at 90 s per decision, "
            f"or {self.estimated_officer_hours / max(len(self.by_batch), 1) * 60:.0f} "
            f"minutes per batch); {self.decided:,} decided, "
            f"{self.agreement_with_platform * 100:.1f}% agreed with the platform proposal"
        )


class AdjudicationQueue:
    """Priority queue with batching, persistence and a feedback channel."""

    SECONDS_PER_DECISION = 90.0

    def __init__(self, path: str | None = None) -> None:
        self.cases: dict[str, AdjudicationCase] = {}
        self.path = path
        self._decisions: list[dict[str, Any]] = []

    # -- filling -------------------------------------------------------------------

    def enqueue(self, conflict: Conflict, resolution: Resolution, *,
                area_m2: float = 0.0, ward: str = "") -> AdjudicationCase:
        case = AdjudicationCase(
            case_id=f"ADJ-{conflict.conflict_id}",
            entity_id=conflict.entity_id,
            property_path=conflict.property_path,
            conflict=conflict,
            proposed=resolution,
            area_m2=area_m2,
            ward=ward,
        )
        case.batch_key = self._batch_key(case)
        case.priority = self._priority(case)
        self.cases[case.case_id] = case
        return case

    def enqueue_many(self, items: Iterable[tuple[Conflict, Resolution, dict[str, Any]]]
                     ) -> list[AdjudicationCase]:
        return [self.enqueue(c, r, **kw) for c, r, kw in items]

    # -- ranking -------------------------------------------------------------------

    @staticmethod
    def _batch_key(case: AdjudicationCase) -> str:
        datasets = "+".join(case.conflict.competing_datasets())
        rule = ""
        if case.proposed.rationale.startswith("["):
            rule = case.proposed.rationale.split("]")[0].strip("[")
        return f"{case.property_path}|{datasets}|{rule}"

    def _priority(self, case: AdjudicationCase) -> float:
        """Expected value of a decision.

        Combines how wrong the record could be (uncertainty x severity), how much land is
        at stake (log area, so a 10x bigger parcel is not 10x more urgent), and how much
        the decision would teach the system (batch size). The log keeps a single enormous
        parcel from monopolising the queue.
        """
        unc = case.proposed.uncertainty
        sev = case.conflict.severity
        stake = math.log10(max(case.area_m2, 1.0) + 1.0) / 4.0
        batch = len([c for c in self.cases.values() if c.batch_key == case.batch_key])
        learn = math.log10(batch + 1.0) / 3.0
        return round(0.34 * sev + 0.28 * unc + 0.24 * stake + 0.14 * learn, 5)

    def rerank(self) -> None:
        for c in self.cases.values():
            c.priority = self._priority(c)

    def next_batch(self, limit: int = 25, officer: str | None = None
                   ) -> list[AdjudicationCase]:
        """Return the highest-value coherent batch, so the officer decides one *kind*
        of thing at a time rather than context-switching on every case."""
        pending = [c for c in self.cases.values() if c.state == AdjudicationState.QUEUED]
        if not pending:
            return []
        groups: dict[str, list[AdjudicationCase]] = defaultdict(list)
        for c in pending:
            groups[c.batch_key].append(c)
        best_key = max(groups, key=lambda k: sum(c.priority for c in groups[k]))
        batch = sorted(groups[best_key], key=lambda c: -c.priority)[:limit]
        for c in batch:
            c.state = AdjudicationState.IN_REVIEW
            c.assigned_to = officer
        return batch

    # -- deciding ------------------------------------------------------------------

    def decide(self, case_id: str, value: Any, officer: str, note: str = "") -> AdjudicationCase:
        case = self.cases[case_id]
        case.decided_value = value
        case.decided_by = officer
        case.decided_at = datetime.now(timezone.utc)
        case.decision_note = note
        case.state = AdjudicationState.HUMAN_RESOLVED
        rec = {
            "case_id": case_id,
            "entity_id": case.entity_id,
            "property": case.property_path,
            "platform_proposal": _short(case.proposed.chosen_value),
            "human_decision": _short(value),
            "agreed": _short(value) == _short(case.proposed.chosen_value),
            "officer": officer,
            "note": note,
            "at": case.decided_at.isoformat(),
            "competing_datasets": case.conflict.competing_datasets(),
        }
        self._decisions.append(rec)
        if self.path:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        return case

    # -- feedback ------------------------------------------------------------------

    def reliability_updates(self, prior_weight: float = 20.0
                            ) -> list[tuple[str, str, float]]:
        """Turn decisions into empirical reliability updates for the source registry.

        For each (dataset, property family) pair, the fraction of contested decisions the
        dataset won is a direct estimate of its reliability. It is blended with the prior
        by a Beta posterior mean, so a handful of decisions nudges the prior rather than
        replacing it, and a campaign's worth of decisions eventually dominates it.
        """
        wins: dict[tuple[str, str], int] = defaultdict(int)
        appearances: dict[tuple[str, str], int] = defaultdict(int)
        for rec in self._decisions:
            prop = rec["property"]
            chosen = rec["human_decision"]
            for ds in rec["competing_datasets"]:
                appearances[(ds, prop)] += 1
            case = self.cases.get(rec["case_id"])
            if case is None:
                continue
            for e in case.conflict.evidences:
                if _short(e.claim.value) == chosen:
                    wins[(e.claim.dataset_id, prop)] += 1
        out: list[tuple[str, str, float]] = []
        for key, n in appearances.items():
            w = wins.get(key, 0)
            posterior = (w + prior_weight * 0.5) / (n + prior_weight)
            out.append((key[0], key[1], round(posterior, 4)))
        return out

    def training_examples(self) -> list[dict[str, Any]]:
        """Human decisions as supervised labels for the next model refresh."""
        return [
            {
                "entity_id": r["entity_id"],
                "property": r["property"],
                "label": r["human_decision"],
                "agreed_with_platform": r["agreed"],
                "competing_datasets": r["competing_datasets"],
            }
            for r in self._decisions
        ]

    # -- reporting -----------------------------------------------------------------

    def stats(self) -> QueueStats:
        s = QueueStats(total=len(self.cases))
        for c in self.cases.values():
            s.by_property[c.property_path] = s.by_property.get(c.property_path, 0) + 1
            s.by_batch[c.batch_key] = s.by_batch.get(c.batch_key, 0) + 1
        s.decided = len(self._decisions)
        if self._decisions:
            s.agreement_with_platform = sum(
                1 for r in self._decisions if r["agreed"]) / len(self._decisions)
        s.estimated_officer_hours = len(self.cases) * self.SECONDS_PER_DECISION / 3600.0
        return s

    def to_geojson(self, geometries: dict[str, Any]) -> dict[str, Any]:
        """The queue as a map layer, which is how an officer actually wants to see it."""
        feats = []
        for c in sorted(self.cases.values(), key=lambda x: -x.priority):
            g = geometries.get(c.entity_id)
            if g is None:
                continue
            feats.append({
                "type": "Feature",
                "geometry": g.__geo_interface__ if hasattr(g, "__geo_interface__") else g,
                "properties": {
                    "case_id": c.case_id,
                    "property": c.property_path,
                    "priority": round(c.priority, 4),
                    "severity": c.conflict.severity,
                    "uncertainty": round(c.proposed.uncertainty, 4),
                    "state": c.state.value,
                    "batch": c.batch_key,
                },
            })
        return {"type": "FeatureCollection", "features": feats}


def _short(v: Any, limit: int = 120) -> str:
    s = str(v)
    return s if len(s) <= limit else s[:limit] + f"...[{len(s)} chars]"
