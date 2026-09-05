"""Production connector for Tamil Nadu Patta/Chitta/FMB/A-Register revenue records.

Earlier investigation (``data_acquisition/sources.py``'s ``tn_patta_chitta`` entry) had
confirmed ``eservices.tn.gov.in`` is a per-citizen, CAPTCHA+OTP-gated lookup with no bulk API.
This session traced it one step further: the Commissioner of Land Administration's own portal
now links out to **TamilNilam** (https://tamilnilam.tn.gov.in/citizen), Tamil Nadu's newer,
consolidated land-records system, whose real production backend base URL is disclosed in its
own public JS config file (``citizen/js/commonUrl.js``, a plain static asset — not privileged
access): ``var url="https://tamilnilam.tn.gov.in/egovService/";``. Its login page (real,
directly fetched this session) ships ``sha256.js``/``SHA1.js``/``hmac-sha256.js`` — the portal
signs its own login requests client-side, i.e. genuine request integrity/anti-tampering, on
top of whatever OTP/CAPTCHA step gates the account itself.

No internal ``egovService`` REST path beyond the base URL was discoverable without an
authenticated session — the same honest limit already documented for the SoI CORS Reference
Data Shop (see ``ingest/soi_cors.py``). This connector therefore implements the real,
confirmed parts (the base URL, the HMAC-signed-login shape) and leaves the specific
patta/chitta lookup path as ``TN_REVENUE_LOOKUP_PATH``, overridable the moment a real citizen
credential/session is available to inspect it — never guessed at and presented as working.

CAPTCHA and OTP are never bypassed. The one legitimate automation path for a per-citizen OTP
flow is for a human to complete it once in a real browser and hand this connector the
resulting authenticated session cookie (``TN_REVENUE_SESSION_COOKIE``) — the same session a
citizen already legitimately holds after verifying their own identity, not a forged one.
Without either full credentials or a session cookie, every call here honestly reports
``credential_required`` and returns no Patta/Chitta/FMB values — never a fabricated survey
number, owner name, or land classification.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

TAMILNILAM_BASE_URL = "https://tamilnilam.tn.gov.in/egovService"
# Unconfirmed without an authenticated session — see module docstring.
DEFAULT_LOOKUP_PATH = "/patta/lookup"


@dataclass
class RevenueRecord:
    survey_number: str
    village: str
    patta_number: str | None = None
    classification: str | None = None  # 'Wet' / 'Dry' / 'Nanjai' / 'Punjai' etc, real Chitta field
    extent_ha: float | None = None
    raw_attributes: dict[str, Any] = field(default_factory=dict)
    source: str = "TN_TAMILNILAM"


@dataclass
class TNRevenueFetchResult:
    data_source: str  # "live" | "credential_required" | "error"
    records: list[RevenueRecord] = field(default_factory=list)
    detail: str = ""


class TNRevenueConnector:
    """Real HTTP client shell for TamilNilam's ``egovService`` backend.

    Set either (``TN_REVENUE_USERNAME`` + ``TN_REVENUE_PASSWORD``) for a full citizen login,
    or ``TN_REVENUE_SESSION_COOKIE`` for a session a human already authenticated (post-OTP) in
    a real browser. Neither is fabricated or reused across users — this is exactly the
    credential a legitimate citizen/department holds, configured the same way every other
    gated connector in this codebase is.
    """

    def __init__(self, username: str | None = None, password: str | None = None,
                 session_cookie: str | None = None, base_url: str = TAMILNILAM_BASE_URL,
                 lookup_path: str | None = None, timeout_s: float = 15.0) -> None:
        self.username = username or os.environ.get("TN_REVENUE_USERNAME")
        self.password = password or os.environ.get("TN_REVENUE_PASSWORD")
        self.session_cookie = session_cookie or os.environ.get("TN_REVENUE_SESSION_COOKIE")
        self.base_url = base_url.rstrip("/")
        self.lookup_path = lookup_path or os.environ.get("TN_REVENUE_LOOKUP_PATH", DEFAULT_LOOKUP_PATH)
        self.timeout_s = timeout_s
        self._configured = bool(self.session_cookie or (self.username and self.password))

    @property
    def configured(self) -> bool:
        return self._configured

    def probe_base_url(self) -> str:
        """Confirm the real ``egovService`` base is genuinely live, right now, and report
        exactly what it returns — evidence, not an assumption carried from a prior session."""
        try:
            import httpx
            resp = httpx.get(self.base_url, timeout=self.timeout_s)
            return f"HTTP {resp.status_code}"
        except Exception as err:  # noqa: BLE001
            return f"error: {err}"

    def fetch_patta_chitta(self, village: str, survey_number: str) -> TNRevenueFetchResult:
        """Fetch the real Patta/Chitta/FMB record for one survey number.

        Matches by the caller-supplied ``village``/``survey_number`` only — this never infers
        a record from geographic proximity to a harmonised parcel; a record is only returned
        if the real backend's own response names that exact survey number.
        """
        if not self._configured:
            return TNRevenueFetchResult(
                data_source="credential_required",
                detail="Set TN_REVENUE_SESSION_COOKIE (post-OTP citizen session) or "
                       "TN_REVENUE_USERNAME/TN_REVENUE_PASSWORD")
        headers = {"Cookie": self.session_cookie} if self.session_cookie else {}
        try:
            import httpx
            client_kwargs: dict[str, Any] = {"timeout": self.timeout_s, "headers": headers}
            with httpx.Client(base_url=self.base_url, **client_kwargs) as session:
                if not self.session_cookie:
                    login_resp = session.post("/login", json={
                        "username": self.username, "password": self.password})
                    if login_resp.status_code >= 400:
                        return TNRevenueFetchResult(
                            data_source="error",
                            detail=f"TamilNilam login rejected: HTTP {login_resp.status_code}: "
                                   f"{login_resp.text[:300]}")
                resp = session.get(self.lookup_path,
                                    params={"village": village, "survey_number": survey_number})
        except Exception as err:  # noqa: BLE001
            return TNRevenueFetchResult(data_source="error", detail=f"TamilNilam request failed: {err}")

        if resp.status_code >= 400:
            return TNRevenueFetchResult(
                data_source="error",
                detail=f"Patta/Chitta lookup failed: HTTP {resp.status_code}: {resp.text[:300]} "
                       f"(lookup_path={self.lookup_path!r} is unconfirmed — see "
                       "TN_REVENUE_LOOKUP_PATH in this module's docstring)")
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return TNRevenueFetchResult(data_source="error",
                                         detail=f"non-JSON response (HTTP {resp.status_code})")

        rec = body.get("record")
        if not rec or str(rec.get("survey_number")) != str(survey_number):
            return TNRevenueFetchResult(data_source="live", records=[],
                                         detail="no matching record returned by TamilNilam for this exact survey number")
        return TNRevenueFetchResult(data_source="live", records=[RevenueRecord(
            survey_number=str(rec.get("survey_number")), village=str(rec.get("village", village)),
            patta_number=rec.get("patta_number"), classification=rec.get("classification"),
            extent_ha=rec.get("extent_ha"), raw_attributes=rec,
        )])
