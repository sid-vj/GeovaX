"""Tests for identity, ledger, CRS and geodesy.

These are the parts of the platform where a silent error is unrecoverable: a duplicated
ULPIN orphans a mutation history, a broken ledger destroys auditability, and a wrong datum
moves a village four hundred metres. They are therefore tested against properties rather
than against golden values.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from samanvay.core.ids import (AdminContext, UlpinMinter, format_ulpin, geohash_encode,
                               mint_ulpin, validate_ulpin)
from samanvay.core.ledger import ProvenanceLedger
from samanvay.crs.engine import (AREA_UNITS_M2, CrsEngine, convert_area, format_extent,
                                 geodesic_area_m2, haversine_m, vincenty_m)

CHENNAI = (80.2425, 13.0777)


# --------------------------------------------------------------------------------------
# ULPIN
# --------------------------------------------------------------------------------------


def admin(ward: str = "089") -> AdminContext:
    return AdminContext("33", "571", "GCC", ward=ward, village_or_zone="07")


def test_ulpin_shape_and_checksum():
    u = mint_ulpin(admin(), *CHENNAI)
    assert len(u) == 14
    assert validate_ulpin(u)
    assert format_ulpin(u).count("-") == 4


def test_ulpin_checksum_catches_single_character_error():
    """A ULPIN is copied by hand off a paper patta. Single-character errors must be caught."""
    u = mint_ulpin(admin(), *CHENNAI)
    caught = 0
    trials = 0
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    for i in range(13):
        for c in alphabet:
            if c == u[i]:
                continue
            trials += 1
            if not validate_ulpin(u[:i] + c + u[i + 1:]):
                caught += 1
    assert caught == trials, f"{trials - caught} single-character errors slipped through"


def test_ulpin_is_stable_under_resurvey_shift():
    """A re-survey moves a centroid by centimetres. The identifier must not change."""
    base = mint_ulpin(admin(), *CHENNAI)
    for dlon, dlat in [(1e-6, 0), (0, 1e-6), (-1e-6, 1e-6)]:   # ~0.1 m
        assert mint_ulpin(admin(), CHENNAI[0] + dlon, CHENNAI[1] + dlat) == base


def test_ulpin_differs_across_administrative_units():
    a = mint_ulpin(admin("089"), *CHENNAI)
    b = mint_ulpin(admin("090"), *CHENNAI)
    assert a != b


def test_minter_guarantees_uniqueness_in_dense_fabric():
    """Two parcels can share a snapped cell. The minter must still issue distinct ULPINs."""
    m = UlpinMinter(snap_precision=8)   # deliberately coarse to force collisions
    issued = set()
    for i in range(400):
        lon = CHENNAI[0] + (i % 20) * 2e-5
        lat = CHENNAI[1] + (i // 20) * 2e-5
        u = m.mint(admin(), lon, lat, key=f"parcel-{i}")
        assert validate_ulpin(u)
        issued.add(u)
    assert len(issued) == 400, "the minter issued a duplicate ULPIN"
    assert m.collisions_resolved > 0, "the coarse grid should have forced collisions"


def test_minter_is_idempotent_for_the_same_parcel():
    m = UlpinMinter()
    a = m.mint(admin(), *CHENNAI, key="P1")
    b = m.mint(admin(), CHENNAI[0] + 5e-7, CHENNAI[1], key="P1")
    assert a == b


def test_geohash_precision_matches_expectation():
    a = geohash_encode(*CHENNAI, 9)
    b = geohash_encode(CHENNAI[0] + 2e-5, CHENNAI[1], 9)   # ~2.2 m east
    assert a[:7] == b[:7]


# --------------------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------------------


def test_ledger_chain_verifies():
    led = ProvenanceLedger()
    for i in range(50):
        led.append(f"E{i % 7}", "ingest", {"i": i})
    ok, broken, msg = led.verify()
    assert ok and broken is None, msg


def test_ledger_detects_tampering_and_names_the_index():
    led = ProvenanceLedger()
    for i in range(20):
        led.append("E1", "resolve", {"value": i})
    entries = list(led)
    tampered = entries[7].__class__(**{**entries[7].__dict__, "payload": {"value": 999}})
    led._entries[7] = tampered           # noqa: SLF001 - deliberately reaching in
    ok, broken, msg = led.verify()
    assert not ok
    assert broken == 7, msg


def test_merkle_inclusion_proof_round_trips():
    led = ProvenanceLedger()
    for i in range(37):
        led.append("E", "op", {"i": i})
    root = led.merkle_root()
    for idx in (0, 1, 18, 36):
        proof = led.inclusion_proof(idx)
        leaf = list(led)[idx].entry_hash
        assert ProvenanceLedger.verify_inclusion(leaf, proof, root), f"proof failed at {idx}"


def test_merkle_proof_rejects_a_wrong_leaf():
    led = ProvenanceLedger()
    for i in range(16):
        led.append("E", "op", {"i": i})
    proof = led.inclusion_proof(3)
    assert not ProvenanceLedger.verify_inclusion("0" * 64, proof, led.merkle_root())


def test_ledger_persists_and_reloads(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    led = ProvenanceLedger(path)
    for i in range(12):
        led.append("E", "op", {"i": i})
    head = led.head
    again = ProvenanceLedger(path)
    assert len(again) == 12
    assert again.head == head
    assert again.verify()[0]


# --------------------------------------------------------------------------------------
# CRS and geodesy
# --------------------------------------------------------------------------------------


def test_metric_crs_for_chennai_is_utm_44n():
    assert CrsEngine.metric_crs_for(*CHENNAI) == "EPSG:32644"


def test_round_trip_through_utm_is_sub_millimetre():
    e = CrsEngine()
    assert e.round_trip_error(CHENNAI[0], CHENNAI[1], "EPSG:4326", "EPSG:32644") < 1e-3


def test_legacy_indian_datum_shift_is_hundreds_of_metres():
    """The whole point of the CRS engine: reading Kalianpur as WGS 84 is a huge error."""
    e = CrsEngine()
    x, y = e.transform_point(CHENNAI[0], CHENNAI[1], "EPSG:4326", "EPSG:4240")
    d = haversine_m(CHENNAI[0], CHENNAI[1], x, y)
    assert 50 < d < 500, f"expected a datum shift of hundreds of metres, got {d:.1f} m"


def test_web_mercator_area_error_is_material_at_chennai():
    """Justifies the rule that measurement never happens in EPSG:3857."""
    e = CrsEngine()
    ring = [(80.24, 13.07), (80.245, 13.07), (80.245, 13.075), (80.24, 13.075)]
    true_area = geodesic_area_m2(ring)
    from shapely.geometry import Polygon
    merc = e.transform_geometry(Polygon(ring), "EPSG:4326", "EPSG:3857")
    ratio = merc.area / true_area
    assert 1.04 < ratio < 1.07, f"expected ~1.054x area inflation, got {ratio:.4f}"


def test_geodesic_area_matches_utm_within_a_tenth_of_a_percent():
    from shapely.geometry import Polygon
    e = CrsEngine()
    ring = [(80.24, 13.07), (80.2445, 13.0702), (80.2447, 13.0748), (80.2402, 13.0746)]
    geodesic = geodesic_area_m2(ring)
    utm = e.transform_geometry(Polygon(ring), "EPSG:4326", "EPSG:32644").area
    assert abs(geodesic - utm) / geodesic < 0.001


def test_vincenty_agrees_with_haversine_over_short_distances():
    d1 = vincenty_m(80.0, 13.0, 80.01, 13.0)
    d2 = haversine_m(80.0, 13.0, 80.01, 13.0)
    assert abs(d1 - d2) / d1 < 0.005


def test_indian_area_units():
    assert math.isclose(convert_area(AREA_UNITS_M2["acre"], "acre"), 1.0, rel_tol=1e-9)
    assert math.isclose(convert_area(AREA_UNITS_M2["acre"], "cent"), 100.0, rel_tol=1e-9)
    assert math.isclose(convert_area(AREA_UNITS_M2["ground"], "sqft"), 2400.0, rel_tol=1e-6)


def test_extent_formats_in_acre_cent():
    s = format_extent(AREA_UNITS_M2["acre"] * 1.25)
    assert s.startswith("1.25 acre")


def test_unknown_area_unit_raises():
    with pytest.raises(KeyError):
        convert_area(100.0, "furlong")
