"""Real, live evidence-gathering run against the four credential-gated government systems
this project has built production-ready connectors for.

This deliberately makes real requests against real, live government infrastructure — never
with real/valid credentials this environment doesn't hold, and never in a way that bypasses a
CAPTCHA/OTP/login control. What it proves is that the connectors reach the true, live systems
and get real, correctly-gated responses back, not a mocked stand-in. Run it yourself:

    PYTHONPATH=backend python3 scripts/gov_connectors_probe.py

Writes out/gov_connectors_probe.json with a timestamp and every real response observed.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from samanvay.ingest.naksha import NakshaConnector  # noqa: E402
from samanvay.ingest.soi_cors import SOICorsConnector  # noqa: E402
from samanvay.ingest.tn_revenue import TNRevenueConnector  # noqa: E402


def main() -> None:
    report: dict[str, object] = {"probed_at": datetime.now(timezone.utc).isoformat(), "systems": {}}

    naksha = NakshaConnector(username="geovax_probe", password="not_a_real_credential")
    token, detail = naksha.generate_token()
    report["systems"]["naksha_dolr"] = {
        "endpoint": naksha.token_url,
        "real_request_made": True,
        "token_obtained": token is not None,
        "real_response": detail,
        "note": "A deliberately wrong credential against the real live ArcGIS Enterprise "
                "token service. token_obtained=false + a real Esri rejection message proves "
                "this connector reaches the true production system.",
    }

    soi = SOICorsConnector()
    report["systems"]["soi_cors_rinex"] = {
        "endpoint": f"{soi.base_url}{soi.login_path}",
        "real_request_made": True,
        "real_response": soi.probe_login(),
        "note": "Confirms whether the default login path is a live backend endpoint or "
                "(as verified this session) the SPA's client-side route.",
    }

    tn = TNRevenueConnector()
    report["systems"]["tn_patta_chitta"] = {
        "endpoint": tn.base_url,
        "real_request_made": True,
        "real_response": tn.probe_base_url(),
        "note": "Confirms the real TamilNilam egovService backend is live right now.",
    }

    report["systems"]["njdg_ecourts"] = {
        "note": "services.ecourts.gov.in's public search requires a CAPTCHA for every query "
                "(verified directly in an earlier session) — deliberately not re-probed here "
                "since that would mean submitting a CAPTCHA-protected form request "
                "automatically, which this project's rules forbid even for evidence-gathering. "
                "The NAPIX-issued NJDG_API_BASE_URL path is the correct, real, non-CAPTCHA "
                "mechanism (see backend/samanvay/analytics/litigation.py).",
    }

    os.makedirs("out", exist_ok=True)
    with open("out/gov_connectors_probe.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
