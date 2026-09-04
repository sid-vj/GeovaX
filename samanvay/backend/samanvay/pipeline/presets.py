"""Shared AOI and layer-catalogue presets for the Chennai demonstration corpus.

Factored out of ``scripts/run_pipeline.py`` so the CLI entry point and the queue-driven
``pipeline/worker.py`` build the exact same layer specs from one place rather than two
copies drifting apart.
"""

from __future__ import annotations

import os

from ..core.models import FeatureClass, SourceType
from .harmonise import LayerSpec

AOIS: dict[str, tuple[str, tuple[float, float, float, float]]] = {
    "core": ("Chennai Central", (80.20, 13.03, 80.28, 13.11)),
    "test": ("Chennai Chetpet tile", (80.235, 13.070, 80.250, 13.085)),
    "mid":  ("Chennai Nungambakkam-Kilpauk", (80.225, 13.045, 80.265, 13.095)),
    # Covers every real jurisdiction button in AVAILABLE_WARDS (web-gis/src/lib/auth.ts) from
    # Vandalur/Veeralakshmi Nagar in the south-west to Mylapore/Egmore in the north-east, so
    # the demo UI's whole Vandalur-to-Guindy-to-Central-Chennai corridor has real harmonised
    # data instead of "AOI outside dataset coverage" for the wards south of Guindy. Verified
    # before running: the same single Google Open Buildings S2 level-4 cell ("3a5") already
    # fetched for "core" covers this whole extent too (s2sphere.RegionCoverer, level 4), and
    # the already-downloaded state-wide TNGIS / city-wide GCC buildings files have real
    # feature density inside the new southern extent (sampled directly, not assumed) — NCSCM
    # coastal cadastre does not reach this far inland, which will show up honestly as
    # single-source (TNGIS-only) parcels there, not a bug.
    "metro": ("Chennai Metro Corridor (Vandalur–Egmore)", (80.05, 12.87, 80.29, 13.10)),
}


def default_layers(data_dir: str, max_features: int | None = None) -> list[LayerSpec]:
    """The four-layer Chennai demo catalogue: TN cadastre, NCSCM cadastre, GCC buildings,
    Google Open Buildings. See docs/02-data-sources.md and the plan's provenance catalogue
    for the real source/tier of each dataset."""
    return [
        LayerSpec(
            dataset_id="TNGIS_CADASTRE",
            path=os.path.join(data_dir, "cadastre_tngis.geojsonl"),
            source_type=SourceType.CADASTRAL_MAP,
            feature_class=FeatureClass.PARCEL,
            authority="TNGIS", licence="CC0-1.0", accuracy_m=3.0, vintage="2023",
            id_fields=("survey_number", "lgd_village_code"),
            role="reference", max_features=max_features,
            tier="mirror", platform="ramSeraph/indian_cadastrals GitHub releases",
            original_format="GeoJSONL", coverage="Tamil Nadu (state-wide)",
            transformation="7z-extracted, streamed and clipped to AOI bbox",
        ),
        LayerSpec(
            dataset_id="NCSCM_CADASTRE",
            path=os.path.join(data_dir, "cadastre_ncscm.geojsonl"),
            source_type=SourceType.CADASTRAL_MAP,
            feature_class=FeatureClass.PARCEL,
            authority="NCSCM", licence="CC0-1.0", accuracy_m=5.0, vintage="2019",
            id_fields=("Survey_Number", "Village"),
            role="candidate", max_features=max_features,
            tier="mirror", platform="ramSeraph/indian_cadastrals GitHub releases",
            original_format="GeoJSONL", coverage="Coastal Tamil Nadu",
            transformation="7z-extracted, streamed and clipped to AOI bbox",
        ),
        LayerSpec(
            dataset_id="GCC_BUILDINGS",
            path=os.path.join(data_dir, "buildings_gcc.geojsonl"),
            source_type=SourceType.MUNICIPAL_GIS,
            feature_class=FeatureClass.BUILDING,
            authority="GCC", licence="CC0-1.0", accuracy_m=1.0, vintage="2024",
            id_fields=("gcc_gis_id",),
            role="reference", max_features=max_features,
            tier="mirror", platform="ramSeraph/indian_buildings GitHub releases",
            original_format="GeoJSONL", coverage="Greater Chennai Corporation limits",
            transformation="7z-extracted, streamed and clipped to AOI bbox",
        ),
        LayerSpec(
            dataset_id="GOOGLE_OPEN_BUILDINGS",
            path=os.path.join(data_dir, "buildings_gob.geojsonl"),
            source_type=SourceType.AI_EXTRACTION,
            feature_class=FeatureClass.BUILDING,
            authority="GOBI", licence="CC-BY-4.0", accuracy_m=1.8, vintage="2023",
            id_fields=("gob_id",),
            role="candidate", max_features=max_features,
            tier="official", platform="Google Research Open Buildings",
            original_format="GeoParquet", coverage="Pan-India (clipped to AOI)",
            transformation="Reprojected to EPSG:4326, clipped to AOI bbox",
        ),
    ]
