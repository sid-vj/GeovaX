"""The real-data catalogue.

Every dataset SAMANVAY is demonstrated on is real, openly licensed data published by an
Indian government body or an institutional programme. Nothing here is synthetic, and
nothing is fabricated to make a demonstration look better than it is — the discrepancies
between these datasets are the actual discrepancies that exist in Indian urban land data
today, which is exactly what makes them the right test.

Each entry records the issuing authority, the licence, the mirror the bytes are fetched
from, and the size, so the whole corpus is reproducible from a clean machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

GITHUB_RELEASE = "https://github.com/{repo}/releases/download/{tag}/{asset}"


@dataclass(frozen=True)
class DataSource:
    key: str
    title: str
    authority_code: str
    authority_name: str
    licence: str
    source_type: str
    feature_class: str
    crs: str
    url: str
    filename: str
    archive: Literal["7z", "none"] = "none"
    member: str | None = None
    approx_bytes: int = 0
    upstream: str = ""
    """Where the data originally came from, before the mirror."""
    accuracy_m: float | None = None
    vintage: str = ""
    notes: str = ""
    role: str = ""
    """Which requirement of the problem statement this dataset satisfies."""


def _gh(repo: str, tag: str, asset: str) -> str:
    return GITHUB_RELEASE.format(repo=repo, tag=tag, asset=asset)


CATALOGUE: dict[str, DataSource] = {
    # ---------------------------------------------------------------- cadastral
    "tngis_cadastre": DataSource(
        key="tngis_cadastre",
        title="Tamil Nadu cadastral parcels (survey-number level)",
        authority_code="TNGIS",
        authority_name="Tamil Nadu Geographic Information System, TNeGA",
        licence="CC0-1.0",
        source_type="cadastral_map",
        feature_class="parcel",
        crs="EPSG:4326",
        url=_gh("ramSeraph/indian_cadastrals", "tamil-nadu", "TNGIS_TN_Cadastrals.geojsonl.7z"),
        filename="TNGIS_TN_Cadastrals.geojsonl.7z",
        archive="7z",
        member="TNGIS_TN_Cadastrals.geojsonl",
        approx_bytes=417_394_406,
        upstream="Tamil Nadu Geographic Information System (tngis.tn.gov.in) village cadastral service",
        accuracy_m=3.0,
        vintage="2023",
        role="PS requirement: 'Existing cadastral maps' and 'Revenue records'. "
             "6.02 million real survey-number parcels with LGD district/taluk/village "
             "codes, survey numbers and FMB flags.",
        notes="Carries lgd_village_code, which is the join key to the LGD directory and "
              "therefore to every other government dataset in India.",
    ),
    "ncscm_cadastre": DataSource(
        key="ncscm_cadastre",
        title="Coastal Tamil Nadu cadastral parcels",
        authority_code="NCSCM",
        authority_name="National Centre for Sustainable Coastal Management, MoEFCC",
        licence="CC0-1.0",
        source_type="cadastral_map",
        feature_class="parcel",
        crs="EPSG:4326",
        url=_gh("ramSeraph/indian_cadastrals", "tamil-nadu", "NCSCM_TN_Cadastrals.geojsonl.7z"),
        filename="NCSCM_TN_Cadastrals.geojsonl.7z",
        archive="7z",
        member="NCSCM_TN_Cadastrals.geojsonl",
        approx_bytes=25_093_639,
        upstream="NCSCM Coastal Zone Management Plan cadastral compilation",
        accuracy_m=5.0,
        vintage="2019",
        role="PS requirement: a genuinely *independent* second cadastral source over the "
             "same ground. Different schema, different vintage, different digitisation "
             "lineage — this is what the spatial matcher and conflict resolver are "
             "actually tested against.",
        notes="Village/Taluk/District as names rather than LGD codes, Survey_Number as a "
              "free-text field, and a precomputed Shape_Area that disagrees with the "
              "geodesic area. All three are realistic harmonisation problems.",
    ),
    # ---------------------------------------------------------------- buildings
    "gcc_buildings": DataSource(
        key="gcc_buildings",
        title="Greater Chennai Corporation building footprints",
        authority_code="GCC",
        authority_name="Greater Chennai Corporation via TNGIS",
        licence="CC0-1.0",
        source_type="municipal_gis",
        feature_class="building",
        crs="EPSG:4326",
        url=_gh("ramSeraph/indian_buildings", "urban", "TNGIS_GCC_Chennai_Buildings.geojsonl.7z"),
        filename="TNGIS_GCC_Chennai_Buildings.geojsonl.7z",
        archive="7z",
        member="TNGIS_GCC_Chennai_buildings.geojsonl",
        approx_bytes=52_942_353,
        upstream="Greater Chennai Corporation GIS property survey",
        accuracy_m=1.0,
        vintage="2024",
        role="PS requirements: 'Building footprint datasets', 'Municipal GIS layers'. "
             "964,053 surveyed footprints carrying zone, ward, locality, road name and "
             "the GCC GIS identifier — i.e. the municipal attribute fabric.",
    ),
    "amrut_buildings": DataSource(
        key="amrut_buildings",
        title="AMRUT urban building footprints, Tamil Nadu",
        authority_code="NRSC",
        authority_name="National Remote Sensing Centre / Bhuvan, under AMRUT (MoHUA)",
        licence="CC0-1.0",
        source_type="building_footprint",
        feature_class="building",
        crs="EPSG:4326",
        url=_gh("ramSeraph/indian_buildings", "urban", "TN_AMRUT_Buildings.geojsonl.7z"),
        filename="TN_AMRUT_Buildings.geojsonl.7z",
        archive="7z",
        member="TN_AMRUT_Buildings.geojsonl",
        approx_bytes=107_537_000,
        upstream="Bhuvan AMRUT urban geospatial database (bhuvan.nrsc.gov.in)",
        accuracy_m=2.5,
        vintage="2024",
        role="PS requirement: 'Building footprint datasets' from a satellite-derived "
             "national programme, with construction type and floor count — the "
             "attribute-rich counterpart to the municipal survey.",
        notes="Covers AMRUT towns; does not cover the Greater Chennai area, which is "
              "itself a finding the platform reports as a completeness gap rather than "
              "hiding.",
    ),
    "google_open_buildings": DataSource(
        key="google_open_buildings",
        title="Google Open Buildings v3, India 2023",
        authority_code="GOOGLE",
        authority_name="Google Research (Open Buildings)",
        licence="CC-BY-4.0",
        source_type="ai_extraction",
        feature_class="building",
        crs="EPSG:4326",
        url=_gh("ramSeraph/indian_buildings", "GOBI-2023",
                "google-open-buildings-india-2023.010001.parquet"),
        filename="gobi_010001.parquet",
        approx_bytes=1_389_313_457,
        upstream="sites.research.google/open-buildings",
        accuracy_m=1.8,
        vintage="2023",
        role="PS requirement: 'AI-generated feature extraction outputs'. This is a real "
             "ML segmentation product with a per-instance model confidence, a presence "
             "score and an estimated height — precisely the kind of output the platform "
             "is asked to reconcile against surveyed data.",
        notes="Partition 010001 of the India release covers 79.80E-83.37E, "
              "10.27N-18.71N, which contains Chennai.",
    ),
    # ---------------------------------------------------------------- municipal
    "gcc_wards": DataSource(
        key="gcc_wards",
        title="Greater Chennai Corporation ward boundaries (201 wards)",
        authority_code="GCC",
        authority_name="Greater Chennai Corporation",
        licence="CC-BY-4.0",
        source_type="admin_boundary",
        feature_class="admin_unit",
        crs="EPSG:4326",
        url="https://raw.githubusercontent.com/yashveeeeeeer/india-geodata/main/"
            "data/urban/municipal-boundaries/chennai/Wards.geojson",
        filename="chennai_wards.geojson",
        approx_bytes=1_200_000,
        upstream="Greater Chennai Corporation / DataMeet municipal spatial data",
        accuracy_m=5.0,
        vintage="2011 delimitation",
        role="PS requirement: 'Municipal GIS layers'. Supplies the administrative "
             "hierarchy the ULPIN is minted against and the unit of reporting the "
             "corporation actually works in.",
    ),
    "gcc_zones": DataSource(
        key="gcc_zones",
        title="Greater Chennai Corporation zone boundaries (16 zones)",
        authority_code="GCC",
        authority_name="Greater Chennai Corporation",
        licence="CC-BY-4.0",
        source_type="admin_boundary",
        feature_class="admin_unit",
        crs="EPSG:4326",
        url="https://raw.githubusercontent.com/yashveeeeeeer/india-geodata/main/"
            "data/urban/municipal-boundaries/chennai/Zones.geojson",
        filename="chennai_zones.geojson",
        approx_bytes=400_000,
        upstream="Greater Chennai Corporation / DataMeet municipal spatial data",
        accuracy_m=5.0,
        vintage="2011",
        role="PS requirement: 'Municipal GIS layers' — the supervisory tier above wards.",
    ),
    "cma_boundary": DataSource(
        key="cma_boundary",
        title="Chennai Metropolitan Area boundary",
        authority_code="CMDA",
        authority_name="Chennai Metropolitan Development Authority",
        licence="CC-BY-4.0",
        source_type="admin_boundary",
        feature_class="admin_unit",
        crs="EPSG:4326",
        url="https://raw.githubusercontent.com/yashveeeeeeer/india-geodata/main/"
            "data/urban/municipal-boundaries/chennai/CMA.geojson",
        filename="chennai_cma.geojson",
        approx_bytes=300_000,
        upstream="CMDA / DataMeet",
        accuracy_m=10.0,
        vintage="2011",
        role="Defines the planning-authority extent used for inter-departmental scoping.",
    ),
    # ---------------------------------------------------------------- raster
    "uav_ori_odm": DataSource(
        key="uav_ori_odm",
        title="UAV orthorectified imagery — OpenDroneMap 3.0.0 reconstruction",
        authority_code="ODM",
        authority_name="OpenDroneMap public survey corpus (UAVArena)",
        licence="CC-BY-4.0",
        source_type="orthorectified_imagery",
        feature_class="raster",
        crs="EPSG:3857",
        url="git+https://github.com/OpenDroneMap/UAVArena.git#data/odm-3.0.0",
        filename="uavarena/odm-3.0.0",
        approx_bytes=101_000_000,
        upstream="OpenDroneMap UAVArena software-comparison corpus",
        accuracy_m=0.10,
        vintage="2020",
        role="PS requirements: 'Drone imagery', 'Orthorectified Imagery (ORI)'. A real "
             "UAV photogrammetric reconstruction published as an XYZ pyramid to zoom 21 "
             "(≈5.6 cm/px), rebuilt by the platform into a georeferenced COG.",
    ),
    "uav_ori_pix4d": DataSource(
        key="uav_ori_pix4d",
        title="UAV orthorectified imagery — Pix4D 4.4.10 reconstruction of the same flight",
        authority_code="ODM",
        authority_name="OpenDroneMap public survey corpus (UAVArena)",
        licence="CC-BY-4.0",
        source_type="orthorectified_imagery",
        feature_class="raster",
        crs="EPSG:3857",
        url="git+https://github.com/OpenDroneMap/UAVArena.git#data/pix4d-4.4.10",
        filename="uavarena/pix4d-4.4.10",
        approx_bytes=77_000_000,
        upstream="OpenDroneMap UAVArena software-comparison corpus",
        accuracy_m=0.10,
        vintage="2020",
        role="An independent photogrammetric reconstruction of the *same* flight. Two "
             "ORIs of identical ground from different engines is the cleanest possible "
             "test of raster co-registration and of change detection's false-positive "
             "rate: every difference between them is by construction a processing "
             "artefact rather than a real-world change.",
    ),
    "uav_dsm_odm": DataSource(
        key="uav_dsm_odm",
        title="UAV digital surface model — OpenDroneMap 3.0.0",
        authority_code="ODM",
        authority_name="OpenDroneMap public survey corpus (UAVArena)",
        licence="CC-BY-4.0",
        source_type="dsm",
        feature_class="raster",
        crs="EPSG:3857",
        url="git+https://github.com/OpenDroneMap/UAVArena.git#data/odm-3.0.0/*/dsm",
        filename="uavarena/odm-3.0.0/dsm",
        approx_bytes=50_000_000,
        upstream="OpenDroneMap UAVArena",
        accuracy_m=0.25,
        vintage="2020",
        role="PS requirement: 'DSM/DTM datasets'. Published as a colour-ramped pyramid; "
             "the platform recovers the surface by inverting the ramp and then derives "
             "the DTM itself with a progressive morphological ground filter, which is "
             "the operation the problem statement actually needs.",
    ),
}


@dataclass
class AreaOfInterest:
    """The demonstration area.

    Central Chennai was chosen because it is the one place where an independent municipal
    survey, two independent cadastral compilations and a machine-learning extraction all
    have real coverage — which is the only honest way to demonstrate multi-source
    harmonisation.
    """

    name: str = "Chennai Central"
    bbox: tuple[float, float, float, float] = (80.20, 13.03, 80.28, 13.11)
    crs: str = "EPSG:4326"
    metric_crs: str = "EPSG:32644"
    state_lgd: str = "33"
    district_lgd: str = "571"
    ulb: str = "GCC"
    description: str = (
        "≈8.7 km x 8.9 km of central Chennai spanning Egmore, Nungambakkam, Kilpauk, "
        "Purasawalkam, Chetpet, Aminjikarai and Anna Nagar East."
    )

    @property
    def width_km(self) -> float:
        import math
        return (self.bbox[2] - self.bbox[0]) * 111.320 * math.cos(math.radians(
            (self.bbox[1] + self.bbox[3]) / 2))

    @property
    def height_km(self) -> float:
        return (self.bbox[3] - self.bbox[1]) * 110.574

    @property
    def area_km2(self) -> float:
        return self.width_km * self.height_km


DEFAULT_AOI = AreaOfInterest()

#: A second, smaller AOI used by the test suite so tests stay fast.
TEST_AOI = AreaOfInterest(
    name="Chennai Chetpet test tile",
    bbox=(80.235, 13.070, 80.250, 13.085),
    description="A 1.6 km x 1.7 km tile used for unit and integration tests.",
)


def by_role(keyword: str) -> list[DataSource]:
    k = keyword.lower()
    return [d for d in CATALOGUE.values() if k in d.role.lower() or k in d.source_type]


def total_download_bytes() -> int:
    return sum(d.approx_bytes for d in CATALOGUE.values())
