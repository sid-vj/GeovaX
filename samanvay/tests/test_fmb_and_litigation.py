"""Unit tests for Generative Draft FMB and Predictive Litigation Hotspot Mapping."""
import xml.etree.ElementTree as ET
from samanvay.cadastre.fmb import generate_fmb, to_collabland_xml, to_fmb_svg
from samanvay.analytics.litigation import (
    calculate_litigation_risk,
    build_litigation_hotspots,
    ECourtsConnector,
    RegistrationConnector,
)


def test_fmb_generation_quadrilateral():
    # Regular 20m x 30m rectangle in local coordinates
    coords = [(0.0, 0.0), (30.0, 0.0), (30.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    meta = {
        "ulpin": "IN-TN-CHN-00123",
        "survey_number": "145",
        "subdivision": "2B",
        "village": "Kilpauk",
        "taluk": "Egmore",
    }
    rec = generate_fmb(coords, meta)

    assert rec.ulpin == "IN-TN-CHN-00123"
    assert rec.survey_number == "145"
    assert rec.subdivision == "2B"
    assert rec.area_sqm == 600.0
    assert rec.area_cents > 14.0  # 600 / 40.4686 ≈ 14.83 cents

    # Baseline should be the diagonal (sqrt(30^2 + 20^2) ≈ 36.06m)
    assert abs(rec.baseline_length_m - 36.06) < 0.2
    assert len(rec.ladder_points) == 4
    assert len(rec.f_lines) == 4

    # Ladder points should include both Left and Right offsets or collinear
    sides = {lp.side for lp in rec.ladder_points}
    assert "L" in sides or "R" in sides


def test_collabland_xml_serialization():
    coords = [(80.241, 13.061), (80.244, 13.061), (80.244, 13.064), (80.241, 13.064)]
    rec = generate_fmb(coords, {"ulpin": "TEST-XML-ULPIN", "survey_number": "99"})
    xml_str = to_collabland_xml(rec)

    assert "<CollabLand" in xml_str
    assert 'standard="NIC-DILRMP"' in xml_str
    assert 'ULPIN="TEST-XML-ULPIN"' in xml_str
    assert "<BaseLine" in xml_str
    assert "<LadderOffsets>" in xml_str
    assert "<Boundaries>" in xml_str

    # Must be well-formed XML
    root = ET.fromstring(xml_str)
    assert root.tag == "CollabLand"
    fmb_el = root.find("FMB")
    assert fmb_el is not None
    assert fmb_el.attrib["SurveyNo"] == "99"


def test_fmb_svg_sketch():
    coords = [(0.0, 0.0), (40.0, 0.0), (30.0, 25.0), (10.0, 20.0)]
    rec = generate_fmb(coords, {"ulpin": "TEST-SVG-ULPIN", "village": "Nungambakkam"})
    svg = to_fmb_svg(rec)

    assert "<svg" in svg
    assert "</svg>" in svg
    assert "FIELD MEASUREMENT BOOK (FMB)" in svg
    assert "G-Line:" in svg
    assert "TEST-SVG-ULPIN" in svg
    assert "<polygon points=" in svg


def test_litigation_connectors_and_scoring():
    court_conn = ECourtsConnector(
        seed_records={
            "kilpauk:42": [
                {
                    "cnr_number": "TNCH01-00042-2023",
                    "case_type": "Original Suit (Title)",
                    "court_name": "City Civil Court, Chennai",
                    "year": 2023,
                    "petitioner": "Govt of TN",
                    "respondent": "Private Encroacher",
                    "status": "Stay Granted",
                }
            ]
        }
    )
    reg_conn = RegistrationConnector(
        seed_flags={"kilpauk:42": ["Injunction Order OS/42/2023"]}
    )

    parcel = {
        "properties": {
            "ulpin": "IN-TN-CHN-00042",
            "survey_number": "42",
            "subdivision": "1",
            "village_name": "Kilpauk",
            "conf_source_agreement": 0.3,  # High conflict K = 0.7
            "confidence_grade": "D",
        }
    }

    assessment = calculate_litigation_risk(parcel, court_conn, reg_conn)
    assert assessment.ulpin == "IN-TN-CHN-00042"
    assert assessment.conflict_mass_k == 0.7
    assert len(assessment.court_cases) == 1
    assert assessment.court_cases[0].status == "Stay Granted"
    assert len(assessment.ec_dispute_flags) == 1
    # High conflict + stay granted + EC flag + grade D => CRITICAL
    assert assessment.risk_tier == "CRITICAL"
    assert assessment.risk_score >= 0.70
    assert len(assessment.risk_drivers) >= 3


def test_build_litigation_hotspots_geojson():
    parcels = [
        {
            "geometry": {"type": "Polygon", "coordinates": [[[80.24, 13.06], [80.25, 13.06], [80.25, 13.07], [80.24, 13.06]]]},
            "properties": {
                "ulpin": f"ULPIN-{i}",
                "survey_number": str(i),
                "conf_source_agreement": 0.2 if i % 2 == 0 else 0.9,
                "confidence_grade": "D" if i % 2 == 0 else "B",
                "village_name": "Kilpauk",
            },
        }
        for i in range(1, 11)
    ]

    hotspots = build_litigation_hotspots(parcels, min_risk=0.3)
    assert hotspots["type"] == "FeatureCollection"
    assert "metadata" in hotspots
    assert hotspots["metadata"]["total_parcels_evaluated"] == 10
    assert hotspots["metadata"]["flagged_hotspots_count"] > 0
    assert len(hotspots["features"]) == hotspots["metadata"]["flagged_hotspots_count"]

    first = hotspots["features"][0]
    assert "litigation_risk_score" in first["properties"]
    assert "risk_tier" in first["properties"]
    assert "recommended_action" in first["properties"]
