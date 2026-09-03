"""The canonical land-record schema.

Harmonisation needs a target. Without one, integrating N sources is an N-squared mapping
problem and every new department makes it worse; with one it is N mappings and the cost is
linear. This module defines that target.

The schema is aligned with what Indian land governance already mandates rather than being
invented here:

* **LGD** (Local Government Directory, MoPR) supplies the administrative code hierarchy —
  state, district, sub-district, village, ULB, ward. Every Indian government dataset that
  can be joined at all joins on these.
* **ULPIN / Bhu-Aadhaar** (DILRMP) supplies parcel identity.
* **NAKSHA** supplies the urban survey attribute set — survey number, sub-division,
  ownership category, land use, extent.
* **RoR** (Record of Rights) supplies the tenure block — patta number, owner, share,
  encumbrance, classification.
* **OGC Simple Features / ISO 19107** supply geometry, **ISO 19115** the metadata, and
  **INSPIRE Cadastral Parcels** the general shape of the parcel model, which India's
  National Geospatial Policy 2022 is broadly consistent with.

Fields are typed, documented, and marked with the confidence dimension they feed, so the
scorer does not need a separate table of what matters.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Callable


class FieldKind(str, enum.Enum):
    IDENTITY = "identity"
    ADMIN = "admin"
    TENURE = "tenure"
    EXTENT = "extent"
    CLASSIFICATION = "classification"
    STRUCTURE = "structure"
    TEMPORAL = "temporal"
    QUALITY = "quality"


@dataclass(frozen=True)
class CanonicalField:
    name: str
    kind: FieldKind
    dtype: str
    description: str
    required: bool = False
    pii: bool = False
    """Personal data under the DPDP Act 2023. Compartmentalised and never emitted on a
    public API without an explicit purpose grant."""
    unit: str | None = None
    domain: tuple[str, ...] | None = None
    aliases: tuple[str, ...] = ()
    validator: Callable[[Any], bool] | None = None
    example: str = ""


def _is_survey_number(v: Any) -> bool:
    return bool(re.fullmatch(r"[0-9]{1,5}([/\-][0-9A-Za-z]{1,6})*", str(v).strip()))


def _is_lgd(v: Any) -> bool:
    return bool(re.fullmatch(r"\d{1,7}", str(v).strip()))


LAND_USE_DOMAIN = (
    "residential", "commercial", "industrial", "institutional", "mixed_use",
    "agricultural", "water_body", "transport", "public_utility", "open_space",
    "vacant", "poramboke_public", "government", "religious", "burial_ground", "unknown",
)

TENURE_DOMAIN = (
    "patta_private", "government_poramboke", "temple_endowment", "wakf",
    "trust", "corporate", "cooperative_society", "joint", "leasehold",
    "encroached", "disputed", "unknown",
)

CONSTRUCTION_DOMAIN = ("pucca", "semi_pucca", "kutcha", "temporary", "under_construction",
                       "ruined", "unknown")


PARCEL_SCHEMA: dict[str, CanonicalField] = {f.name: f for f in [
    # --- identity -----------------------------------------------------------------
    CanonicalField("ulpin", FieldKind.IDENTITY, "str",
                   "14-character Unique Land Parcel Identification Number (Bhu-Aadhaar).",
                   required=True, example="33GCC3N5CZBR3Y"),
    CanonicalField("entity_id", FieldKind.IDENTITY, "str",
                   "Internal content-addressed handle, stable for identical geometry.",
                   required=True),
    CanonicalField("survey_number", FieldKind.IDENTITY, "str",
                   "Revenue survey number, with sub-division where applicable.",
                   aliases=("survey_no", "sy_no", "s_no", "survey_number", "kide",
                            "old_survey_no", "resurvey_no", "ts_no", "town_survey_no"),
                   validator=_is_survey_number, example="437/2A"),
    CanonicalField("subdivision", FieldKind.IDENTITY, "str",
                   "Sub-division suffix of the survey number, separated for joinability.",
                   aliases=("sub_div", "subdiv", "sub_division", "hissa")),
    CanonicalField("patta_number", FieldKind.IDENTITY, "str",
                   "Patta (title deed) number in the village register.",
                   aliases=("patta_no", "patta", "khata_no", "khata", "khewat")),
    CanonicalField("door_number", FieldKind.IDENTITY, "str",
                   "Municipal door / property tax assessment number.",
                   aliases=("door_no", "assessment_no", "property_id", "ptin",
                            "gcc_gis_id", "bldg_id")),
    # --- administrative -----------------------------------------------------------
    CanonicalField("state_lgd", FieldKind.ADMIN, "str", "LGD state code.",
                   required=True, aliases=("state_code", "lgd_state_code"),
                   validator=_is_lgd, example="33"),
    CanonicalField("district_lgd", FieldKind.ADMIN, "str", "LGD district code.",
                   required=True, aliases=("district_code", "lgd_district_code", "dist_code"),
                   validator=_is_lgd, example="571"),
    CanonicalField("taluk_lgd", FieldKind.ADMIN, "str",
                   "LGD sub-district (taluk / tehsil / mandal) code.",
                   aliases=("taluk_code", "lgd_taluk_code", "tehsil_code", "subdistrict_code")),
    CanonicalField("village_lgd", FieldKind.ADMIN, "str",
                   "LGD revenue village code — the primary join key to every other "
                   "Indian government dataset.",
                   aliases=("village_code", "lgd_village_code", "vill_code")),
    CanonicalField("village_name", FieldKind.ADMIN, "str", "Revenue village name.",
                   aliases=("village", "vill_name", "revenue_village")),
    CanonicalField("taluk_name", FieldKind.ADMIN, "str", "Taluk name.",
                   aliases=("taluk", "tehsil", "taluka", "mandal")),
    CanonicalField("district_name", FieldKind.ADMIN, "str", "District name.",
                   aliases=("district", "dist", "dist_name")),
    CanonicalField("ulb_code", FieldKind.ADMIN, "str",
                   "Urban local body code, where the parcel is inside a municipal area.",
                   aliases=("ulb", "corporation", "municipality")),
    CanonicalField("ward", FieldKind.ADMIN, "str", "Municipal ward number.",
                   aliases=("ward_no", "ward_number", "wardno", "ward_id")),
    CanonicalField("zone", FieldKind.ADMIN, "str", "Municipal zone.",
                   aliases=("zone_no", "zone_number", "zone_name", "region_name")),
    CanonicalField("locality", FieldKind.ADMIN, "str", "Locality / colony / area name.",
                   aliases=("area_name", "colony", "locality_name", "neighbourhood")),
    CanonicalField("street", FieldKind.ADMIN, "str", "Street or road name.",
                   aliases=("road_name", "rd_name", "street_name", "road")),
    # --- tenure -------------------------------------------------------------------
    CanonicalField("tenure_type", FieldKind.TENURE, "str",
                   "Category of holding.", domain=TENURE_DOMAIN,
                   aliases=("tenure", "ownership_type", "land_type", "nature_of_land")),
    CanonicalField("owner_name", FieldKind.TENURE, "str",
                   "Recorded holder of the patta.", pii=True,
                   aliases=("owner", "pattadar", "holder", "name_of_owner", "khatedar")),
    CanonicalField("owner_name_normalised", FieldKind.TENURE, "str",
                   "Owner name transliterated to Latin and normalised for record linkage.",
                   pii=True),
    CanonicalField("owner_share", FieldKind.TENURE, "str",
                   "Fractional share where a parcel is jointly held.",
                   aliases=("share", "share_fraction", "extent_share")),
    CanonicalField("encumbrance", FieldKind.TENURE, "str",
                   "Registered encumbrance summary, if any.",
                   aliases=("mortgage", "charge", "lien")),
    # --- extent -------------------------------------------------------------------
    CanonicalField("recorded_extent_m2", FieldKind.EXTENT, "float",
                   "Extent as written in the record of rights, converted to m².",
                   unit="m2", aliases=("extent", "area_recorded", "patta_extent", "rec_area")),
    CanonicalField("computed_extent_m2", FieldKind.EXTENT, "float",
                   "Geodesic area of the harmonised geometry on the WGS 84 ellipsoid.",
                   unit="m2", required=True),
    CanonicalField("extent_discrepancy_pct", FieldKind.EXTENT, "float",
                   "Percentage difference between recorded and computed extent. The "
                   "single most actionable number in a cadastral audit.",
                   unit="percent"),
    CanonicalField("recorded_extent_display", FieldKind.EXTENT, "str",
                   "Extent rendered in the customary local unit (acre-cent, ground)."),
    # --- classification -----------------------------------------------------------
    CanonicalField("land_use", FieldKind.CLASSIFICATION, "str",
                   "Harmonised land-use class.", domain=LAND_USE_DOMAIN,
                   aliases=("landuse", "land_use_class", "class", "usage", "category")),
    CanonicalField("classification_source", FieldKind.CLASSIFICATION, "str",
                   "Which dataset the land-use decision came from."),
    CanonicalField("is_public_land", FieldKind.CLASSIFICATION, "bool",
                   "True for poramboke / government land. Drives encroachment analysis."),
    # --- structure ----------------------------------------------------------------
    CanonicalField("building_count", FieldKind.STRUCTURE, "int",
                   "Number of harmonised building footprints inside the parcel."),
    CanonicalField("built_up_area_m2", FieldKind.STRUCTURE, "float",
                   "Sum of harmonised footprint areas within the parcel.", unit="m2"),
    CanonicalField("ground_coverage_pct", FieldKind.STRUCTURE, "float",
                   "Built-up area as a percentage of parcel extent — the input to a "
                   "development-control compliance check.", unit="percent"),
    CanonicalField("max_height_m", FieldKind.STRUCTURE, "float",
                   "Maximum structure height from the normalised DSM.", unit="m",
                   aliases=("height", "height_m", "bldg_height")),
    CanonicalField("floors", FieldKind.STRUCTURE, "str",
                   "Recorded floor count or configuration (e.g. G+2).",
                   aliases=("no_floors", "no_floors_", "floors", "storeys")),
    CanonicalField("construction_type", FieldKind.STRUCTURE, "str",
                   "Construction quality class.", domain=CONSTRUCTION_DOMAIN,
                   aliases=("cons_type", "construction", "structure_type", "wall_type")),
    # --- temporal -----------------------------------------------------------------
    CanonicalField("survey_date", FieldKind.TEMPORAL, "date",
                   "Date the authoritative geometry was observed.",
                   aliases=("created_at", "survey_dt", "date_of_survey", "capture_date")),
    CanonicalField("last_mutation_date", FieldKind.TEMPORAL, "date",
                   "Date of the most recent recorded mutation.",
                   aliases=("mutation_date", "updated_at", "last_updated")),
    CanonicalField("change_type", FieldKind.TEMPORAL, "str",
                   "Detected change class relative to the previous epoch."),
    # --- quality ------------------------------------------------------------------
    CanonicalField("confidence", FieldKind.QUALITY, "float",
                   "Composite confidence 0..1.", required=True),
    CanonicalField("confidence_grade", FieldKind.QUALITY, "str",
                   "A..E grade derived from the composite.", domain=("A", "B", "C", "D", "E")),
    CanonicalField("contributing_datasets", FieldKind.QUALITY, "str",
                   "Comma-separated dataset ids that contributed a surviving claim."),
    CanonicalField("conflict_count", FieldKind.QUALITY, "int",
                   "Number of conflicts detected on this entity."),
    CanonicalField("adjudication_state", FieldKind.QUALITY, "str",
                   "Whether the record was auto-resolved or needs human sign-off."),
    CanonicalField("ledger_head", FieldKind.QUALITY, "str",
                   "Hash of the ledger entry that produced this state."),
]}


BUILDING_SCHEMA: dict[str, CanonicalField] = {f.name: f for f in [
    PARCEL_SCHEMA["entity_id"],
    PARCEL_SCHEMA["door_number"],
    PARCEL_SCHEMA["ward"],
    PARCEL_SCHEMA["zone"],
    PARCEL_SCHEMA["locality"],
    PARCEL_SCHEMA["street"],
    PARCEL_SCHEMA["floors"],
    PARCEL_SCHEMA["construction_type"],
    PARCEL_SCHEMA["max_height_m"],
    PARCEL_SCHEMA["confidence"],
    PARCEL_SCHEMA["confidence_grade"],
    PARCEL_SCHEMA["contributing_datasets"],
    CanonicalField("footprint_area_m2", FieldKind.EXTENT, "float",
                   "Geodesic footprint area.", unit="m2", required=True),
    CanonicalField("parcel_ulpin", FieldKind.IDENTITY, "str",
                   "ULPIN of the parcel this structure sits on."),
    CanonicalField("building_use", FieldKind.CLASSIFICATION, "str",
                   "Use class of the structure.", domain=LAND_USE_DOMAIN,
                   aliases=("sub_class", "bldg_use", "usage", "class")),
    CanonicalField("extraction_confidence", FieldKind.QUALITY, "float",
                   "Where the footprint came from a model, the model's own probability."),
]}


@dataclass
class MappingResult:
    """Outcome of mapping one source record onto the canonical schema."""

    canonical: dict[str, Any] = field(default_factory=dict)
    unmapped: dict[str, Any] = field(default_factory=dict)
    coerced: list[str] = field(default_factory=list)
    rejected: list[tuple[str, Any, str]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        total = len(self.canonical) + len(self.unmapped)
        return len(self.canonical) / total if total else 0.0


def validate_record(record: dict[str, Any],
                    schema: dict[str, CanonicalField] | None = None) -> list[str]:
    """Check a canonical record against the schema. Returns human-readable problems."""
    schema = schema or PARCEL_SCHEMA
    problems: list[str] = []
    for name, f in schema.items():
        if f.required and record.get(name) in (None, ""):
            problems.append(f"required field {name!r} is missing")
        value = record.get(name)
        if value in (None, ""):
            continue
        if f.domain and str(value) not in f.domain:
            problems.append(
                f"{name}={value!r} is outside the controlled vocabulary "
                f"({', '.join(f.domain[:6])}...)"
            )
        if f.validator and not f.validator(value):
            problems.append(f"{name}={value!r} fails its format check")
    return problems


def redact_pii(record: dict[str, Any],
               schema: dict[str, CanonicalField] | None = None,
               *, purpose_granted: bool = False) -> dict[str, Any]:
    """Strip DPDP-protected fields unless a purpose grant is present.

    Owner names in a cadastre are personal data. A harmonisation platform inevitably ends
    up as the most convenient source of a full name-to-land mapping for a whole city, which
    is exactly the aggregation the DPDP Act is concerned with. The default on every
    outbound path is therefore redaction, and access is by explicit purpose.
    """
    schema = schema or PARCEL_SCHEMA
    if purpose_granted:
        return dict(record)
    out = dict(record)
    for name, f in schema.items():
        if f.pii and name in out and out[name] not in (None, ""):
            out[name] = "[redacted:dpdp]"
    return out
