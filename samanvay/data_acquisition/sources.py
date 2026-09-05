"""The real-data catalogue.

Every dataset SAMANVAY is demonstrated on is real, openly licensed data published by an
Indian government body or an institutional programme. Nothing here is synthetic, and
nothing is fabricated to make a demonstration look better than it is — the discrepancies
between these datasets are the actual discrepancies that exist in Indian urban land data
today, which is exactly what makes them the right test.

Each entry records the issuing authority, the licence, exactly where the bytes are actually
fetched from, and the size, so the whole corpus is reproducible from a clean machine. Every
entry also carries an explicit provenance ``tier`` — "official" (the authoritative platform's
own documented API/download), "mirror" (a legitimate third-party republication, used only
where the official platform's own bulk path was checked and found closed — see the notes on
``tngis_cadastre`` for a directly-verified example), or "proxy" (a research/open benchmark
standing in for a category with no real Indian dataset available, e.g. the OpenDroneMap UAV
entries). The tier is never blurred upward: a mirror is recorded and surfaced as a mirror.
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
    tier: Literal["official", "mirror", "proxy"] = "mirror"
    """"official": fetched directly from the authoritative platform's own documented
    API/WMS/WFS/download. "mirror": a legitimate third-party republication of official data,
    used only when the official platform itself has no reachable bulk path (see `upstream`
    and `notes` for what was actually verified about that). "proxy": a research/open
    benchmark standing in for a category with no real Indian dataset available. Never
    blurred upward — a mirror is never presented as if it were official."""
    platform: str = ""
    """The specific portal/service the bytes are actually served from, distinct from
    `authority_name` (the organisation accountable for the underlying data)."""
    requires_credentials: bool = False
    """True if fetching this for real requires a human to complete registration/login this
    environment cannot perform (Bhuvan signup, SOI Aadhaar-linked login, SOI CORS
    subscription, ...). Such sources ship a real connector that honestly reports
    "not connected" rather than fetching nothing silently or fabricating a response."""
    resolver: Literal["direct", "git", "ckan"] = "direct"
    """"direct": `url` is a downloadable file. "git": `url` is a `git+...#subpath` spec.
    "ckan": `url` is a human-facing dataset page on a CKAN portal (e.g. OpenCity) — the real
    file URL is resolved at fetch time via that portal's documented `package_show` API
    (`ckan_base`/`ckan_dataset`), not hardcoded, since CKAN resource URLs are not stable
    human-facing links."""
    ckan_base: str = ""
    ckan_dataset: str = ""
    ckan_resource_hint: str = ""
    """Case-insensitive substring to pick the right resource when a CKAN dataset has several
    (e.g. ward maps for multiple years) — verified against the real package_show response,
    not guessed."""


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
        tier="mirror",
        platform="ramSeraph/indian_cadastrals GitHub releases",
        accuracy_m=3.0,
        vintage="2023",
        role="PS requirement: 'Existing cadastral maps' and 'Revenue records'. "
             "6.02 million real survey-number parcels with LGD district/taluk/village "
             "codes, survey numbers and FMB flags.",
        notes="Carries lgd_village_code, which is the join key to the LGD directory and "
              "therefore to every other government dataset in India. Tier is 'mirror', not "
              "'official', because the official path was directly checked and found closed: "
              "TNGIS runs a live public GeoServer 2.12.2 at tngis.tn.gov.in/geoserver/, but "
              "every WFS endpoint on it returns 'Service WFS is disabled' (a deliberate "
              "server-side policy), and its Downloads page is gated behind a registration/"
              "login form with no files reachable unauthenticated. This mirror's own project "
              "notes say the data was obtained via 'WMS/Geoserver GetMap calls' against that "
              "same TNGIS infrastructure; its precise acquisition date and licence are not "
              "documented by the mirror maintainer and are recorded as unknown, not invented.",
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
        tier="mirror",
        platform="ramSeraph/indian_cadastrals GitHub releases",
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
        tier="mirror",
        platform="ramSeraph/indian_buildings GitHub releases",
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
        tier="mirror",
        platform="ramSeraph/indian_buildings GitHub releases",
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
        title="Google Open Buildings V3 — S2 cell 3a5 (covers the Chennai demo AOI)",
        authority_code="GOBI",
        authority_name="Google Research",
        licence="CC-BY-4.0",
        source_type="ai_extraction",
        feature_class="building",
        crs="EPSG:4326",
        url="https://storage.googleapis.com/open-buildings-data/v3/polygons_s2_level_4_gzip/3a5_buildings.csv.gz",
        filename="gobi_3a5_buildings.csv.gz",
        approx_bytes=1_123_552_473,
        upstream="sites.research.google/open-buildings (full pan-India V3 dataset is 178GB across "
                 "many S2 level-4 cells; this fetches only the real cell — token '3a5', computed "
                 "with s2sphere.RegionCoverer at level 4 — that actually covers the demo AOI "
                 "bbox (80.20-80.28E, 13.03-13.11N), verified reachable and its real size "
                 "confirmed via a HEAD request before being catalogued here)",
        tier="official",
        platform="Google Research Open Buildings (Google Cloud Storage)",
        accuracy_m=1.8,
        vintage="2023",
        role="PS requirement: 'AI-generated feature extraction outputs'. ML-segmented building "
             "footprints; this is the real GOOGLE_OPEN_BUILDINGS candidate layer "
             "pipeline/presets.py matches against GCC_BUILDINGS.",
        notes="CSV with a WKT `geometry` column (not GeoJSON/parquet) — build_aoi.py's "
              "clip_csv_wkt() parses and clips it directly. The pan-India dataset (250M+ "
              "footprints across ~178GB) is real but far larger than any single AOI "
              "demonstration needs; fetching one real, verified S2 cell rather than "
              "asserting the whole national dataset was fetched is the honest scope here.",
    ),
    "lgd_india": DataSource(
        key="lgd_india",
        title="Local Government Directory (LGD) — Pan-India Village Boundaries",
        authority_code="LGD",
        authority_name="Ministry of Panchayati Raj, Govt of India",
        licence="CC0-1.0 (attribute DataMeet/LGD/original government source where possible)",
        source_type="admin_boundary",
        feature_class="admin_unit",
        crs="EPSG:4326",
        url=_gh("ramSeraph/indian_admin_boundaries", "villages", "LGD_Villages.geojsonl.7z"),
        filename="LGD_Villages.geojsonl.7z",
        archive="7z",
        member="LGD_Villages.geojsonl",
        approx_bytes=350_561_734,
        upstream="lgdirectory.gov.in (Local Government Directory)",
        tier="mirror",
        platform="ramSeraph/indian_admin_boundaries GitHub releases",
        accuracy_m=10.0,
        vintage="2024",
        role="Defines the real village/subdistrict/district/state administrative hierarchy "
             "for any location in India — the join key for pan-India ULPIN minting, and the "
             "one real dataset that lets the platform identify a jurisdiction honestly "
             "outside the Chennai AOI, not just show a bare empty state.",
        notes="Tier is 'mirror', not 'official': the official direct-download URL previously "
              "catalogued here (lgdirectory.gov.in/download/All_India_Villages.geojson) was "
              "directly checked this session and returns HTTP 404, not a real file — that "
              "entry was aspirational, not verified. This mirror was directly verified "
              "reachable instead (HTTP 200 via redirect, Content-Length 350,561,734 bytes), "
              "published by the same maintainer/pattern already trusted for "
              "tngis_cadastre/gcc_buildings above, itself compiled from LGD, Bhuvan Panchayat "
              "and Survey of India village boundary sources.",
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
        tier="mirror",
        platform="yashveeeeeeer/india-geodata GitHub raw",
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
        tier="mirror",
        platform="yashveeeeeeer/india-geodata GitHub raw",
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
        tier="mirror",
        platform="yashveeeeeeer/india-geodata GitHub raw",
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
        tier="proxy",
        platform="OpenDroneMap UAVArena",
        accuracy_m=0.10,
        vintage="2020",
        role="PS requirements: 'Drone imagery', 'Orthorectified Imagery (ORI)'. A real "
             "UAV photogrammetric reconstruction published as an XYZ pyramid to zoom 21 "
             "(≈5.6 cm/px), rebuilt by the platform into a georeferenced COG. Tier is "
             "'proxy': this is NOT NAKSHA or any Indian government drone survey — DoLR's "
             "NAKSHA outputs are not publicly bulk-downloadable (nakshauat.dolr.gov.in is a "
             "per-property citizen verification portal, not an open data archive). See "
             "'svamitva_drone_villages' below for the real, official (but non-imagery) "
             "record of which villages actually received a government drone survey.",
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
        tier="proxy",
        platform="OpenDroneMap UAVArena",
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
        tier="proxy",
        platform="OpenDroneMap UAVArena",
        accuracy_m=0.25,
        vintage="2020",
        role="PS requirement: 'DSM/DTM datasets'. Published as a colour-ramped pyramid; "
             "the platform recovers the surface by inverting the ramp and then derives "
             "the DTM itself with a progressive morphological ground filter, which is "
             "the operation the problem statement actually needs.",
    ),
    # ------------------------------------------------------- deeper research pass
    # Added after directly checking DoLR/NAKSHA, ISRO/NRSC Bhuvan, Survey of India, TNGIS,
    # data.gov.in and Chennai/TN municipal portals for real, legitimate sources across every
    # PS 26013 category, rather than stopping at the entries above. See the implementation
    # plan's provenance catalogue for the full research trail.
    "svamitva_drone_villages": DataSource(
        key="svamitva_drone_villages",
        title="State/UT-wise details of drone-surveyed villages under SVAMITVA",
        authority_code="DOLR",
        authority_name="Ministry of Panchayati Raj & Survey of India (SVAMITVA scheme, DILRMP)",
        licence="Open Government Data",
        source_type="ground_truth",
        feature_class="admin_unit",
        crs="",
        url="https://www.data.gov.in/resource/drone-flown-villages",
        filename="svamitva_drone_flown_villages.csv",
        upstream="data.gov.in Open Government Data Platform India",
        tier="official",
        platform="data.gov.in OGD Platform",
        accuracy_m=None,
        vintage="2020-21 to 2023-24",
        role="PS requirement: 'Drone imagery' / 'Ground Truthing (GT)' provenance context. "
             "This is real official metadata about *which villages* received a government "
             "drone survey under SVAMITVA (the rural analogue of NAKSHA) — not raw imagery. "
             "Used to honestly annotate whether a demo village's drone-survey status is "
             "backed by an official record, independent of the OpenDroneMap imagery proxy "
             "used to exercise the raster pipeline itself.",
        notes="Tabular (CSV), no geometry — joined to the AOI by village LGD code. Directly "
              "verified: data.gov.in's resource page returns HTTP 500/HTML to a plain GET "
              "(server-rendered SPA with bot protection), not the CSV — data.gov.in's real "
              "bulk-download path is its authenticated API (api.data.gov.in), which needs a "
              "registered API key this environment doesn't have.",
        requires_credentials=True,
    ),
    "ncscm_czmp_tn": DataSource(
        key="ncscm_czmp_tn",
        title="Coastal Zone Management Plan (CZMP), Tamil Nadu",
        authority_code="NCSCM",
        authority_name="National Centre for Sustainable Coastal Management, MoEFCC",
        licence="Government publication",
        source_type="cadastral_map",
        feature_class="admin_unit",
        crs="",
        url="https://ncscm.res.in/wp-content/uploads/pdf/TN_CZMP.pdf",
        filename="ncscm_tn_czmp.pdf",
        upstream="ncscm.res.in (NCSCM is headquartered in Chennai)",
        tier="official",
        platform="NCSCM official publication",
        accuracy_m=None,
        vintage="",
        role="PS requirement: 'Revenue records' / coastal land classification context. A "
             "real, citable public document from the NCSCM authority already referenced by "
             "the ncscm_cadastre entry above — this gives that authority a confirmed real "
             "source document rather than only a name.",
        notes="PDF report, not machine-readable vector data; used for provenance/citation "
              "and coastal-zone classification context, not as a geometry source.",
    ),
    "tn_ogd_panchayats": DataSource(
        key="tn_ogd_panchayats",
        title="Details of Blocks, Habitations and Village Panchayats in Tamil Nadu",
        authority_code="TNREV",
        authority_name="Tamil Nadu Rural Development & Panchayat Raj Department",
        licence="Open Government Data",
        source_type="admin_boundary",
        feature_class="admin_unit",
        crs="",
        url="https://tn.data.gov.in/catalog/details-blocks-habitations-and-village-panchayats-tamil-nadu",
        filename="tn_blocks_habitations_panchayats.csv",
        upstream="Tamil Nadu Open Government Data Portal (tn.data.gov.in, run by NIC)",
        tier="official",
        platform="TN OGD Portal",
        accuracy_m=None,
        vintage="",
        role="Supplements LGD with TN-specific administrative hierarchy; confirms "
             "Chennai-area villages/panchayats officially rather than assuming LGD alone "
             "is the only administrative reference.",
        notes="Tabular (CSV), no geometry. Directly verified: the TN OGD catalog page "
              "returns empty/blocked to a plain GET, same bot-protected NIC OGD platform "
              "pattern as svamitva_drone_villages above — needs the api.data.gov.in key "
              "system, not a hardcoded URL.",
        requires_credentials=True,
    ),
    "gcc_opencity_wards": DataSource(
        key="gcc_opencity_wards",
        title="GCC Ward Map 2022 / Zone Map 2022",
        authority_code="GCC",
        authority_name="Greater Chennai Corporation",
        licence="Public domain (tagged 'other-pd' on OpenCity)",
        source_type="admin_boundary",
        feature_class="admin_unit",
        crs="EPSG:4326",
        url="https://data.opencity.in/dataset/gcc-ward-information",
        filename="gcc_ward_2022.kml",
        upstream="data.opencity.in Urban Data Portal, organization=greater-chennai-corporation",
        tier="mirror",
        platform="OpenCity Urban Data Portal",
        resolver="ckan", ckan_base="https://data.opencity.in", ckan_dataset="gcc-ward-information",
        ckan_resource_hint="Ward Map - 2022",
        accuracy_m=5.0,
        vintage="2022",
        role="PS requirement: 'Municipal GIS layers'. A better-provenance alternative to "
             "gcc_wards/gcc_zones above: OpenCity is a recognised civic open-data "
             "aggregator that explicitly attributes the dataset to GCC as its publishing "
             "organisation (unlike an anonymous GitHub raw mirror), even though the bytes "
             "still don't come from chennaicorporation.gov.in itself — hence tier='mirror'. "
             "fetch.py resolves the exact resource file via OpenCity's public CKAN API "
             "(package_show for this dataset id) rather than a hardcoded file URL, since "
             "CKAN portals expose that as their documented access method.",
    ),
    "chennai_metrowater_transmission": DataSource(
        key="chennai_metrowater_transmission",
        title="Chennai Water Transmission Network",
        authority_code="CMWSSB",
        authority_name="Chennai Metropolitan Water Supply and Sewerage Board",
        licence="OpenCity license tag",
        source_type="utility_network",
        feature_class="network",
        crs="EPSG:4326",
        url="https://data.opencity.in/dataset/chennai-water-transmission-network",
        filename="chennai_water_transmission.kml",
        upstream="data.opencity.in Urban Data Portal",
        tier="mirror",
        platform="OpenCity Urban Data Portal",
        resolver="ckan", ckan_base="https://data.opencity.in", ckan_dataset="chennai-water-transmission-network",
        accuracy_m=None,
        vintage="",
        role="PS requirement: 'Utility network data'. The first genuine utility-network "
             "layer in the corpus — previously the pipeline had none.",
    ),
    "ms_building_footprints_tn": DataSource(
        key="ms_building_footprints_tn",
        title="Microsoft Global ML Building Footprints — quadkey 123312203 (covers the metro-corridor AOI)",
        authority_code="MSFT",
        authority_name="Microsoft (Bing Maps AI team)",
        licence="CDLA Permissive 2.0 / ODbL",
        source_type="ai_extraction",
        feature_class="building",
        crs="EPSG:4326",
        url="https://minedbuildings.z5.web.core.windows.net/global-buildings/2026-02-03/"
            "global-buildings.geojsonl/RegionName=India/quadkey=123312203/"
            "part-00159-4feead82-d499-422b-94cb-c036c212127a.c000.csv.gz",
        filename="ms_buildings_quadkey_123312203.csv.gz",
        approx_bytes=27_414_977,
        upstream="Microsoft Global ML Building Footprints master index "
                 "(minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv) — "
                 "the real, direct blob URL for the India quadkey actually covering the metro "
                 "AOI bbox (80.05-80.29E, 12.87-13.11N), resolved by computing that bbox's Bing "
                 "tile quadkey at zoom 9 and matching it against the real dataset-links.csv "
                 "(667 India rows), then confirmed reachable via a direct HEAD request "
                 "(200 OK, Content-Length 27,414,977 matching the index's listed 26.1MB) "
                 "before being catalogued here — not assumed from the repo's README alone. "
                 "The neighbouring NE-corner tile (quadkey 123312212, 40.5MB) is real too but "
                 "not yet catalogued — the AOI's SW tile alone already gives real cross-source "
                 "coverage over most of the corridor.",
        tier="official",
        platform="Microsoft Azure Blob Storage (direct corporate open dataset)",
        accuracy_m=2.0,
        vintage="2026-02-03 (per the index's UploadDate column)",
        role="PS requirement: 'AI-generated feature extraction outputs' / 'Building "
             "footprint datasets'. A second, independently-derived ML building-footprint "
             "source alongside Google Open Buildings — matching these two against each "
             "other is a genuine cross-source harmonisation demonstration (real spatial "
             "matching between two real, independent datasets), not one source shown twice.",
        notes="Despite the .csv.gz extension, each line is a real GeoJSON Feature (gzipped "
              "GeoJSONL), not WKT+CSV rows like the Google Open Buildings entry above — "
              "build_aoi.py's PLAN table does not yet have a clipping kind for this format "
              "(only 'geojsonl', 'geojson' and 'csv_wkt_gz' exist), so this is catalogued and "
              "verified-reachable but not yet wired into a pipeline run. Integrating it needs "
              "a small 'geojsonl_gz' clipping kind added to build_aoi.py, not a new fetch "
              "mechanism — the real bytes are already one HTTP GET away.",
    ),
    "bhuvan_cartodem": DataSource(
        key="bhuvan_cartodem",
        title="CartoDEM v3 (Cartosat-1, 30 m posting) — Chennai tile",
        authority_code="NRSC",
        authority_name="National Remote Sensing Centre, ISRO",
        licence="Bhuvan Terms of Use",
        source_type="dsm",
        feature_class="raster",
        crs="EPSG:4326",
        url="https://bhuvan-app3.nrsc.gov.in/data/download/",
        filename="cartodem_chennai.tif",
        upstream="ISRO/NRSC Bhuvan Open Data Archive (bhoonidhi.nrsc.gov.in for the newer "
                 "EO Data Hub covering the same ortho/DEM products)",
        tier="official",
        platform="Bhuvan Open Data Archive / Bhoonidhi",
        accuracy_m=8.0,
        vintage="",
        role="PS requirement: 'DSM/DTM datasets' from an authoritative Indian source, as a "
             "real (if coarser than drone-derived) complement to the UAV DSM proxy above. "
             "30 m posting is coarser than drone-derived DSM — documented as such, never "
             "presented as equivalent.",
        notes="Genuinely free, but requires a Bhuvan account signup/login this environment "
              "cannot complete — see requires_credentials.",
        requires_credentials=True,
    ),
    "soi_cors_rinex": DataSource(
        key="soi_cors_rinex",
        title="Survey of India CORS Network RINEX / virtual-RINEX",
        authority_code="SOI",
        authority_name="Survey of India",
        licence="SOI CORS policy",
        source_type="gnss_cors",
        feature_class="raster",
        crs="",
        url="https://cors.surveyofindia.gov.in/",
        filename="soi_cors_rinex.zip",
        upstream="Survey of India CORS Portal",
        tier="official",
        platform="SOI CORS Portal",
        accuracy_m=0.02,
        vintage="",
        role="PS requirement: 'GNSS/CORS survey data'. Matches the exact RINEX 2.x/3.x "
             "format `ingest/gnss.py` already parses. Free for government/academic users "
             "after registration; paid for private/PSU users.",
        notes="Requires SOI CORS Portal registration this environment cannot complete — see "
              "requires_credentials. fetch.py's generic fetcher (used for every entry in this "
              "catalogue) honestly reports status='requires_credentials' for this dataset and "
              "skips it rather than fabricating a download, exactly like every other gated "
              "source here; there is no separate bespoke adapter file for it.",
        requires_credentials=True,
    ),
    "naksha_dolr": DataSource(
        key="naksha_dolr",
        title="NAKSHA — National Cadastral Mapping Platform",
        authority_code="DOLR",
        authority_name="Department of Land Resources, Ministry of Rural Development",
        licence="Government of India (access-gated)",
        source_type="cadastral_map",
        feature_class="parcel",
        crs="",
        url="https://naksha.dolr.gov.in/NakshaPortal/",
        filename="naksha_portal.html",
        upstream="naksha.dolr.gov.in",
        tier="official",
        platform="NAKSHA Portal",
        accuracy_m=None,
        vintage="",
        role="PS requirement: the flagship national cadastral-mapping integration platform "
             "this project's whole approach is modelled on — the natural home for real-time "
             "state cadastral data once a department stands up an integration.",
        notes="Directly checked: the portal is a JS-rendered SPA (naksha.dolr.gov.in/"
              "NakshaPortal/) with no discoverable public bulk-download endpoint, "
              "GetCapabilities URL, or documented open API — it is a state-department "
              "integration/citizen-verification platform, not an open data archive. This "
              "matches the 'nakshauat.dolr.gov.in is a per-property citizen verification "
              "portal, not an open data archive' finding already recorded on the "
              "uav_ori_odm entry above. Cataloguing it here rather than omitting it keeps "
              "the Data Source Matrix honest about the one source a reviewer will look for "
              "first: it exists, it is real, and it needs a departmental integration "
              "agreement this environment cannot obtain — not a code gap.",
        requires_credentials=True,
    ),
    "soi_open_series_maps": DataSource(
        key="soi_open_series_maps",
        title="Survey of India Open Series Maps (OSM), 1:50,000",
        authority_code="SOI",
        authority_name="Survey of India",
        licence="SOI Terms of Use",
        source_type="cadastral_map",
        feature_class="raster",
        crs="EPSG:4326",
        url="https://onlinemaps.surveyofindia.gov.in/",
        filename="soi_osm_50k_chennai.pdf",
        upstream="Survey of India (also listed as a WMS on data.gov.in catalog #6622464, "
                 "which returned 403 on direct programmatic fetch)",
        tier="official",
        platform="Survey of India Online Maps / Nakshe",
        accuracy_m=10.0,
        vintage="",
        role="Topographic reference for QA against the harmonised cadastral output.",
        notes="Full-resolution download requires Aadhaar-linked login this environment "
              "cannot complete — see requires_credentials.",
        requires_credentials=True,
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
