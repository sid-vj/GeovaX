"""Spatial conflict resolution framework.

The resolver sits between evidence fusion and the output record. Its job is to decide, for
every property of every entity, which claim survives — and to make that decision
inspectable, overridable and reversible.

Resolution proceeds through an explicit ladder, and stops at the first rung that applies:

1. **Statutory rules.** Some answers are not a matter of evidence. A parcel classified as
   *poramboke* in the revenue record does not become private because a footprint dataset
   shows a house on it; that is an encroachment, and the platform says so rather than
   quietly resolving in favour of the newer observation. Rules of this kind are declared,
   not learned.
2. **Domain precedence.** For a property family where one source type is authoritative by
   mandate — tenure from the revenue record, ward from the municipal corporation, position
   from GNSS control — precedence applies when that source has spoken.
3. **Evidence fusion.** Dempster-Shafer over the weighted claims, as in ``evidence.py``.
4. **Escalation.** When conflict mass is high, or belief is low, or the fused answer
   contradicts a rule, the conflict goes to a human with everything needed to decide.

Every resolution names the claims it supersedes. Nothing is deleted.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ..core.models import (AdjudicationState, Claim, Conflict, ConflictKind, Evidence,
                           Resolution, ResolutionStrategy, SourceType)
from ..core.registry import SourceRegistry, property_family
from .evidence import build_evidence, fuse, fuse_geometry


# --------------------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------------------


@dataclass
class Rule:
    """A declared constraint that overrides evidence."""

    rule_id: str
    description: str
    applies_to: str                       # property path or family
    predicate: Callable[[str, Sequence[Evidence], dict[str, Any]], bool]
    action: str                           # "escalate" | "force" | "flag"
    forced_value: Any = None
    severity: float = 0.8
    statutory_basis: str = ""


def _has_source(evs: Sequence[Evidence], *types: SourceType) -> Evidence | None:
    for e in evs:
        if e.claim.source_type in types:
            return e
    return None


def _public_land_conflict(prop: str, evs: Sequence[Evidence], ctx: dict[str, Any]) -> bool:
    rev = _has_source(evs, SourceType.REVENUE_RECORD, SourceType.CADASTRAL_MAP)
    if rev is None:
        return False
    if str(rev.claim.value).lower() not in {"poramboke", "government", "poramboke_public",
                                            "government_poramboke"}:
        return False
    return bool(ctx.get("has_structure"))


def _extent_gap(prop: str, evs: Sequence[Evidence], ctx: dict[str, Any]) -> bool:
    pct = ctx.get("extent_discrepancy_pct")
    return pct is not None and abs(float(pct)) > 20.0


def _geometry_far_apart(prop: str, evs: Sequence[Evidence], ctx: dict[str, Any]) -> bool:
    return float(ctx.get("max_pairwise_offset_m", 0.0)) > 10.0


def _control_present(prop: str, evs: Sequence[Evidence], ctx: dict[str, Any]) -> bool:
    return _has_source(evs, SourceType.GNSS_CORS, SourceType.GROUND_TRUTH) is not None


DEFAULT_RULES: list[Rule] = [
    Rule(
        rule_id="R-POR-01",
        description=("A structure detected on land recorded as poramboke/government is an "
                     "encroachment finding, not evidence that the classification is wrong. "
                     "The classification is preserved and the case is escalated."),
        applies_to="land_use",
        predicate=_public_land_conflict,
        action="escalate",
        severity=0.95,
        statutory_basis=("Tamil Nadu Land Encroachment Act 1905; classification of "
                         "poramboke land is a revenue determination, not a survey product."),
    ),
    Rule(
        rule_id="R-EXT-01",
        description=("A discrepancy above 20% between the recorded extent in the record of "
                     "rights and the geodesic area of the harmonised boundary cannot be "
                     "auto-resolved: either the boundary or the patta is wrong, and only a "
                     "field verification distinguishes them."),
        applies_to="extent",
        predicate=_extent_gap,
        action="escalate",
        severity=0.85,
        statutory_basis="DILRMP survey/settlement reconciliation requirement.",
    ),
    Rule(
        rule_id="R-GEO-01",
        description=("Competing boundaries more than 10 m apart are not a digitising "
                     "difference. This is a datum, control or identity error and automated "
                     "fusion would launder it into a false answer."),
        applies_to="geometry",
        predicate=_geometry_far_apart,
        action="escalate",
        severity=0.90,
    ),
    Rule(
        rule_id="R-CTL-01",
        description=("Where GNSS/CORS or ground-truth control exists for the entity, its "
                     "position is authoritative and takes precedence over every "
                     "photogrammetric or cartographic claim."),
        applies_to="geometry",
        predicate=_control_present,
        action="flag",
        severity=0.30,
        statutory_basis="Survey of India CORS network is the national positional reference.",
    ),
]


# --------------------------------------------------------------------------------------
# precedence
# --------------------------------------------------------------------------------------

#: For each property family, source types in descending order of statutory authority.
PRECEDENCE: dict[str, tuple[SourceType, ...]] = {
    "position": (SourceType.GNSS_CORS, SourceType.GROUND_TRUTH, SourceType.ORI,
                 SourceType.POINT_CLOUD, SourceType.DRONE_IMAGERY),
    "tenure": (SourceType.REVENUE_RECORD, SourceType.CADASTRAL_MAP),
    "identity": (SourceType.REVENUE_RECORD, SourceType.CADASTRAL_MAP,
                 SourceType.MUNICIPAL_GIS),
    "extent": (SourceType.REVENUE_RECORD, SourceType.CADASTRAL_MAP),
    "classification": (SourceType.MUNICIPAL_GIS, SourceType.REVENUE_RECORD),
    "network": (SourceType.UTILITY_NETWORK, SourceType.TRANSPORT_NETWORK),
    "shape": (SourceType.ORI, SourceType.POINT_CLOUD, SourceType.AI_EXTRACTION),
}


# --------------------------------------------------------------------------------------
# the resolver
# --------------------------------------------------------------------------------------


@dataclass
class ResolverConfig:
    conflict_escalation_threshold: float = 0.55
    """Dempster conflict mass above which no automatic answer is trusted."""
    belief_floor: float = 0.40
    """Below this belief, escalate even if there is no conflict — it means nobody
    really asserted the answer."""
    uncertainty_ceiling: float = 0.45
    """plausibility - belief above this means the evidence is too thin."""
    geometry_agreement_tolerance_m: float = 0.5
    apply_rules: bool = True
    apply_precedence: bool = True


@dataclass
class ResolutionOutcome:
    resolutions: list[Resolution] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    escalated: list[Conflict] = field(default_factory=list)
    rule_hits: dict[str, int] = field(default_factory=dict)
    strategy_counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        strat = ", ".join(f"{k} {v:,}" for k, v in sorted(self.strategy_counts.items()))
        rules = ", ".join(f"{k} {v}" for k, v in sorted(self.rule_hits.items())) or "none"
        return (
            f"{len(self.resolutions):,} properties resolved, {len(self.conflicts):,} "
            f"conflicts detected, {len(self.escalated):,} escalated for adjudication; "
            f"strategies: {strat}; rules fired: {rules}"
        )


class ConflictResolver:
    def __init__(self, registry: SourceRegistry, config: ResolverConfig | None = None,
                 rules: Sequence[Rule] | None = None,
                 lineage: dict[str, set[str]] | None = None) -> None:
        self.registry = registry
        self.config = config or ResolverConfig()
        self.rules = list(rules if rules is not None else DEFAULT_RULES)
        self.lineage = lineage or {}

    def resolve_entity(self, entity_id: str, claims: Sequence[Claim],
                       context: dict[str, Any] | None = None) -> ResolutionOutcome:
        ctx = context or {}
        out = ResolutionOutcome()
        by_prop: dict[str, list[Claim]] = defaultdict(list)
        for c in claims:
            by_prop[c.property_path].append(c)

        for prop, group in by_prop.items():
            evs = build_evidence(group, self.registry)
            if len(group) == 1:
                e = evs[0]
                out.resolutions.append(Resolution(
                    conflict_id="", entity_id=entity_id, property_path=prop,
                    chosen_value=group[0].value,
                    strategy=ResolutionStrategy.HIGHEST_RELIABILITY,
                    belief=round(e.mass, 4), plausibility=1.0,
                    rationale=(f"only {group[0].dataset_id} asserts this property; "
                               f"accepted with belief {e.mass:.2f} and the remaining mass "
                               f"held as ignorance rather than as support"),
                ))
                out.strategy_counts["single_source"] = out.strategy_counts.get("single_source", 0) + 1
                continue

            values = {_hashable(c.value) for c in group}
            if len(values) == 1 and prop != "geometry":
                out.resolutions.append(Resolution(
                    conflict_id="", entity_id=entity_id, property_path=prop,
                    chosen_value=group[0].value,
                    strategy=ResolutionStrategy.EVIDENCE_FUSION,
                    belief=1.0, plausibility=1.0,
                    rationale=f"all {len(group)} sources agree",
                ))
                out.strategy_counts["unanimous"] = out.strategy_counts.get("unanimous", 0) + 1
                continue

            res, conflict = self._resolve_property(entity_id, prop, group, evs, ctx, out)
            out.resolutions.append(res)
            if conflict is not None:
                out.conflicts.append(conflict)
                if res.state in (AdjudicationState.QUEUED, AdjudicationState.ESCALATED):
                    out.escalated.append(conflict)
        return out

    # -- one property --------------------------------------------------------------

    def _resolve_property(self, entity_id: str, prop: str, claims: Sequence[Claim],
                          evs: Sequence[Evidence], ctx: dict[str, Any],
                          out: ResolutionOutcome) -> tuple[Resolution, Conflict | None]:
        cfg = self.config
        fam = property_family(prop)
        cid = f"CF-{uuid.uuid5(uuid.NAMESPACE_URL, entity_id + prop).hex[:12]}"

        # 1. rules
        if cfg.apply_rules:
            for rule in self.rules:
                if rule.applies_to not in (prop, fam):
                    continue
                try:
                    fired = rule.predicate(prop, evs, ctx)
                except Exception:  # noqa: BLE001
                    fired = False
                if not fired:
                    continue
                out.rule_hits[rule.rule_id] = out.rule_hits.get(rule.rule_id, 0) + 1
                if rule.action == "escalate":
                    conflict = self._make_conflict(cid, entity_id, prop, evs, rule.severity, 1.0)
                    return Resolution(
                        conflict_id=cid, entity_id=entity_id, property_path=prop,
                        chosen_value=self._precedence_value(prop, evs) or claims[0].value,
                        strategy=ResolutionStrategy.RULE_OVERRIDE,
                        belief=0.0, plausibility=1.0,
                        state=AdjudicationState.QUEUED,
                        rationale=f"[{rule.rule_id}] {rule.description}"
                                  + (f" Statutory basis: {rule.statutory_basis}"
                                     if rule.statutory_basis else ""),
                        superseded=[c.fingerprint() for c in claims],
                    ), conflict
                if rule.action == "force":
                    return Resolution(
                        conflict_id=cid, entity_id=entity_id, property_path=prop,
                        chosen_value=rule.forced_value,
                        strategy=ResolutionStrategy.RULE_OVERRIDE,
                        belief=1.0, plausibility=1.0,
                        rationale=f"[{rule.rule_id}] {rule.description}",
                        superseded=[c.fingerprint() for c in claims],
                    ), None

        # 2. precedence
        if cfg.apply_precedence:
            forced = self._precedence_evidence(prop, evs)
            if forced is not None:
                out.strategy_counts["precedence"] = out.strategy_counts.get("precedence", 0) + 1
                return Resolution(
                    conflict_id=cid, entity_id=entity_id, property_path=prop,
                    chosen_value=forced.claim.value,
                    strategy=ResolutionStrategy.HIGHEST_RELIABILITY,
                    belief=round(min(0.99, forced.mass + 0.2), 4), plausibility=1.0,
                    rationale=(f"{forced.claim.source_type.value} is the mandated authority "
                               f"for {fam}; {forced.claim.dataset_id} takes precedence over "
                               f"{len(evs) - 1} other claim(s)"),
                    superseded=[c.fingerprint() for c in claims
                                if c.dataset_id != forced.claim.dataset_id],
                ), self._make_conflict(cid, entity_id, prop, evs, 0.35, _spread(evs))

        # 3. fusion
        if prop == "geometry":
            # The tolerance within which two boundaries count as agreeing is a property of
            # the *sources*, not a global constant: two 3 m-accuracy cadastral compilations
            # that differ by 2 m agree as well as they possibly could, while two 10 cm drone
            # products differing by 2 m emphatically do not. Deriving it from the declared
            # accuracies is what stops the queue filling with cases nobody can resolve.
            accs = [e.claim.accuracy_m for e in evs if e.claim.accuracy_m]
            derived = (math.hypot(*sorted(accs, reverse=True)[:2])
                       if len(accs) >= 2 else (accs[0] if accs else 0.0))
            tol = max(cfg.geometry_agreement_tolerance_m, derived)
            value, bel, pl, k, detail = fuse_geometry(evs, tolerance_m=tol)
            detail["agreement_tolerance_m"] = round(tol, 3)
        else:
            value, bel, pl, k, detail = fuse(evs, key=_hashable, lineage=self.lineage)
        out.strategy_counts["fusion"] = out.strategy_counts.get("fusion", 0) + 1

        uncertainty = max(0.0, pl - bel)
        escalate = (k >= cfg.conflict_escalation_threshold
                    or bel < cfg.belief_floor
                    or uncertainty > cfg.uncertainty_ceiling)

        conflict = self._make_conflict(cid, entity_id, prop, evs, severity=min(1.0, k + 0.2),
                                       disagreement=k)
        rationale = (
            f"Dempster-Shafer fusion over {detail.get('n_evidence', len(evs))} claims "
            f"from {len(set(e.claim.dataset_id for e in evs))} datasets: belief {bel:.3f}, "
            f"plausibility {pl:.3f}, conflict mass {k:.3f}. "
            + (f"Supporting: {detail.get('agreeing_datasets') or detail.get('supporters')}. "
               if detail else "")
            + ("Escalated because "
               + ("sources materially contradict each other"
                  if k >= cfg.conflict_escalation_threshold
                  else "no claim reached the belief floor" if bel < cfg.belief_floor
                  else "the evidence is too thin to distinguish the alternatives")
               if escalate else "Auto-resolved.")
        )
        return Resolution(
            conflict_id=cid, entity_id=entity_id, property_path=prop,
            chosen_value=value if value is not None else claims[0].value,
            strategy=ResolutionStrategy.EVIDENCE_FUSION,
            belief=round(bel, 4), plausibility=round(pl, 4),
            state=AdjudicationState.QUEUED if escalate else AdjudicationState.AUTO_RESOLVED,
            rationale=rationale,
            superseded=[c.fingerprint() for c in claims],
        ), conflict

    # -- helpers -------------------------------------------------------------------

    def _precedence_evidence(self, prop: str, evs: Sequence[Evidence]) -> Evidence | None:
        order = PRECEDENCE.get(property_family(prop))
        if not order:
            return None
        for st in order:
            for e in evs:
                if e.claim.source_type == st:
                    return e
        return None

    def _precedence_value(self, prop: str, evs: Sequence[Evidence]) -> Any:
        e = self._precedence_evidence(prop, evs)
        return e.claim.value if e else None

    @staticmethod
    def _make_conflict(cid: str, entity_id: str, prop: str, evs: Sequence[Evidence],
                       severity: float, disagreement: float) -> Conflict:
        kind = (ConflictKind.GEOMETRIC if prop == "geometry"
                else ConflictKind.ATTRIBUTE)
        return Conflict(
            conflict_id=cid, entity_id=entity_id, kind=kind, property_path=prop,
            evidences=list(evs), severity=round(severity, 4),
            disagreement=round(disagreement, 4),
        )


def _hashable(v: Any) -> Any:
    if isinstance(v, (list, dict, set)):
        return repr(v)
    if isinstance(v, str):
        return v.strip().lower()
    return v


def _spread(evs: Sequence[Evidence]) -> float:
    vals = {_hashable(e.claim.value) for e in evs}
    return 0.0 if len(vals) <= 1 else min(1.0, (len(vals) - 1) / max(len(evs) - 1, 1))
