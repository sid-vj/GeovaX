"""Domain model for SAMANVAY.

Everything that flows through the platform is expressed in terms of a small number of
first-class concepts:

``SourceDataset``   a dataset as received from a producing authority
``Claim``           one source's assertion about one property of one real-world entity
``Evidence``        a claim plus the weight the platform attaches to it
``MatchPair``       a hypothesis that two features from two sources are the same entity
``Conflict``        two or more irreconcilable claims about the same property
``Resolution``      the platform's decision, with the reasoning that produced it
``HarmonisedFeature`` the single output record for a real-world entity

The design rule is that **nothing is ever silently overwritten**. A source claim is never
deleted; it is superseded by a resolution that names it. That is what makes the output
auditable to a revenue officer and defensible in a land dispute.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable


# --------------------------------------------------------------------------------------
# enumerations
# --------------------------------------------------------------------------------------


class SourceType(str, enum.Enum):
    """The producing mechanism of a dataset.

    The value is used as the key into the source-reliability prior table, so the
    granularity here is deliberately *mechanism*-level rather than *agency*-level: a
    drone-photogrammetry product from any agency has broadly the same error behaviour.
    """

    DRONE_IMAGERY = "drone_imagery"
    ORI = "orthorectified_imagery"
    DSM = "dsm"
    DTM = "dtm"
    POINT_CLOUD = "point_cloud"
    AI_EXTRACTION = "ai_extraction"
    CADASTRAL_MAP = "cadastral_map"
    REVENUE_RECORD = "revenue_record"
    MUNICIPAL_GIS = "municipal_gis"
    UTILITY_NETWORK = "utility_network"
    GROUND_TRUTH = "ground_truth"
    GNSS_CORS = "gnss_cors"
    BUILDING_FOOTPRINT = "building_footprint"
    ADMIN_BOUNDARY = "admin_boundary"
    TRANSPORT_NETWORK = "transport_network"
    HYDROLOGY = "hydrology"


class FeatureClass(str, enum.Enum):
    """The real-world entity class a feature represents."""

    PARCEL = "parcel"
    BUILDING = "building"
    ROAD = "road"
    WATER_BODY = "water_body"
    UTILITY_LINE = "utility_line"
    UTILITY_NODE = "utility_node"
    ADMIN_UNIT = "admin_unit"
    CONTROL_POINT = "control_point"


class Cardinality(str, enum.Enum):
    """Relationship between a source feature and its counterpart(s)."""

    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"      # a source parcel was subdivided
    MANY_TO_ONE = "N:1"      # source parcels were amalgamated
    MANY_TO_MANY = "N:M"     # boundary re-organisation
    UNMATCHED_LEFT = "1:0"   # present only in the reference
    UNMATCHED_RIGHT = "0:1"  # present only in the candidate (new feature)


class ChangeType(str, enum.Enum):
    NEW_CONSTRUCTION = "new_construction"
    DEMOLITION = "demolition"
    EXTENSION = "extension"
    PARTIAL_DEMOLITION = "partial_demolition"
    SUBDIVISION = "subdivision"
    AMALGAMATION = "amalgamation"
    BOUNDARY_ADJUSTMENT = "boundary_adjustment"
    RECLASSIFICATION = "reclassification"
    ATTRIBUTE_ONLY = "attribute_only"
    POSITIONAL_ONLY = "positional_only"
    ENCROACHMENT = "encroachment"
    NO_CHANGE = "no_change"
    # The following are *not* change on the ground. They are disagreement between two
    # contemporaneous sources, and conflating the two is the most damaging mistake a
    # harmonisation platform can make: it would record a mutation for every feature one
    # department happens not to hold.
    SOURCE_OMISSION = "source_omission"        # in the reference, absent from the candidate
    SOURCE_COMMISSION = "source_commission"    # in the candidate, absent from the reference
    GEOMETRIC_DISAGREEMENT = "geometric_disagreement"
    ATTRIBUTE_DISAGREEMENT = "attribute_disagreement"


class ConflictKind(str, enum.Enum):
    GEOMETRIC = "geometric"
    ATTRIBUTE = "attribute"
    TOPOLOGICAL = "topological"
    SEMANTIC = "semantic"
    TEMPORAL = "temporal"
    CARDINALITY = "cardinality"


class ResolutionStrategy(str, enum.Enum):
    HIGHEST_RELIABILITY = "highest_reliability"
    EVIDENCE_FUSION = "evidence_fusion"
    MOST_RECENT = "most_recent"
    GEOMETRIC_MEDIAN = "geometric_median"
    RULE_OVERRIDE = "rule_override"
    HUMAN_ADJUDICATION = "human_adjudication"
    DEFERRED = "deferred"


class AdjudicationState(str, enum.Enum):
    AUTO_RESOLVED = "auto_resolved"
    QUEUED = "queued"
    IN_REVIEW = "in_review"
    HUMAN_RESOLVED = "human_resolved"
    ESCALATED = "escalated"


# --------------------------------------------------------------------------------------
# datasets and claims
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Authority:
    """The organisation accountable for a dataset."""

    code: str
    name: str
    tier: str  # "central" | "state" | "ulb" | "parastatal" | "institutional"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} ({self.code})"


@dataclass
class SourceDataset:
    """A dataset as received, before any harmonisation."""

    dataset_id: str
    title: str
    source_type: SourceType
    authority: Authority
    licence: str
    crs: str
    acquired_on: datetime | None = None
    published_on: datetime | None = None
    feature_count: int | None = None
    positional_accuracy_m: float | None = None
    """1-sigma horizontal accuracy declared or empirically estimated, in metres."""
    completeness: float | None = None
    """Fraction of the AOI the dataset is believed to cover, 0..1."""
    uri: str | None = None
    checksum_sha256: str | None = None
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    # -- provenance tier, kept explicit and never blurred upward --------------------
    tier: str = "official"
    """One of "official" (fetched directly from the authoritative platform's own documented
    API/WMS/WFS/download), "mirror" (a legitimate third-party republication of official data,
    used only when the official platform has no reachable bulk path), or "proxy" (a research/
    open benchmark dataset standing in for a category with no real Indian source available).
    Surfaced verbatim in /api/lineage and the ledger so tier-2/3 sources never look identical
    to tier-1 government data."""
    platform: str = ""
    """The specific portal/service the data was retrieved through, distinct from ``authority``
    (the accountable organisation) — e.g. authority=GCC, platform="OpenCity Urban Data Portal"."""
    original_format: str = ""
    coverage: str = ""
    """Geographic coverage actually acquired, e.g. "Chennai (GCC limits), 200 wards"."""
    transformation: str = ""
    """What was done to the data between acquisition and ingestion, e.g. "reprojected
    EPSG:4326 -> EPSG:32644; clipped to AOI bbox"."""

    @property
    def age_days(self) -> float | None:
        ref = self.acquired_on or self.published_on
        if ref is None:
            return None
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ref).total_seconds() / 86400.0


@dataclass
class Claim:
    """One source's assertion about one property of one entity.

    ``property_path`` is either ``"geometry"`` or a dotted attribute path such as
    ``"tenure.owner_name"``. Keeping geometry and attributes in one structure means the
    conflict machinery, the ledger and the confidence scorer all work uniformly.
    """

    dataset_id: str
    source_type: SourceType
    property_path: str
    value: Any
    observed_on: datetime | None = None
    accuracy_m: float | None = None
    source_feature_id: str | None = None
    extraction_confidence: float | None = None
    """For AI-derived claims: the model's own probability, 0..1."""

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "d": self.dataset_id,
                "p": self.property_path,
                "v": _stable(self.value),
                "f": self.source_feature_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class Evidence:
    """A claim weighted by everything the platform knows about its provenance."""

    claim: Claim
    reliability: float          # prior belief in the source mechanism, 0..1
    recency_weight: float       # 0..1, decays with dataset age
    accuracy_weight: float      # 0..1, from declared/empirical positional accuracy
    corroboration: float = 0.0  # 0..1, agreement with independent sources
    penalty: float = 0.0        # 0..1, subtracted for known defects

    @property
    def mass(self) -> float:
        """Basic probability mass assigned to this claim by Dempster-Shafer fusion.

        Reliability is the base; recency and accuracy *modulate* it rather than
        multiplying it away. The distinction matters more than it looks. Multiplying three
        independent sub-unit factors drives the mass of a perfectly ordinary source — a
        municipal survey, two years old, accurate to a metre — down to about 0.09, which
        makes every claim in the system look like a rumour and sends every decision to a
        human. Modulating instead keeps such a source near 0.5, which is what it deserves,
        while still separating it clearly from a fresh centimetre-accurate observation.
        """
        recency = 0.45 + 0.55 * min(max(self.recency_weight, 0.0), 1.0)
        accuracy = 0.40 + 0.60 * min(max(self.accuracy_weight, 0.0), 1.0)
        w = self.reliability * recency * accuracy
        w *= 1.0 + 0.35 * self.corroboration
        w *= 1.0 - min(max(self.penalty, 0.0), 0.95)
        return max(0.0, min(0.995, w))


# --------------------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------------------


@dataclass
class MatchPair:
    """A hypothesis that a reference feature and a candidate feature are the same entity."""

    left_id: str
    right_id: str
    left_dataset: str
    right_dataset: str
    features: dict[str, float] = field(default_factory=dict)
    probability: float = 0.0
    cardinality: Cardinality = Cardinality.ONE_TO_ONE
    accepted: bool = False
    group_id: str | None = None
    """Set when the pair is part of an N:1 / 1:N / N:M group."""

    def __hash__(self) -> int:  # pragma: no cover - trivial
        return hash((self.left_id, self.right_id))


# --------------------------------------------------------------------------------------
# conflict and resolution
# --------------------------------------------------------------------------------------


@dataclass
class Conflict:
    conflict_id: str
    entity_id: str
    kind: ConflictKind
    property_path: str
    evidences: list[Evidence]
    severity: float             # 0..1
    disagreement: float         # 0..1, normalised spread of the competing claims
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def competing_datasets(self) -> list[str]:
        return sorted({e.claim.dataset_id for e in self.evidences})


@dataclass
class Resolution:
    conflict_id: str
    entity_id: str
    property_path: str
    chosen_value: Any
    strategy: ResolutionStrategy
    belief: float               # DS belief in the chosen value, 0..1
    plausibility: float         # DS plausibility, 0..1
    state: AdjudicationState = AdjudicationState.AUTO_RESOLVED
    rationale: str = ""
    superseded: list[str] = field(default_factory=list)
    """Fingerprints of the claims this resolution overrides."""
    resolved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_by: str = "samanvay/auto"

    @property
    def uncertainty(self) -> float:
        return max(0.0, self.plausibility - self.belief)


# --------------------------------------------------------------------------------------
# confidence
# --------------------------------------------------------------------------------------


@dataclass
class ConfidenceReport:
    """A six-dimension, explainable confidence score for an integrated output.

    A single number is useless to a revenue officer who has to sign the record. The
    platform therefore always emits the components alongside the composite, and a plain
    language explanation of the weakest dimension.
    """

    entity_id: str
    positional: float
    source_agreement: float
    topological: float
    attribute_completeness: float
    temporal_currency: float
    lineage_integrity: float
    weights: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    DEFAULT_WEIGHTS = {
        "positional": 0.28,
        "source_agreement": 0.22,
        "topological": 0.18,
        "attribute_completeness": 0.14,
        "temporal_currency": 0.10,
        "lineage_integrity": 0.08,
    }

    def components(self) -> dict[str, float]:
        return {
            "positional": self.positional,
            "source_agreement": self.source_agreement,
            "topological": self.topological,
            "attribute_completeness": self.attribute_completeness,
            "temporal_currency": self.temporal_currency,
            "lineage_integrity": self.lineage_integrity,
        }

    @property
    def composite(self) -> float:
        w = self.weights or self.DEFAULT_WEIGHTS
        total = sum(w.values()) or 1.0
        return sum(v * w.get(k, 0.0) for k, v in self.components().items()) / total

    @property
    def grade(self) -> str:
        c = self.composite
        if c >= 0.90:
            return "A"   # publishable without review
        if c >= 0.75:
            return "B"   # publishable, flagged
        if c >= 0.60:
            return "C"   # needs desk review
        if c >= 0.40:
            return "D"   # needs field verification
        return "E"       # reject

    def weakest(self) -> tuple[str, float]:
        return min(self.components().items(), key=lambda kv: kv[1])

    def explain(self) -> str:
        name, value = self.weakest()
        pretty = name.replace("_", " ")
        return (
            f"Grade {self.grade} ({self.composite * 100:.1f}%). "
            f"Weakest dimension: {pretty} at {value * 100:.1f}%."
        )


# --------------------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------------------


@dataclass
class HarmonisedFeature:
    """The single authoritative record the platform emits for one real-world entity."""

    entity_id: str
    ulpin: str | None
    feature_class: FeatureClass
    geometry_wkt: str
    crs: str
    attributes: dict[str, Any] = field(default_factory=dict)
    contributing_datasets: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    resolutions: list[Resolution] = field(default_factory=list)
    confidence: ConfidenceReport | None = None
    change: ChangeType = ChangeType.NO_CHANGE
    ledger_head: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_geojson_properties(self) -> dict[str, Any]:
        props: dict[str, Any] = dict(self.attributes)
        props["entity_id"] = self.entity_id
        props["ulpin"] = self.ulpin
        props["feature_class"] = self.feature_class.value
        props["contributing_datasets"] = ",".join(self.contributing_datasets)
        props["change_type"] = self.change.value
        if self.confidence is not None:
            props["confidence"] = round(self.confidence.composite, 4)
            props["confidence_grade"] = self.confidence.grade
            for k, v in self.confidence.components().items():
                props[f"conf_{k}"] = round(v, 4)
        props["ledger_head"] = self.ledger_head
        return props


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _stable(value: Any) -> Any:
    """Make a value JSON-stable for fingerprinting."""
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in sorted(value.items())}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return value


def dump(obj: Any) -> dict[str, Any]:
    """Dataclass -> plain dict with enums and datetimes flattened."""
    return json.loads(json.dumps(asdict(obj), default=_json_default))


def _json_default(o: Any) -> Any:
    if isinstance(o, enum.Enum):
        return o.value
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not serialisable: {type(o)}")


def merge_claims(claims: Iterable[Claim]) -> dict[str, list[Claim]]:
    """Group claims by the property they talk about."""
    out: dict[str, list[Claim]] = {}
    for c in claims:
        out.setdefault(c.property_path, []).append(c)
    return out
