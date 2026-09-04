"""Predictive Litigation Hotspot Mapping Engine.

Fuses internal spatial-evidential tension (Dempster-Shafer Conflict Mass K) with external
judicial and land registry feeds:
1. e-Courts Services / National Judicial Data Grid (NJDG) property dispute records.
2. State Registration Department (e.g., TN STAR 2.0) Encumbrance Certificate (EC) court stays.
3. Computes a multi-factor Litigation Risk Index [0.0 - 1.0] and categorises parcels into Risk Tiers.
4. Emits OGC-compliant GeoJSON FeatureCollections and cluster metrics for District Collectors.

On the two external feeds: this environment has no credentials or documented public bulk API
for either e-Courts/NJDG or a state Registration Department's EC system (both are per-case /
per-parcel citizen lookup services, not open data). ``ECourtsConnector`` and
``RegistrationConnector`` therefore make a real, correctly-shaped HTTP request against a
configurable endpoint when one is set, and otherwise return an explicit, empty,
``data_source="not_configured"`` result — never a plausible-looking fabricated case. The risk
math in ``calculate_litigation_risk`` is real and untouched; only its two external inputs
changed from `hash(key) % 100` simulation to this honest pattern.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CourtCase:
    cnr_number: str
    case_type: str  # 'Original Suit (Title)', 'Injunction', 'Eviction', 'Partition'
    court_name: str
    year: int
    petitioner: str
    respondent: str
    status: str  # 'Pending', 'Stay Granted', 'Disposed'


@dataclass
class LitigationAssessment:
    ulpin: str
    survey_number: str
    subdivision: str
    village: str
    ward: str
    conflict_mass_k: float
    confidence_grade: str
    court_cases: list[CourtCase] = field(default_factory=list)
    ec_dispute_flags: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    risk_tier: str = "LOW"  # 'CRITICAL', 'HIGH', 'MODERATE', 'LOW'
    risk_drivers: list[str] = field(default_factory=list)
    recommended_action: str = ""
    court_data_source: str = "not_configured"
    """"seed" (test fixture), "live" (a real ECOURTS_API_URL was queried), or
    "not_configured" (no external e-Courts source available — court_cases is honestly
    empty, not simulated)."""
    ec_data_source: str = "not_configured"


class ECourtsConnector:
    """Connector for e-Courts Services & National Judicial Data Grid (NJDG).

    No credentials or documented public bulk API exist for this environment. Set
    ``ECOURTS_API_URL`` to a real endpoint to make this genuinely live; without it, every
    query honestly returns no cases rather than a fabricated one. ``seed_records`` remains
    for tests that want to exercise the risk-scoring math against known, explicit inputs —
    that is real test fixture data, not a claim about e-Courts itself.
    """

    def __init__(self, seed_records: dict[str, list[dict[str, Any]]] | None = None,
                 api_url: str | None = None) -> None:
        self._cache = seed_records or {}
        self.api_url = api_url or os.environ.get("ECOURTS_API_URL")
        self.data_source = "seed" if seed_records else (
            "live" if self.api_url else "not_configured")

    def fetch_cases_by_survey(self, village: str, survey_number: str) -> list[CourtCase]:
        """Fetch pending civil suits matching survey number and village."""
        key = f"{village.lower()}:{survey_number}"
        if key in self._cache:
            return [CourtCase(**c) for c in self._cache[key]]
        if not self.api_url:
            return []
        try:
            import httpx
            resp = httpx.get(self.api_url, params={"village": village, "survey_number": survey_number},
                              timeout=10.0)
            resp.raise_for_status()
            return [CourtCase(**c) for c in resp.json().get("cases", [])]
        except Exception as err:  # noqa: BLE001
            logger.warning("ECourtsConnector: live fetch failed (%s); returning no cases.", err)
            return []


class RegistrationConnector:
    """Connector for State Registration Department Encumbrance Certificates (EC).

    Same honest pattern as ``ECourtsConnector``: set ``REGISTRATION_API_URL`` for a real
    endpoint; otherwise this reports no flags rather than inventing lis-pendens entries.
    """

    def __init__(self, seed_flags: dict[str, list[str]] | None = None,
                 api_url: str | None = None) -> None:
        self._cache = seed_flags or {}
        self.api_url = api_url or os.environ.get("REGISTRATION_API_URL")
        self.data_source = "seed" if seed_flags else (
            "live" if self.api_url else "not_configured")

    def fetch_ec_flags(self, village: str, survey_number: str) -> list[str]:
        """Fetch lis pendens, attachment, or dispute flags registered against the parcel."""
        key = f"{village.lower()}:{survey_number}"
        if key in self._cache:
            return self._cache[key]
        if not self.api_url:
            return []
        try:
            import httpx
            resp = httpx.get(self.api_url, params={"village": village, "survey_number": survey_number},
                              timeout=10.0)
            resp.raise_for_status()
            return list(resp.json().get("flags", []))
        except Exception as err:  # noqa: BLE001
            logger.warning("RegistrationConnector: live fetch failed (%s); returning no flags.", err)
            return []


def calculate_litigation_risk(
    parcel: dict[str, Any],
    court_connector: ECourtsConnector | None = None,
    reg_connector: RegistrationConnector | None = None,
) -> LitigationAssessment:
    """Calculate multi-factor litigation risk index combining geometric uncertainty and legal flags."""
    court = court_connector or ECourtsConnector()
    reg = reg_connector or RegistrationConnector()

    props = parcel.get("properties", parcel)
    ulpin = str(props.get("ulpin") or props.get("entity_id") or "ULPIN-UNKNOWN")
    survey_no = str(props.get("survey_number") or "0")
    subdiv = str(props.get("subdivision") or "1")
    village = str(props.get("village_name") or props.get("village") or "Chennai")
    ward = str(props.get("ward") or "Central")
    grade = str(props.get("confidence_grade") or "C")

    # Dempster-Shafer evidential conflict mass K
    # If source agreement is high (e.g. 0.9), conflict is low (0.1)
    src_agreement = float(props.get("conf_source_agreement") or 0.6)
    conflict_k = max(0.0, min(1.0, 1.0 - src_agreement))

    # Query external connectors
    cases = court.fetch_cases_by_survey(village, survey_no)
    ec_flags = reg.fetch_ec_flags(village, survey_no)

    # Multi-factor scoring model
    # 1. Evidential Conflict Weight (0.35)
    # 2. Active Court Suits Weight (0.35)
    # 3. Encumbrance Flags Weight (0.20)
    # 4. Low Confidence Grade Penalty (0.10)
    w_conflict = conflict_k * 0.35

    case_score = 0.0
    for c in cases:
        if c.status == "Stay Granted":
            case_score = max(case_score, 1.0)
        elif "Title" in c.case_type or "Injunction" in c.case_type:
            case_score = max(case_score, 0.8)
        else:
            case_score = max(case_score, 0.5)
    w_cases = case_score * 0.35

    w_ec = (1.0 if ec_flags else 0.0) * 0.20
    w_grade = 0.10 if grade in ("D", "E") else 0.0

    total_risk = round(min(1.0, w_conflict + w_cases + w_ec + w_grade), 3)

    # Determine Tier
    drivers: list[str] = []
    if conflict_k > 0.5:
        drivers.append(f"High inter-departmental conflict mass (K = {conflict_k:.2f})")
    if cases:
        drivers.append(f"{len(cases)} active e-Courts dispute(s): {', '.join(c.case_type for c in cases)}")
    if ec_flags:
        drivers.append(f"Encumbrance Certificate flags: {', '.join(ec_flags)}")
    if grade in ("D", "E"):
        drivers.append(f"Poor spatial baseline grade ({grade}) against NAKSHA 0.5m spec")

    if total_risk >= 0.70:
        tier = "CRITICAL"
        rec = "Block automated registration; Issue Section 7 show-cause notice; Order immediate 0.05m RTK drone re-survey."
    elif total_risk >= 0.45:
        tier = "HIGH"
        rec = "Route to Tahsildar adjudication queue with e-Courts docket attachment; flag in Land Registry."
    elif total_risk >= 0.25:
        tier = "MODERATE"
        rec = "Desk verification with revenue FMB records; publish harmonised boundary with caution flag."
    else:
        tier = "LOW"
        rec = "Proceed to automated ULPIN finalization and Merkle provenance anchoring."

    return LitigationAssessment(
        ulpin=ulpin,
        survey_number=survey_no,
        subdivision=subdiv,
        village=village,
        ward=ward,
        conflict_mass_k=round(conflict_k, 3),
        confidence_grade=grade,
        court_cases=cases,
        ec_dispute_flags=ec_flags,
        risk_score=total_risk,
        risk_tier=tier,
        risk_drivers=drivers,
        recommended_action=rec,
        court_data_source=court.data_source,
        ec_data_source=reg.data_source,
    )


def build_litigation_hotspots(
    parcels: list[dict[str, Any]],
    min_risk: float = 0.45,
) -> dict[str, Any]:
    """Generate GeoJSON FeatureCollection of parcels flagged for litigation risk for map display."""
    court_conn = ECourtsConnector()
    reg_conn = RegistrationConnector()

    features = []
    tier_counts = {"CRITICAL": 0, "HIGH": 0, "MODERATE": 0, "LOW": 0}

    for p in parcels:
        assessment = calculate_litigation_risk(p, court_conn, reg_conn)
        tier_counts[assessment.risk_tier] = tier_counts.get(assessment.risk_tier, 0) + 1

        if assessment.risk_score >= min_risk:
            feat = {
                "type": "Feature",
                "geometry": p.get("geometry"),
                "properties": {
                    "ulpin": assessment.ulpin,
                    "survey_number": f"{assessment.survey_number}/{assessment.subdivision}",
                    "village": assessment.village,
                    "ward": assessment.ward,
                    "litigation_risk_score": assessment.risk_score,
                    "risk_tier": assessment.risk_tier,
                    "conflict_mass_k": assessment.conflict_mass_k,
                    "active_cases_count": len(assessment.court_cases),
                    "active_cases": [
                        {"cnr": c.cnr_number, "type": c.case_type, "status": c.status}
                        for c in assessment.court_cases
                    ],
                    "ec_flags": assessment.ec_dispute_flags,
                    "risk_drivers": assessment.risk_drivers,
                    "recommended_action": assessment.recommended_action,
                    "court_data_source": assessment.court_data_source,
                    "ec_data_source": assessment.ec_data_source,
                },
            }
            features.append(feat)

    # Sort high-risk features first
    features.sort(key=lambda f: f["properties"]["litigation_risk_score"], reverse=True)

    return {
        "type": "FeatureCollection",
        "metadata": {
            "title": "SAMANVAY Predictive Litigation Hotspot Layer",
            "total_parcels_evaluated": len(parcels),
            "flagged_hotspots_count": len(features),
            "tier_summary": tier_counts,
            "fusion_model": "Dempster-Shafer K + e-Courts NJDG + Registration Lis Pendens",
            "court_data_source": court_conn.data_source,
            "ec_data_source": reg_conn.data_source,
            "data_source_note": (
                "'not_configured' means no live e-Courts/NJDG or Registration Department "
                "API is reachable from this environment; the conflict_mass_k-driven risk "
                "score is still real, computed from actual harmonisation evidence, but no "
                "external court/EC records are included until ECOURTS_API_URL / "
                "REGISTRATION_API_URL are configured."
            ),
        },
        "features": features,
    }
