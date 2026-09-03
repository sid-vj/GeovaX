"""Source registry and reliability priors.

Every fusion decision the platform makes ultimately rests on a prior: *how much do we
believe this kind of source about this kind of property?* Making that prior an explicit,
inspectable, version-controlled table — rather than burying it in the code of a resolver —
is what lets a department argue about it, tune it, and defend a decision in an appeal.

The numbers below are grounded in the accuracy specifications that actually govern these
products in India:

* NAKSHA drone survey: GSD 5 cm, planimetric accuracy specification of ±10 cm.
* GNSS/CORS (CORS network of Survey of India): sub-centimetre in static mode.
* Ground truthing with DGPS: 10–30 cm.
* Cartosat / high-resolution satellite derived footprints (AMRUT, Bhuvan): 2–5 m.
* Municipal GIS from total-station surveys: 0.5–2 m.
* Legacy FMB / village maps scanned and rubber-sheeted: 3–30 m, non-uniform.

The prior is deliberately *property-dependent*. A drone orthophoto is the best source in
the country for **where a wall is**; it knows nothing at all about **who owns it**. The
revenue record is the opposite. Encoding that asymmetry is the single highest-leverage
piece of domain knowledge in the whole platform.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import Authority, SourceDataset, SourceType

# --------------------------------------------------------------------------------------
# authorities
# --------------------------------------------------------------------------------------

AUTHORITIES: dict[str, Authority] = {
    "DOLR": Authority("DOLR", "Department of Land Resources, Ministry of Rural Development", "central"),
    "SOI": Authority("SOI", "Survey of India", "central"),
    "NRSC": Authority("NRSC", "National Remote Sensing Centre, ISRO", "central"),
    "SAC": Authority("SAC", "Space Applications Centre, ISRO", "central"),
    "MOHUA": Authority("MOHUA", "Ministry of Housing and Urban Affairs", "central"),
    "TNGIS": Authority("TNGIS", "Tamil Nadu Geographic Information System, TNeGA", "state"),
    "TNREV": Authority("TNREV", "Revenue Department, Government of Tamil Nadu", "state"),
    "GCC": Authority("GCC", "Greater Chennai Corporation", "ulb"),
    "CMDA": Authority("CMDA", "Chennai Metropolitan Development Authority", "parastatal"),
    "CMWSSB": Authority("CMWSSB", "Chennai Metropolitan Water Supply and Sewerage Board", "parastatal"),
    "NCSCM": Authority("NCSCM", "National Centre for Sustainable Coastal Management", "institutional"),
    "ODM": Authority("ODM", "OpenDroneMap public survey corpus", "institutional"),
}


# --------------------------------------------------------------------------------------
# priors
# --------------------------------------------------------------------------------------

# Property families the prior discriminates between.
GEOMETRY = "geometry"
POSITION = "position"       # absolute placement
SHAPE = "shape"             # relative form, independent of placement
TENURE = "tenure"           # ownership, rights, encumbrance
EXTENT = "extent"           # recorded area
CLASSIFICATION = "classification"  # land use, building type
IDENTITY = "identity"       # survey number, door number, ward number
NETWORK = "network"         # connectivity of utility/transport


@dataclass(frozen=True)
class ReliabilityProfile:
    """Prior belief, per property family, in the range 0..1."""

    position: float
    shape: float
    tenure: float
    extent: float
    classification: float
    identity: float
    network: float
    nominal_accuracy_m: float
    half_life_days: float
    """Days after which the recency weight falls to 0.5. Encodes how fast a source rots."""

    def for_property(self, property_path: str) -> float:
        fam = property_family(property_path)
        return getattr(self, fam, 0.5)


def property_family(property_path: str) -> str:
    p = property_path.lower()
    if p in {"geometry", "geom", "boundary", "footprint"}:
        return "position"
    head = p.split(".")[0]
    return {
        "geometry": "position",
        "position": "position",
        "shape": "shape",
        "tenure": "tenure",
        "owner": "tenure",
        "extent": "extent",
        "area": "extent",
        "classification": "classification",
        "landuse": "classification",
        "identity": "identity",
        "survey": "identity",
        "network": "network",
    }.get(head, "classification")


PRIORS: dict[SourceType, ReliabilityProfile] = {
    #                              pos   shape tenure extent class ident net   acc_m  half-life
    SourceType.GNSS_CORS:          ReliabilityProfile(0.99, 0.60, 0.05, 0.55, 0.05, 0.10, 0.10, 0.02, 3650),
    SourceType.GROUND_TRUTH:       ReliabilityProfile(0.96, 0.80, 0.45, 0.75, 0.80, 0.75, 0.55, 0.20, 1095),
    SourceType.ORI:                ReliabilityProfile(0.93, 0.92, 0.02, 0.80, 0.55, 0.05, 0.35, 0.10, 1095),
    SourceType.DRONE_IMAGERY:      ReliabilityProfile(0.88, 0.90, 0.02, 0.72, 0.50, 0.05, 0.30, 0.15, 730),
    SourceType.DSM:                ReliabilityProfile(0.85, 0.75, 0.02, 0.55, 0.60, 0.02, 0.10, 0.25, 730),
    SourceType.DTM:                ReliabilityProfile(0.85, 0.60, 0.02, 0.45, 0.35, 0.02, 0.10, 0.25, 1460),
    SourceType.POINT_CLOUD:        ReliabilityProfile(0.92, 0.88, 0.02, 0.70, 0.60, 0.02, 0.20, 0.08, 730),
    SourceType.AI_EXTRACTION:      ReliabilityProfile(0.72, 0.70, 0.02, 0.62, 0.72, 0.05, 0.45, 0.60, 1095),
    SourceType.CADASTRAL_MAP:      ReliabilityProfile(0.55, 0.72, 0.88, 0.90, 0.62, 0.95, 0.15, 3.00, 2555),
    SourceType.REVENUE_RECORD:     ReliabilityProfile(0.20, 0.25, 0.96, 0.93, 0.70, 0.97, 0.05, 8.00, 1825),
    SourceType.MUNICIPAL_GIS:      ReliabilityProfile(0.74, 0.76, 0.42, 0.66, 0.86, 0.88, 0.70, 1.00, 1460),
    SourceType.BUILDING_FOOTPRINT: ReliabilityProfile(0.76, 0.82, 0.05, 0.70, 0.68, 0.35, 0.20, 1.20, 1460),
    SourceType.UTILITY_NETWORK:    ReliabilityProfile(0.62, 0.60, 0.05, 0.25, 0.72, 0.55, 0.94, 2.50, 1095),
    SourceType.TRANSPORT_NETWORK:  ReliabilityProfile(0.70, 0.68, 0.05, 0.30, 0.78, 0.60, 0.90, 2.00, 1460),
    SourceType.ADMIN_BOUNDARY:     ReliabilityProfile(0.68, 0.70, 0.30, 0.60, 0.90, 0.92, 0.30, 3.00, 1825),
    SourceType.HYDROLOGY:          ReliabilityProfile(0.66, 0.64, 0.20, 0.55, 0.84, 0.50, 0.60, 3.00, 730),
}

DEFAULT_PRIOR = ReliabilityProfile(0.50, 0.50, 0.50, 0.50, 0.50, 0.50, 0.50, 5.00, 730)


class SourceRegistry:
    """Holds the datasets known to a run and answers reliability questions about them."""

    def __init__(self) -> None:
        self._datasets: dict[str, SourceDataset] = {}
        self._overrides: dict[tuple[str, str], float] = {}

    # -- registration -------------------------------------------------------------

    def register(self, dataset: SourceDataset) -> SourceDataset:
        self._datasets[dataset.dataset_id] = dataset
        return dataset

    def get(self, dataset_id: str) -> SourceDataset | None:
        return self._datasets.get(dataset_id)

    def all(self) -> list[SourceDataset]:
        return list(self._datasets.values())

    def by_type(self, source_type: SourceType) -> list[SourceDataset]:
        return [d for d in self._datasets.values() if d.source_type == source_type]

    # -- priors -------------------------------------------------------------------

    def profile(self, dataset_id: str) -> ReliabilityProfile:
        ds = self._datasets.get(dataset_id)
        if ds is None:
            return DEFAULT_PRIOR
        return PRIORS.get(ds.source_type, DEFAULT_PRIOR)

    def override(self, dataset_id: str, property_path: str, value: float) -> None:
        """Pin a reliability for one dataset and property.

        Used when empirical validation against ground truth contradicts the prior — the
        platform measures each dataset's real positional error in ``quality.accuracy`` and
        feeds the result back here, so the priors are self-correcting over a campaign.
        """
        self._overrides[(dataset_id, property_family(property_path))] = float(value)

    def reliability(self, dataset_id: str, property_path: str) -> float:
        fam = property_family(property_path)
        if (dataset_id, fam) in self._overrides:
            return self._overrides[(dataset_id, fam)]
        return self.profile(dataset_id).for_property(property_path)

    def recency_weight(self, dataset_id: str) -> float:
        ds = self._datasets.get(dataset_id)
        prof = self.profile(dataset_id)
        if ds is None or ds.age_days is None:
            return 0.75
        return 0.5 ** (ds.age_days / max(prof.half_life_days, 1.0))

    def accuracy_weight(self, dataset_id: str, target_accuracy_m: float = 1.0) -> float:
        """Map a dataset's positional accuracy onto 0..1 against a target specification.

        A logistic on the log-ratio: exactly at target scores about 0.70, twice as good
        0.85, twice as bad 0.50, ten times as bad 0.20. The obvious alternative,
        ``target/(target+error)``, decays far too fast — it scores a metre-accurate
        municipal survey at 0.33 against a half-metre target, which is the same score it
        would give something an order of magnitude worse.
        """
        ds = self._datasets.get(dataset_id)
        prof = self.profile(dataset_id)
        acc = (ds.positional_accuracy_m if ds and ds.positional_accuracy_m else prof.nominal_accuracy_m)
        acc = max(acc, 1e-3)
        ratio = math.log10(acc / max(target_accuracy_m, 1e-6))
        return 1.0 / (1.0 + math.exp(1.9 * ratio - 0.85))


@dataclass
class RegistryStats:
    datasets: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    total_features: int = 0

    @classmethod
    def of(cls, reg: SourceRegistry) -> "RegistryStats":
        s = cls(datasets=len(reg.all()))
        for d in reg.all():
            s.by_type[d.source_type.value] = s.by_type.get(d.source_type.value, 0) + 1
            s.total_features += d.feature_count or 0
        return s
