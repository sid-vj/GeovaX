"""Integration tests against the real clipped government corpus.

These are marked ``real`` and skip cleanly when the corpus has not been fetched, so the
unit suite still runs on a clean checkout. They assert the properties that must hold on
*real* data and that no constructed fixture can establish — that the streaming reader
survives a gigabyte file, that the schemas are what the catalogue says, and that the
matcher finds a systematic offset between two layers that genuinely have one.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from samanvay.core.models import FeatureClass, SourceDataset, SourceType
from samanvay.core.registry import AUTHORITIES
from samanvay.ingest.vector import GeoJsonLinesConnector
from samanvay.matching.features import BlockingConfig, MatchableFeature
from samanvay.matching.pipeline import MatchingPipeline
from samanvay.attributes.schema_match import SchemaMatcher

AOI_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "aoi")
TILE = (80.235, 13.070, 80.250, 13.085)

pytestmark = pytest.mark.real


def _have(name: str) -> bool:
    return os.path.exists(os.path.join(AOI_DIR, name))


needs_corpus = pytest.mark.skipif(
    not _have("buildings_gcc.geojsonl"),
    reason="real corpus not fetched — run `make data`",
)


def _dataset(ds_id: str, st: SourceType, acc: float) -> SourceDataset:
    return SourceDataset(ds_id, ds_id, st, AUTHORITIES["GCC"], "CC0-1.0", "EPSG:4326",
                         positional_accuracy_m=acc)


def _load(name: str, ds_id: str, limit: int | None = None):
    from pyproj import Transformer
    from shapely.ops import transform as shp_transform

    tr = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
    conn = GeoJsonLinesConnector(_dataset(ds_id, SourceType.MUNICIPAL_GIS, 1.0),
                                 FeatureClass.BUILDING)
    out = []
    for rf in conn.read(os.path.join(AOI_DIR, name), bbox=TILE, limit=limit):
        out.append(MatchableFeature(
            fid=rf.source_feature_id, dataset_id=ds_id,
            geometry=shp_transform(lambda x, y, z=None: tr.transform(x, y), rf.geometry),
            attributes=rf.properties))
    return out


# --------------------------------------------------------------------------------------


@needs_corpus
def test_manifest_records_provenance_for_every_dataset():
    with open(os.path.join(AOI_DIR, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    assert man["datasets"], "the manifest must list what was clipped"
    for d in man["datasets"]:
        assert d["authority"], f"{d['key']} has no issuing authority recorded"
        assert d["licence"], f"{d['key']} has no licence recorded"
        assert len(d["sha256_aoi_copy"]) == 64, f"{d['key']} has no checksum"
        assert d["kept"] <= d["scanned"]


@needs_corpus
def test_streaming_reader_handles_the_real_cadastral_file_in_bounded_memory():
    """The TN cadastre is 1.07 GB. Reading a tile of it must not load the file."""
    import resource

    path = os.path.join(AOI_DIR, "cadastre_tngis.geojsonl")
    if not os.path.exists(path):
        pytest.skip("cadastral extract not present")
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    conn = GeoJsonLinesConnector(
        _dataset("TNGIS", SourceType.CADASTRAL_MAP, 3.0), FeatureClass.PARCEL)
    n = sum(1 for _ in conn.read(path, bbox=TILE))
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert n > 100, f"expected real parcels in the tile, got {n}"
    # ru_maxrss is KB on Linux; the whole tile must cost well under a gigabyte
    assert (after - before) < 900_000, f"streaming read grew RSS by {(after - before) / 1000:.0f} MB"


@needs_corpus
def test_real_gcc_schema_maps_without_configuration():
    """The municipal survey's own column names must resolve to the canonical schema."""
    conn = GeoJsonLinesConnector(
        _dataset("GCC", SourceType.MUNICIPAL_GIS, 1.0), FeatureClass.BUILDING)
    records = [rf.properties for rf in
               conn.read(os.path.join(AOI_DIR, "buildings_gcc.geojsonl"),
                         bbox=TILE, limit=3000)]
    assert records, "no real GCC records in the test tile"
    matcher = SchemaMatcher()
    cw = matcher.crosswalk(matcher.match(records))
    assert cw.get("ward_number") == "ward"
    assert cw.get("road_name") == "street"
    assert "gcc_gis_id" in cw, "the GCC identifier should map somewhere in the schema"


@needs_corpus
def test_real_extraction_carries_model_confidence_and_height():
    """Google Open Buildings is the PS's 'AI-generated feature extraction output'."""
    conn = GeoJsonLinesConnector(
        _dataset("GOB", SourceType.AI_EXTRACTION, 1.8), FeatureClass.BUILDING)
    feats = list(conn.read(os.path.join(AOI_DIR, "buildings_gob.geojsonl"),
                           bbox=TILE, limit=500))
    assert feats
    with_conf = [f for f in feats if f.properties.get("confidence") is not None]
    with_height = [f for f in feats if f.properties.get("height_m") is not None]
    assert len(with_conf) / len(feats) > 0.95
    assert len(with_height) / len(feats) > 0.95
    heights = [float(f.properties["height_m"]) for f in with_height]
    assert 1.0 < (sum(heights) / len(heights)) < 30.0, "implausible mean building height"


@needs_corpus
@pytest.mark.slow
def test_matcher_finds_the_real_systematic_offset_between_two_departments():
    """The GCC survey and the Google extraction really are offset. It must be found."""
    left = _load("buildings_gcc.geojsonl", "GCC")
    right = _load("buildings_gob.geojsonl", "GOB")
    assert len(left) > 1000 and len(right) > 1000

    pipe = MatchingPipeline(
        blocking=BlockingConfig(accuracy_multiplier=3.0, max_candidates_per_feature=8))
    res = pipe.run(left, right, acc_left_m=1.0, acc_right_m=1.8)

    assert res.offset.significant, "a known real offset was not detected"
    assert 0.5 < res.offset.magnitude_m < 4.0, (
        f"offset of {res.offset.magnitude_m:.2f} m is outside the plausible range for two "
        f"metre-accuracy urban layers")
    assert res.offset.n_samples > 100

    assert res.assignment is not None
    accepted = res.assignment.n_accepted
    assert accepted > 0.25 * min(len(left), len(right)), (
        f"only {accepted} matches from {len(left)}x{len(right)} — the matcher has "
        f"collapsed")
    # both split and merge relationships genuinely occur between a survey and an extraction
    assert res.assignment.cardinality_counts.get("1:N", 0) > 0
    assert res.assignment.cardinality_counts.get("N:1", 0) > 0

    if res.training and res.training.n_labelled:
        assert res.training.n_positive > 0 and res.training.n_negative > 0
        assert res.training.calibration_error < 0.1


@needs_corpus
def test_two_cadastral_compilations_disagree_as_expected():
    """TNGIS and NCSCM describe the same ground and are known to diverge."""
    from samanvay.ingest.vector import GeoJsonLinesConnector as C

    a = C(_dataset("TNGIS", SourceType.CADASTRAL_MAP, 3.0), FeatureClass.PARCEL)
    b = C(_dataset("NCSCM", SourceType.CADASTRAL_MAP, 5.0), FeatureClass.PARCEL)
    na = sum(1 for _ in a.read(os.path.join(AOI_DIR, "cadastre_tngis.geojsonl"), bbox=TILE))
    nb = sum(1 for _ in b.read(os.path.join(AOI_DIR, "cadastre_ncscm.geojsonl"), bbox=TILE))
    assert na > 0 and nb > 0
    # the compilations differ by roughly an order of magnitude in this tile; that gap is
    # the completeness finding the platform is meant to surface, not an error
    assert na != nb
