"""Tests for topology, matching, fusion, confidence, change and terrain.

Each test states the property it is protecting. Where a test uses a constructed geometry
that is because the *property* is geometric — a sliver is a sliver regardless of which
department drew it — and constructing it makes the expected answer unambiguous. The
end-to-end behaviour on real government data is exercised separately by
``tests/test_integration_real_data.py``.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon, box

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from samanvay.attributes.schema_match import SchemaMatcher, profile_column
from samanvay.attributes.translit import canonical_place, detect_script, normalise_name, transliterate
from samanvay.change.vector_change import ChangeConfig, ChangeDetector
from samanvay.confidence.scorer import ConfidenceScorer
from samanvay.conflict.evidence import MassFunction, combine, combine_all, fuse
from samanvay.core.models import (Cardinality, ChangeType, Claim, Evidence, MatchPair,
                                  SourceDataset, SourceType)
from samanvay.core.registry import AUTHORITIES, SourceRegistry
from samanvay.crs.gcp import GroundControlPoint, GeoReferencer, choose_model
from samanvay.geoai.footprints import ExtractionQC, RegulariseConfig, rectilinearity, regularise
from samanvay.matching.assign import AssignmentConfig, GlobalAssigner
from samanvay.matching.features import (BlockingConfig, CandidateGenerator, FeatureExtractor,
                                        MatchableFeature)
from samanvay.matching.normalise import normalise_survey_number, normalise_zone, parse_survey_number
from samanvay.raster.terrain import GroundFilterConfig, dsm_to_dtm, extract_structures, normalised_dsm
from samanvay.raster.coreg import coregister, shift_image
from samanvay.topology.repair import RepairConfig, TopologyRepairer
from samanvay.topology.validate import RuleId, Severity, TopologyValidator, validate_network


# --------------------------------------------------------------------------------------
# topology
# --------------------------------------------------------------------------------------


def test_validator_finds_overlap_between_neighbours():
    a = box(0, 0, 10, 10)
    b = box(9, 0, 19, 10)          # 10 m² overlap: land claimed twice
    rep = TopologyValidator().validate({"A": a, "B": b})
    assert rep.counts.get(RuleId.OVERLAP.value, 0) >= 1
    assert rep.total_overlap_area_m2 == pytest.approx(10.0, abs=0.01)
    assert not rep.clean


def test_validator_finds_an_enclosed_gap():
    """A hole inside the fabric is land belonging to nobody — the raw material of a claim."""
    outer = Polygon([(0, 0), (30, 0), (30, 30), (0, 30)],
                    [[(13, 13), (17, 13), (17, 17), (13, 17)]])   # 16 m² hole
    rep = TopologyValidator().validate({"ring": outer})
    assert rep.total_gap_area_m2 == pytest.approx(16.0, abs=0.01)
    assert rep.counts.get(RuleId.GAP.value, 0) == 1


def test_validator_flags_a_sliver_by_compactness_not_only_area():
    sliver = box(0, 0, 40, 0.05)   # 2 m², extremely thin
    rep = TopologyValidator().validate({"S": sliver}, check_partition=False)
    assert rep.counts.get(RuleId.SLIVER.value, 0) == 1


def test_validator_reports_invalid_geometry_as_fatal():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    rep = TopologyValidator().validate({"X": bowtie}, check_partition=False)
    assert any(v.rule is RuleId.INVALID_GEOMETRY and v.severity is Severity.FATAL
               for v in rep.violations)


def test_validating_in_degrees_misclassifies_a_normal_parcel():
    """Documents why the platform refuses to validate in EPSG:4326.

    Every area threshold in the rule set is in square metres. A perfectly ordinary 900 m²
    house plot expressed in degrees has an area of about 7e-8, which is below every
    threshold in the configuration — so a healthy parcel is reported as a defect, and in a
    real run that noise buries the genuine findings.
    """
    metric = box(0, 0, 30, 30)                              # a normal 900 m² plot
    degrees = box(80.0, 13.0, 80.000277, 13.000271)         # the same plot, in degrees
    m_report = TopologyValidator().validate({"P": metric}, check_partition=False)
    d_report = TopologyValidator().validate({"P": degrees}, check_partition=False)
    assert not m_report.counts, f"a healthy parcel must produce no findings: {m_report.counts}"
    assert d_report.counts.get(RuleId.MICRO_AREA.value, 0) == 1


def test_repair_removes_overlap_and_records_what_it_did():
    a = box(0, 0, 10, 10)
    b = box(9.9, 0, 20, 10)        # 1 m² overlap, absorbable
    fixed, rep = TopologyRepairer().repair({"A": a, "B": b})
    assert rep.before.total_overlap_area_m2 > 0
    assert rep.after.total_overlap_area_m2 < rep.before.total_overlap_area_m2
    assert rep.actions, "a repair must always leave an audit trail"


def test_repair_refuses_to_move_a_boundary_beyond_the_ceiling():
    """A 4 m disagreement is a survey dispute, not a digitising error."""
    a = box(0, 0, 10, 10)
    b = box(6, 0, 16, 10)          # 40 m² overlap
    _, rep = TopologyRepairer(RepairConfig(sliver_absorb_max_area_m2=5.0)).repair({"A": a, "B": b})
    assert rep.refused, "a large overlap must be escalated, not silently absorbed"


def test_repair_makes_invalid_geometry_valid():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    fixed, rep = TopologyRepairer().repair({"X": bowtie}, validate_before_after=False)
    assert fixed["X"].is_valid


def test_network_validation_finds_undershoot():
    edges = {
        "e1": LineString([(0, 0), (10, 0)]),
        "e2": LineString([(10.4, 0), (20, 0)]),    # 40 cm short
    }
    rep = validate_network(edges)
    assert rep.undershoots, "a broken network connection must be reported"
    assert rep.n_components == 2


# --------------------------------------------------------------------------------------
# georeferencing
# --------------------------------------------------------------------------------------


def _gcps(n: int = 12, tx: float = 5.0, ty: float = -3.0, noise: float = 0.0):
    rng = np.random.default_rng(11)
    out = []
    for i in range(n):
        sx, sy = float(rng.uniform(0, 500)), float(rng.uniform(0, 500))
        out.append(GroundControlPoint(
            f"G{i}", sx, sy,
            sx + tx + float(rng.normal(0, noise)),
            sy + ty + float(rng.normal(0, noise)),
        ))
    return out


def test_affine_recovers_a_pure_translation():
    gr = GeoReferencer("affine")
    rep = gr.fit(_gcps())
    assert rep.rmse < 1e-6
    assert rep.params["tx"] == pytest.approx(5.0, abs=1e-6)
    assert rep.params["ty"] == pytest.approx(-3.0, abs=1e-6)


def test_blunder_is_detected_and_named():
    pts = _gcps(14, noise=0.02)
    bad = pts[5]
    pts[5] = GroundControlPoint(bad.gcp_id, bad.source_x, bad.source_y,
                                bad.target_x + 25.0, bad.target_y - 18.0)
    rep = GeoReferencer("affine").fit(pts)
    assert bad.gcp_id in rep.blunders, "data snooping must catch a gross control error"


def test_spline_reports_honest_leave_one_out_error():
    """An exact interpolator has zero in-sample residuals. LOO is the honest number."""
    rep = GeoReferencer("tps").fit(_gcps(14, noise=0.4))
    assert rep.rmse < 0.05
    assert rep.loo_rmse is not None and rep.loo_rmse > rep.rmse


def test_model_selection_prefers_the_simplest_adequate_model():
    """A spline must not be chosen when a similarity explains the control just as well."""
    model, rep, all_reports = choose_model(_gcps(16, noise=0.03))
    assert model in ("helmert", "affine"), (
        f"over-fitted to {model}; LOO RMSE by model: "
        f"{ {k: round(v.loo_rmse or v.rmse, 4) for k, v in all_reports.items()} }")


def test_helmert_is_a_true_similarity_and_cannot_shear():
    """An unconstrained affine absorbs a blunder as shear and hides it; a similarity cannot."""
    rep = GeoReferencer("helmert").fit(_gcps(12, noise=0.0))
    assert rep.params["scale_x"] == pytest.approx(rep.params["scale_y"], rel=1e-9)
    assert abs(rep.params["shear_deg"]) < 1e-6


def test_too_few_control_points_is_refused():
    with pytest.raises(ValueError):
        GeoReferencer("polynomial3").fit(_gcps(4))


# --------------------------------------------------------------------------------------
# identifiers and names
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("437", "437"), ("437/2A", "437/2A"), ("437 / 2 A", "437/2A"),
    ("0437-2A", "437/2A"), ("437.2A", "437/2A"), ("௪௩௭", "437"),
])
def test_survey_number_normalisation(raw, expected):
    assert normalise_survey_number(raw) == expected


def test_survey_numbers_of_siblings_never_collapse():
    a, b = parse_survey_number("437/2"), parse_survey_number("437/3")
    assert a.canonical != b.canonical
    assert a.is_sibling_of(b)


def test_survey_number_descendant_relationship():
    parent, child = parse_survey_number("437/2"), parse_survey_number("437/2/1")
    assert child.is_descendant_of(parent)


def test_zone_roman_and_arabic_normalise_together():
    assert normalise_zone("IX") == normalise_zone("09") == "9"


def test_tamil_script_is_detected_and_transliterated():
    assert detect_script("இராமசாமி") == "tamil"
    assert transliterate("இராமசாமி").startswith("ir")


def test_owner_name_variants_are_linked():
    a = normalise_name("Thiru R. Ramaswamy")
    b = normalise_name("Ramasami R")
    assert a.similarity(b) > 0.6, f"similarity was {a.similarity(b)}"


def test_unrelated_names_are_not_linked():
    a = normalise_name("Ramaswamy Krishnan")
    b = normalise_name("Fatima Beevi")
    assert a.similarity(b) < 0.4


def test_place_aliases_resolve():
    assert canonical_place("Madras") == canonical_place("Chennai")


# --------------------------------------------------------------------------------------
# schema matching
# --------------------------------------------------------------------------------------


def test_schema_matcher_maps_an_opaque_column_by_its_values():
    """`kide` carries survey numbers. Name similarity cannot find it; value shape can."""
    records = [{"kide": v, "lgd_village_code": "628573", "created_at": "Nov 7, 2023 10:54:21 AM"}
               for v in ["437", "438/2", "12", "1201/3A", "88", "7/1", "654", "23/2B"] * 30]
    matches = SchemaMatcher().match(records)
    cw = SchemaMatcher().crosswalk(matches)
    assert cw.get("lgd_village_code") == "village_lgd"
    kide = [m for m in matches if m.source_column == "kide"]
    assert kide and kide[0].canonical_field == "survey_number"
    assert kide[0].instance > 0.5, "the value-profiling signal should carry this match"


def test_schema_matcher_will_not_map_two_columns_to_one_field():
    records = [{"district_name": "Chennai", "dist_name": "Chennai"}] * 50
    matches = SchemaMatcher().match(records)
    accepted = [m for m in matches if m.accepted and m.canonical_field == "district_name"]
    assert len(accepted) <= 1


def test_column_profile_identifies_an_identifier():
    p = profile_column("gcc_gis_id", [f"G-089-23-{i:05d}" for i in range(500)])
    assert p.looks_like_identifier
    assert p.dominant_pattern == "gcc_gis_id"


# --------------------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------------------


def _grid(prefix: str, ds: str, n: int = 6, dx: float = 0.0, dy: float = 0.0, size: float = 8.0):
    out = []
    for i in range(n):
        for j in range(n):
            x, y = i * 20 + dx, j * 20 + dy
            out.append(MatchableFeature(f"{prefix}{i}_{j}", ds,
                                        box(x, y, x + size, y + size)))
    return out


def test_blocking_radius_comes_from_declared_accuracy():
    gen = CandidateGenerator(BlockingConfig())
    assert gen.radius_for(1.0, 1.8) == pytest.approx(3.0 * math.hypot(1.0, 1.8))


def test_blocking_reduces_the_pair_space_drastically():
    left, right = _grid("L", "a"), _grid("R", "b", dx=1.0)
    pairs = CandidateGenerator(BlockingConfig()).generate(left, right,
                                                          acc_left_m=1.0, acc_right_m=1.0)
    assert 0 < len(pairs) < len(left) * len(right) * 0.15


def test_features_are_scale_aware():
    ext = FeatureExtractor()
    small_a = MatchableFeature("a", "x", box(0, 0, 5, 5))
    small_b = MatchableFeature("b", "y", box(1, 0, 6, 5))
    big_a = MatchableFeature("c", "x", box(0, 0, 100, 100))
    big_b = MatchableFeature("d", "y", box(1, 0, 101, 100))
    f1 = ext.extract(small_a, small_b)
    f2 = ext.extract(big_a, big_b)
    assert f1["centroid_distance_m"] == pytest.approx(f2["centroid_distance_m"])
    assert f1["centroid_distance_norm"] > f2["centroid_distance_norm"] * 5


def test_turning_function_is_translation_invariant():
    ext = FeatureExtractor()
    a = MatchableFeature("a", "x", box(0, 0, 10, 20))
    b = MatchableFeature("b", "y", box(500, 500, 510, 520))
    assert ext.extract(a, b)["turning_distance"] < 0.1


def test_global_assignment_forbids_double_claiming():
    left = {"L1": MatchableFeature("L1", "a", box(0, 0, 10, 10)),
            "L2": MatchableFeature("L2", "a", box(10, 0, 20, 10))}
    right = {"R1": MatchableFeature("R1", "b", box(0.5, 0, 10.5, 10))}
    pairs = [MatchPair("L1", "R1", "a", "b", probability=0.9),
             MatchPair("L2", "R1", "a", "b", probability=0.8)]
    accepted, rep = GlobalAssigner().assign(pairs, left, right)
    winners = [p for p in accepted if p.accepted]
    assert len({p.right_id for p in winners}) == len(winners)


def test_subdivision_is_detected_by_area_conservation():
    parent = MatchableFeature("P", "a", box(0, 0, 30, 10))
    kids = {f"C{i}": MatchableFeature(f"C{i}", "b", box(i * 10, 0, i * 10 + 10, 10))
            for i in range(3)}
    pairs = [MatchPair("P", k, "a", "b", probability=0.9) for k in kids]
    accepted, rep = GlobalAssigner().assign(pairs, {"P": parent}, kids)
    assert all(p.cardinality is Cardinality.ONE_TO_MANY for p in accepted)
    assert rep.cardinality_counts.get("1:N", 0) == 3


def test_a_bad_match_set_is_not_mistaken_for_a_subdivision():
    """Children covering 30% of the parent are a bad match set, not a split."""
    parent = MatchableFeature("P", "a", box(0, 0, 30, 10))
    kids = {"C0": MatchableFeature("C0", "b", box(0, 0, 5, 5)),
            "C1": MatchableFeature("C1", "b", box(20, 0, 25, 5))}
    pairs = [MatchPair("P", k, "a", "b", probability=0.9) for k in kids]
    accepted, _ = GlobalAssigner().assign(pairs, {"P": parent}, kids)
    assert not any(p.cardinality is Cardinality.ONE_TO_MANY for p in accepted)


# --------------------------------------------------------------------------------------
# evidence fusion
# --------------------------------------------------------------------------------------


def _registry() -> SourceRegistry:
    r = SourceRegistry()
    for did, st, acc in [("CORS", SourceType.GNSS_CORS, 0.02),
                         ("ORI", SourceType.ORI, 0.10),
                         ("REV", SourceType.REVENUE_RECORD, 8.0),
                         ("MUN", SourceType.MUNICIPAL_GIS, 1.0)]:
        r.register(SourceDataset(did, did, st, AUTHORITIES["DOLR"], "CC0", "EPSG:4326",
                                 positional_accuracy_m=acc))
    return r


def _ev(reg, did, st, prop, value) -> Evidence:
    c = Claim(did, st, prop, value)
    return Evidence(c, reg.reliability(did, prop), reg.recency_weight(did),
                    reg.accuracy_weight(did))


def test_single_source_puts_the_remainder_on_ignorance_not_on_negation():
    m = MassFunction({"X": 0.7}, 0.3)
    assert m.belief("X") == pytest.approx(0.7)
    assert m.plausibility("X") == pytest.approx(1.0)
    assert m.plausibility("Y") == pytest.approx(0.3)


def test_agreeing_sources_raise_belief():
    m1 = MassFunction({"X": 0.6}, 0.4)
    m2 = MassFunction({"X": 0.6}, 0.4)
    combined, k = combine(m1, m2)
    assert k == 0.0
    assert combined.belief("X") > 0.6


def test_conflicting_sources_produce_conflict_mass():
    m1 = MassFunction({"X": 0.8}, 0.2)
    m2 = MassFunction({"Y": 0.8}, 0.2)
    _, k = combine(m1, m2)
    assert k > 0.5, "strong disagreement must surface as conflict, not be normalised away"


def test_revenue_record_wins_on_tenure_and_loses_on_position():
    reg = _registry()
    assert reg.reliability("REV", "tenure.owner_name") > reg.reliability("ORI", "tenure.owner_name")
    assert reg.reliability("ORI", "geometry") > reg.reliability("REV", "geometry")


def test_fusion_selects_the_corroborated_value():
    reg = _registry()
    evs = [_ev(reg, "MUN", SourceType.MUNICIPAL_GIS, "land_use", "residential"),
           _ev(reg, "ORI", SourceType.ORI, "land_use", "residential"),
           _ev(reg, "REV", SourceType.REVENUE_RECORD, "land_use", "agricultural")]
    value, bel, pl, k, detail = fuse(evs)
    assert value == "residential"
    assert detail["n_hypotheses"] == 2


def test_independence_discounting_reduces_manufactured_confidence():
    """Three derivatives of one 1970s village map are one witness, not three."""
    reg = _registry()
    for did in ("D1", "D2", "D3"):
        reg.register(SourceDataset(did, did, SourceType.MUNICIPAL_GIS, AUTHORITIES["DOLR"],
                                   "CC0", "EPSG:4326", positional_accuracy_m=1.0))
    evs = [_ev(reg, d, SourceType.MUNICIPAL_GIS, "land_use", "residential")
           for d in ("D1", "D2", "D3")]
    shared_lineage = {d: {"VILLAGE_MAP_1973"} for d in ("D1", "D2", "D3")}
    _, bel_indep, _, _, _ = fuse(evs)
    _, bel_shared, _, _, detail = fuse(evs, lineage=shared_lineage)
    assert bel_shared < bel_indep
    assert all(f < 1.0 for f in detail["independence_factors"])


# --------------------------------------------------------------------------------------
# confidence
# --------------------------------------------------------------------------------------


def test_single_source_scores_lower_agreement_than_three():
    reg = _registry()
    scorer = ConfidenceScorer(reg)
    one = scorer.score("E", claims=[Claim("MUN", SourceType.MUNICIPAL_GIS, "geometry", "x")],
                       resolutions=[], attributes={}, independent_sources=1)
    three = scorer.score("E", claims=[Claim("MUN", SourceType.MUNICIPAL_GIS, "geometry", "x")],
                         resolutions=[], attributes={}, independent_sources=3)
    assert three.source_agreement > one.source_agreement


def test_better_positional_accuracy_scores_higher():
    reg = _registry()
    scorer = ConfidenceScorer(reg)
    cors = scorer.score("E", claims=[Claim("CORS", SourceType.GNSS_CORS, "geometry", "x",
                                           accuracy_m=0.02)],
                        resolutions=[], attributes={})
    rev = scorer.score("E", claims=[Claim("REV", SourceType.REVENUE_RECORD, "geometry", "x",
                                          accuracy_m=8.0)],
                       resolutions=[], attributes={})
    assert cors.positional > rev.positional + 0.3


def test_grades_are_ordered_and_the_explanation_names_the_weakest_dimension():
    reg = _registry()
    r = ConfidenceScorer(reg).score(
        "E", claims=[Claim("MUN", SourceType.MUNICIPAL_GIS, "geometry", "x")],
        resolutions=[], attributes={"ward": "89"}, independent_sources=1)
    assert r.grade in "ABCDE"
    name, _ = r.weakest()
    assert name.replace("_", " ") in r.explain()


# --------------------------------------------------------------------------------------
# change detection
# --------------------------------------------------------------------------------------


def test_layer_wide_reregistration_is_not_reported_as_change():
    """The single most important behaviour: a re-survey is not two thousand mutations."""
    before = {f"B{i}": box(i * 20, 0, i * 20 + 10, 10) for i in range(40)}
    after = {f"A{i}": box(i * 20 + 1.2, 0.4, i * 20 + 11.2, 10.4) for i in range(40)}
    pairs = [MatchPair(f"B{i}", f"A{i}", "old", "new", probability=0.95, accepted=True)
             for i in range(40)]
    det = ChangeDetector()
    recs = det.detect(pairs, before, after)
    actionable = [r for r in recs if r.is_actionable]
    assert not actionable, f"{len(actionable)} spurious mutations from a pure re-registration"
    assert all(r.change_type in (ChangeType.NO_CHANGE, ChangeType.POSITIONAL_ONLY)
               for r in recs)


def test_a_genuine_extension_survives_offset_removal():
    before = {f"B{i}": box(i * 20, 0, i * 20 + 10, 10) for i in range(40)}
    after = {f"A{i}": box(i * 20 + 1.2, 0.4, i * 20 + 11.2, 10.4) for i in range(40)}
    after["A7"] = box(7 * 20 + 1.2, 0.4, 7 * 20 + 18.0, 10.4)     # a real extension
    pairs = [MatchPair(f"B{i}", f"A{i}", "old", "new", probability=0.95, accepted=True)
             for i in range(40)]
    recs = ChangeDetector().detect(pairs, before, after)
    ext = [r for r in recs if r.change_type is ChangeType.EXTENSION]
    assert len(ext) == 1 and ext[0].entity_id == "B7"


def test_cross_source_mode_never_reports_a_demolition():
    """Contemporaneous departments disagreeing is not a mutation."""
    before = {"B1": box(0, 0, 10, 10)}
    after: dict = {}
    recs = ChangeDetector(ChangeConfig(mode="cross_source")).detect(
        [], before, after, unmatched_before=["B1"])
    assert recs[0].change_type is ChangeType.SOURCE_OMISSION
    assert not recs[0].is_actionable


def test_temporal_mode_does_report_a_demolition():
    before = {"B1": box(0, 0, 10, 10)}
    recs = ChangeDetector(ChangeConfig(mode="temporal")).detect(
        [], before, {}, unmatched_before=["B1"])
    assert recs[0].change_type is ChangeType.DEMOLITION
    assert recs[0].is_actionable


def test_structure_on_public_land_is_flagged_for_verification_not_determined():
    public = {"PORAMBOKE": box(0, 0, 50, 50)}
    after = {"NEW": box(10, 10, 25, 25)}
    recs = ChangeDetector().detect([], {}, after, unmatched_after=["NEW"], public_land=public)
    assert recs[0].change_type is ChangeType.ENCROACHMENT
    assert "verification" in recs[0].evidence[-1].lower()


# --------------------------------------------------------------------------------------
# footprint regularisation
# --------------------------------------------------------------------------------------


def test_a_rectangle_is_rectilinear_and_a_circle_is_not():
    assert rectilinearity(box(0, 0, 10, 20)) > 0.95
    circle = box(0, 0, 10, 10).centroid.buffer(5, quad_segs=32)
    assert rectilinearity(circle) < 0.4


def test_regularisation_reduces_vertices_without_moving_the_area():
    rng = np.random.default_rng(3)
    pts = []
    for x in np.linspace(0, 20, 40):
        pts.append((x, 0 + rng.normal(0, 0.06)))
    for y in np.linspace(0, 12, 24):
        pts.append((20 + rng.normal(0, 0.06), y))
    for x in np.linspace(20, 0, 40):
        pts.append((x, 12 + rng.normal(0, 0.06)))
    for y in np.linspace(12, 0, 24):
        pts.append((0 + rng.normal(0, 0.06), y))
    wobbly = Polygon(pts)
    out, rep = regularise({"B": wobbly}, RegulariseConfig())
    assert rep.vertex_reduction > 0.5
    assert abs(out["B"].area - wobbly.area) / wobbly.area < 0.05


def test_regularisation_leaves_a_genuinely_curved_building_alone():
    circle = box(0, 0, 10, 10).centroid.buffer(6, quad_segs=32)
    _, rep = regularise({"C": circle})
    assert rep.n_refused_non_rectilinear == 1


def test_extraction_qc_drops_low_confidence_and_thin_shapes():
    feats = {"good": box(0, 0, 10, 10), "thin": box(0, 0, 60, 0.2), "unsure": box(0, 0, 12, 12)}
    attrs = {"good": {"confidence": 0.9}, "thin": {"confidence": 0.9},
             "unsure": {"confidence": 0.2}}
    kept, reasons = ExtractionQC().filter(feats, attrs)
    assert set(kept) == {"good"}
    assert "low_model_confidence" in reasons and "too_thin_to_be_a_building" in reasons


# --------------------------------------------------------------------------------------
# terrain and raster
# --------------------------------------------------------------------------------------


def test_ground_filter_puts_the_terrain_at_zero_and_finds_the_building():
    x, y = np.meshgrid(np.linspace(0, 100, 200), np.linspace(0, 100, 200))
    dsm = (0.08 * x).astype(np.float32)                     # a gentle real slope
    dsm[60:120, 60:120] += 9.0                              # a 9 m building
    dtm, ground, rep = dsm_to_dtm(dsm, GroundFilterConfig(cell_size_m=0.5, max_window_m=45))
    ndsm = normalised_dsm(dsm, dtm)
    assert float(np.median(ndsm)) < 0.5, "terrain should sit at zero in the nDSM"
    assert 7.0 < float(ndsm[80:100, 80:100].mean()) < 10.0, "the building height must survive"
    _, structures = extract_structures(ndsm, cell_size_m=0.5, min_height_m=2.0,
                                       min_area_m2=20.0)
    assert structures, "the building should be segmented from height alone"


def test_dtm_never_rises_above_the_dsm():
    rng = np.random.default_rng(5)
    dsm = rng.normal(50, 3, (150, 150)).astype(np.float32)
    dtm, _, _ = dsm_to_dtm(dsm, GroundFilterConfig(cell_size_m=1.0, max_window_m=20))
    assert (dtm <= dsm + 1e-4).all(), "a DTM above the DSM produces phantom demolitions"


def test_phase_correlation_recovers_a_known_shift():
    rng = np.random.default_rng(9)
    base = rng.random((256, 256)).astype(np.float32)
    from scipy.ndimage import gaussian_filter
    base = gaussian_filter(base, 3)
    moved = shift_image(base, 4.0, -6.0)
    res = coregister(base, moved, pixel_size_m=0.1)
    assert res.reliable, res.notes
    # the returned pair is the displacement OF THE TARGET relative to the reference,
    # so that shift_image(target, -dy, -dx) undoes it
    assert res.dy_px == pytest.approx(4.0, abs=0.35)
    assert res.dx_px == pytest.approx(-6.0, abs=0.35)
    realigned = shift_image(moved, -res.dy_px, -res.dx_px)
    from samanvay.raster.coreg import normalised_cross_correlation
    assert normalised_cross_correlation(base, realigned) > 0.99


def test_coregistration_refuses_an_ambiguous_pair():
    rng = np.random.default_rng(2)
    a = rng.random((256, 256)).astype(np.float32)
    b = rng.random((256, 256)).astype(np.float32)   # unrelated noise
    res = coregister(a, b, pixel_size_m=0.1)
    assert not res.reliable, "an ambiguous correlation peak must not be applied"
