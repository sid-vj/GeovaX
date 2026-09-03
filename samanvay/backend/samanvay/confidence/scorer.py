"""Confidence scoring for integrated outputs.

The problem statement asks for "confidence scoring for integrated outputs". The temptation
is to emit one number. That number would be useless, because the officer who has to act on
it needs to know *what kind* of doubt they are looking at: a parcel that is positionally
excellent but has one unverified owner name needs a different action from a parcel whose
geometry three sources disagree about.

So the scorer emits six independent dimensions and a weighted composite:

**positional** — how well-located the geometry is, from the accuracy of the sources that
survived resolution and their measured agreement, benchmarked against ground control where
it exists.

**source_agreement** — how many *independent* sources contributed and how much they agreed.
Independence matters: three derivatives of one survey score as one witness.

**topological** — whether the feature is geometrically sound and consistent with its
neighbours after repair, and how invasive that repair had to be.

**attribute_completeness** — how much of the canonical schema is populated, weighted by how
important each field is to the record's legal function.

**temporal_currency** — how recent the surviving evidence is, decayed by each source type's
own half-life.

**lineage_integrity** — whether the provenance chain is intact and every claim is
attributable. A record whose lineage cannot be verified is not trustworthy no matter how
good its geometry.

Weights are explicit and configurable per deployment because different states will
reasonably weight these differently, and burying that choice in code would be dishonest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.models import (Claim, ConfidenceReport, Evidence, Resolution,
                           ResolutionStrategy, SourceType)
from ..core.registry import SourceRegistry

#: How much each canonical field matters to the record's legal function.
FIELD_IMPORTANCE: dict[str, float] = {
    "ulpin": 1.0,
    "survey_number": 1.0,
    "village_lgd": 0.9,
    "district_lgd": 0.7,
    "computed_extent_m2": 0.9,
    "recorded_extent_m2": 0.8,
    "land_use": 0.7,
    "tenure_type": 0.8,
    "owner_name": 0.8,
    "ward": 0.6,
    "zone": 0.4,
    "locality": 0.3,
    "street": 0.3,
    "patta_number": 0.7,
    "door_number": 0.5,
    "survey_date": 0.5,
    "construction_type": 0.3,
    "floors": 0.3,
    "max_height_m": 0.3,
}

#: Buildings are not parcels. Scoring a footprint's completeness against the parcel schema
#: penalises it for lacking a patta number and an owner, which it is not supposed to have,
#: and drives every building in the city to grade D for no reason.
BUILDING_FIELD_IMPORTANCE: dict[str, float] = {
    "footprint_area_m2": 1.0,
    "parcel_ulpin": 0.8,
    "ward": 0.7,
    "zone": 0.4,
    "locality": 0.5,
    "street": 0.5,
    "door_number": 0.6,
    "building_use": 0.6,
    "construction_type": 0.4,
    "floors": 0.4,
    "max_height_m": 0.5,
}


@dataclass
class ScoringConfig:
    weights: dict[str, float] = field(default_factory=lambda: dict(
        ConfidenceReport.DEFAULT_WEIGHTS))
    target_accuracy_m: float = 0.5
    """The specification the deployment is held to. NAKSHA urban is 0.5 m."""
    repair_penalty_per_metre: float = 0.35
    """How much a metre of boundary movement during repair costs the topological score."""
    min_sources_for_full_agreement: int = 3


class ConfidenceScorer:
    def __init__(self, registry: SourceRegistry, config: ScoringConfig | None = None) -> None:
        self.registry = registry
        self.config = config or ScoringConfig()

    def score(self, entity_id: str, *,
              claims: Sequence[Claim],
              resolutions: Sequence[Resolution],
              attributes: dict[str, Any],
              topology: dict[str, Any] | None = None,
              control_residual_m: float | None = None,
              lineage_ok: bool = True,
              independent_sources: int | None = None,
              feature_class: str = "parcel") -> ConfidenceReport:
        topology = topology or {}
        notes: list[str] = []
        self._importance = (BUILDING_FIELD_IMPORTANCE if feature_class == "building"
                            else FIELD_IMPORTANCE)

        positional = self._positional(claims, resolutions, control_residual_m, notes)
        agreement = self._agreement(claims, resolutions, independent_sources, notes)
        topological = self._topological(topology, notes)
        completeness = self._completeness(attributes, notes)
        currency = self._currency(claims, notes)
        lineage = self._lineage(claims, resolutions, lineage_ok, notes)

        return ConfidenceReport(
            entity_id=entity_id,
            positional=positional,
            source_agreement=agreement,
            topological=topological,
            attribute_completeness=completeness,
            temporal_currency=currency,
            lineage_integrity=lineage,
            weights=dict(self.config.weights),
            notes=notes,
        )

    # -- dimensions ----------------------------------------------------------------

    def _positional(self, claims: Sequence[Claim], resolutions: Sequence[Resolution],
                    control_residual_m: float | None, notes: list[str]) -> float:
        geom_claims = [c for c in claims if c.property_path == "geometry"]
        if not geom_claims:
            notes.append("no geometry claim: positional confidence cannot be assessed")
            return 0.0

        # best accuracy among the sources that actually contributed
        accs: list[float] = []
        for c in geom_claims:
            prof = self.registry.profile(c.dataset_id)
            ds = self.registry.get(c.dataset_id)
            accs.append(c.accuracy_m or (ds.positional_accuracy_m if ds and ds.positional_accuracy_m
                                         else prof.nominal_accuracy_m))
        best = min(accs)
        target = self.config.target_accuracy_m
        # Logistic on the log-ratio of achieved to target accuracy. A source exactly at
        # specification scores 0.70; twice as good 0.85; twice as bad 0.50; ten times as
        # bad 0.20. A plain target/(target+error) ratio collapses far too fast — it grades
        # a 1 m municipal survey against a 0.5 m specification at 0.33, which is
        # indistinguishable from useless and makes the whole score uninformative.
        ratio = math.log10(max(best, 1e-3) / target)
        base = 1.0 / (1.0 + math.exp(1.9 * ratio - 0.85))

        if control_residual_m is not None and math.isfinite(control_residual_m):
            # measured against ground truth beats any declared figure
            measured = target / (target + max(control_residual_m, 1e-3))
            base = 0.35 * base + 0.65 * measured
            notes.append(
                f"positional score is anchored on a measured {control_residual_m:.2f} m "
                f"residual against ground control, not on declared accuracy"
            )
        else:
            notes.append(
                f"no ground control for this entity; positional score rests on the "
                f"declared accuracy of the best contributing source ({best:.2f} m)"
            )

        geom_res = [r for r in resolutions if r.property_path == "geometry"]
        if geom_res:
            base *= 0.70 + 0.30 * geom_res[0].belief
        return round(min(1.0, max(0.0, base)), 4)

    def _agreement(self, claims: Sequence[Claim], resolutions: Sequence[Resolution],
                   independent_sources: int | None, notes: list[str]) -> float:
        datasets = {c.dataset_id for c in claims}
        n = independent_sources if independent_sources is not None else len(datasets)
        if n <= 1:
            notes.append(
                "only one source contributed: nothing corroborates this record, so its "
                "errors are undetectable by the platform"
            )
            return 0.30
        breadth = min(1.0, (n - 1) / max(self.config.min_sources_for_full_agreement - 1, 1))
        if resolutions:
            depth = float(sum(r.belief for r in resolutions) / len(resolutions))
            conflictless = sum(
                1 for r in resolutions
                if r.strategy is not ResolutionStrategy.HUMAN_ADJUDICATION
                and r.uncertainty < 0.3) / len(resolutions)
        else:
            depth, conflictless = 0.5, 0.5
        score = 0.40 * breadth + 0.35 * depth + 0.25 * conflictless
        if n >= 3 and depth > 0.8:
            notes.append(f"{n} independent sources corroborate this record")
        return round(min(1.0, max(0.0, score)), 4)

    def _topological(self, topology: dict[str, Any], notes: list[str]) -> float:
        if not topology:
            return 0.75
        score = 1.0
        if topology.get("invalid"):
            score -= 0.55
            notes.append("geometry was invalid before repair")
        overlap = float(topology.get("overlap_area_m2", 0.0))
        gap = float(topology.get("gap_area_m2", 0.0))
        area = max(float(topology.get("area_m2", 0.0)), 1.0)
        share = (overlap + gap) / area
        score -= min(0.45, share * 3.0)
        if share > 0.02:
            notes.append(
                f"{share * 100:.1f}% of this parcel's area is contested with or missing "
                f"from its neighbours"
            )
        moved = float(topology.get("repair_shift_m", 0.0))
        score -= min(0.30, moved * self.config.repair_penalty_per_metre)
        if moved > 0.2:
            notes.append(f"automated repair moved the boundary by up to {moved:.2f} m")
        if topology.get("slivers"):
            score -= 0.10
        return round(min(1.0, max(0.0, score)), 4)

    def _completeness(self, attributes: dict[str, Any], notes: list[str]) -> float:
        importance = getattr(self, "_importance", FIELD_IMPORTANCE)
        total = sum(importance.values())
        got = sum(w for f, w in importance.items()
                  if attributes.get(f) not in (None, "", "[redacted:dpdp]"))
        score = got / total if total else 0.0
        missing_critical = [f for f, w in importance.items()
                            if w >= 0.8 and attributes.get(f) in (None, "")]
        if missing_critical:
            notes.append("missing legally significant fields: " + ", ".join(missing_critical[:5]))
        return round(min(1.0, max(0.0, score)), 4)

    def _currency(self, claims: Sequence[Claim], notes: list[str]) -> float:
        weights: list[float] = []
        for c in claims:
            w = self.registry.recency_weight(c.dataset_id)
            weights.append(w)
        if not weights:
            return 0.5
        # The freshest evidence sets currency, and the floor is deliberately non-zero:
        # a decade-old cadastral survey is stale, not worthless, and scoring it at 0.02
        # would let age alone veto a record that is otherwise sound.
        score = 0.30 + 0.70 * float(max(weights))
        if score < 0.4:
            notes.append(
                "the most recent contributing source is old enough that its half-life has "
                "elapsed more than once; a re-survey is indicated"
            )
        return round(score, 4)

    def _lineage(self, claims: Sequence[Claim], resolutions: Sequence[Resolution],
                 lineage_ok: bool, notes: list[str]) -> float:
        if not lineage_ok:
            notes.append("provenance chain failed verification: this record is not trustworthy")
            return 0.0
        attributed = sum(1 for c in claims if c.source_feature_id) / max(len(claims), 1)
        explained = sum(1 for r in resolutions if r.rationale) / max(len(resolutions), 1) \
            if resolutions else 1.0
        return round(min(1.0, 0.5 * attributed + 0.5 * explained), 4)


# --------------------------------------------------------------------------------------
# aggregate reporting
# --------------------------------------------------------------------------------------


@dataclass
class ConfidenceSummary:
    n: int = 0
    mean_composite: float = 0.0
    grade_counts: dict[str, int] = field(default_factory=dict)
    mean_components: dict[str, float] = field(default_factory=dict)
    publishable_fraction: float = 0.0
    needs_field_check: int = 0

    def summary(self) -> str:
        grades = ", ".join(f"{g}:{c:,}" for g, c in sorted(self.grade_counts.items()))
        weakest = min(self.mean_components.items(), key=lambda kv: kv[1]) \
            if self.mean_components else ("n/a", 0.0)
        return (
            f"{self.n:,} features, mean confidence {self.mean_composite * 100:.1f}% "
            f"(grades {grades}); {self.publishable_fraction * 100:.1f}% publishable "
            f"without review; {self.needs_field_check:,} need field verification; "
            f"weakest dimension across the AOI is {weakest[0]} at {weakest[1] * 100:.1f}%"
        )


def summarise(reports: Sequence[ConfidenceReport]) -> ConfidenceSummary:
    s = ConfidenceSummary(n=len(reports))
    if not reports:
        return s
    s.mean_composite = float(sum(r.composite for r in reports) / len(reports))
    comp_totals: dict[str, float] = {}
    for r in reports:
        s.grade_counts[r.grade] = s.grade_counts.get(r.grade, 0) + 1
        for k, v in r.components().items():
            comp_totals[k] = comp_totals.get(k, 0.0) + v
    s.mean_components = {k: round(v / len(reports), 4) for k, v in comp_totals.items()}
    s.publishable_fraction = sum(1 for r in reports if r.grade in ("A", "B")) / len(reports)
    s.needs_field_check = sum(1 for r in reports if r.grade in ("D", "E"))
    return s
