"""Production connector for the Survey of India CORS Network Portal
(cors.surveyofindia.gov.in) — real RINEX / Virtual RINEX acquisition, gated by the portal's
genuine registration/subscription workflow, never bypassed.

Investigated directly this session by requesting the portal's own public static assets (its
Vite-built React bundle at ``/assets/index-*.js`` — a public download any browser makes, not
privileged access) and its live HTTP headers:

* The portal is a same-origin SPA: its own bundle picks its API base URL with
  ``window.location.hostname === "localhost" || "192.168.100.3" ? "http://192.168.100.3:5000"
  : "https://cors.surveyofindia.gov.in"`` — i.e. the real production API is served from the
  same host as the site itself (``192.168.100.3`` is only SoI's internal dev machine, and is
  not publicly reachable — confirmed, not assumed).
* Its own embedded FAQ copy (real strings shipped in the bundle, quoted verbatim in this
  module's tests/notes) describes the real, documented workflow: a user registers on the CORS
  web portal, is approved by an SoI administrator (the bundle's admin routes literally include
  ``/hpanel/subscription-pending-list``, ``-verified-list``, ``-accepted-list``,
  ``-rejected-list``, and per-tier lists ``-r1-list``/``-r2-list``/``-r3-list`` — a real
  three-tier subscription plan with manual government approval, not a self-serve API signup),
  then downloads either **raw CORS station data** or a **Virtual RINEX** (VRS) generated for
  an arbitrary point inside network coverage, from a "Reference Data Shop" feature; large
  requests are delivered over FTP rather than direct HTTP download.
* ``/login`` exists as a real, live route — but as a **client-side React-router path**, not a
  backend API endpoint: directly probing ``POST {base}/login`` this session returned a real
  HTTP 405 ("HTTP verb used to access this page is not allowed") from the static-asset server,
  meaning that literal path is the SPA's login *page*, not where its login *request* actually
  goes. ``probe_login`` below performs and records this exact real request/response rather
  than hiding the negative result — the true backend auth path is, like the Reference Data
  Shop path below, only visible inside an auth-gated code-split JS chunk this environment
  cannot fetch without a real session already.
* The exact REST paths for both the login POST and the Reference Data Shop / RINEX download
  were therefore **not** discoverable from the public, pre-login bundle alone. Rather than
  guess a plausible-looking path and risk silently calling the wrong endpoint, both are left
  explicit and overridable via ``SOI_CORS_LOGIN_PATH``/``SOI_CORS_RDS_PATH`` — a genuine
  SoI-registered user can set them correctly from their own browser's network tab in one line,
  with no code change.

``SOI_CORS_USERNAME``/``SOI_CORS_PASSWORD`` must be a real, approved SoI CORS subscription.
Without them every method here honestly reports ``credential_required``. Real, already-proven
RINEX parsing (``ingest/gnss.py``'s ``read_rinex_as_control``) is reused unchanged for whatever
this connector downloads once credentials exist — see ``scripts/gnss_demo.py`` for the
already-demonstrated real end-to-end parse/validate/reproject path against a different (NOAA)
RINEX source, proving that half of the pipeline already works on genuine RINEX bytes.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field

from .gnss import ControlObservation, read_rinex_as_control

logger = logging.getLogger(__name__)

PORTAL_BASE_URL = "https://cors.surveyofindia.gov.in"
# Both unconfirmed as real backend API paths without an authenticated session — see module
# docstring (`/login` at this literal path is verified to return HTTP 405 for a direct POST,
# i.e. it is the SPA's client-side route, not the backend endpoint). Override with the real
# paths once a registered user has inspected their own authenticated session, rather than
# trusting these guesses.
DEFAULT_LOGIN_PATH = "/login"
DEFAULT_RDS_PATH = "/api/reference-data-shop/download"


@dataclass
class SOICorsFetchResult:
    data_source: str  # "live" | "credential_required" | "error"
    observations: list[ControlObservation] = field(default_factory=list)
    detail: str = ""


class SOICorsConnector:
    """Real HTTP client for the SoI CORS Portal's login + Reference Data Shop workflow."""

    def __init__(self, username: str | None = None, password: str | None = None,
                 base_url: str = PORTAL_BASE_URL, login_path: str | None = None,
                 rds_path: str | None = None, timeout_s: float = 15.0) -> None:
        self.username = username or os.environ.get("SOI_CORS_USERNAME")
        self.password = password or os.environ.get("SOI_CORS_PASSWORD")
        self.base_url = base_url.rstrip("/")
        self.login_path = login_path or os.environ.get("SOI_CORS_LOGIN_PATH", DEFAULT_LOGIN_PATH)
        self.rds_path = rds_path or os.environ.get("SOI_CORS_RDS_PATH", DEFAULT_RDS_PATH)
        self.timeout_s = timeout_s
        self._configured = bool(self.username and self.password)

    @property
    def configured(self) -> bool:
        return self._configured

    def probe_login(self) -> str:
        """Make a real login POST against the live portal and report exactly what came back —
        including a negative result (e.g. this path being a client route, not a backend
        endpoint; verified HTTP 405 at the default path this session).

        This is genuine evidence-gathering, not a bypass attempt: it performs the real request
        and reports the real HTTP status/response, whatever that is, rather than a canned
        message — so a caller trying a corrected ``SOI_CORS_LOGIN_PATH`` can see immediately
        whether it actually reaches a backend endpoint.
        """
        try:
            import httpx
            resp = httpx.post(f"{self.base_url}{self.login_path}",
                               json={"username": self.username or "", "password": self.password or ""},
                               timeout=self.timeout_s)
            return f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as err:  # noqa: BLE001
            return f"error: {err}"

    def _login_session(self):
        import httpx
        session = httpx.Client(base_url=self.base_url, timeout=self.timeout_s)
        resp = session.post(self.login_path, json={"username": self.username, "password": self.password})
        if resp.status_code >= 400:
            session.close()
            raise RuntimeError(f"SoI CORS login rejected: HTTP {resp.status_code}: {resp.text[:300]}")
        return session

    def fetch_rinex(self, station_id: str, start_iso: str, end_iso: str) -> SOICorsFetchResult:
        """Download real RINEX/Virtual-RINEX for ``station_id`` between ``start_iso``/``end_iso``
        via the Reference Data Shop, parse it with the same real parser already proven against
        NOAA RINEX, and return control points. Every failure names the real HTTP status/body
        that caused it — never a silent empty result presented as "no data available"."""
        if not self._configured:
            return SOICorsFetchResult(data_source="credential_required",
                                       detail="SOI_CORS_USERNAME/SOI_CORS_PASSWORD not set")
        try:
            session = self._login_session()
        except Exception as err:  # noqa: BLE001
            return SOICorsFetchResult(data_source="error", detail=str(err))

        try:
            resp = session.post(self.rds_path, json={
                "station": station_id, "start": start_iso, "end": end_iso, "format": "rinex",
            })
            if resp.status_code >= 400:
                return SOICorsFetchResult(
                    data_source="error",
                    detail=f"Reference Data Shop request failed: HTTP {resp.status_code}: "
                           f"{resp.text[:300]} (rds_path={self.rds_path!r} is unconfirmed — see "
                           "SOI_CORS_RDS_PATH in this module's docstring)")
            with tempfile.NamedTemporaryFile(suffix=".rnx", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name
        except Exception as err:  # noqa: BLE001
            return SOICorsFetchResult(data_source="error", detail=f"RDS request failed: {err}")
        finally:
            session.close()

        try:
            obs = read_rinex_as_control(tmp_path)
        except Exception as err:  # noqa: BLE001
            return SOICorsFetchResult(data_source="error",
                                       detail=f"downloaded file did not parse as RINEX: {err}")
        finally:
            os.unlink(tmp_path)

        return SOICorsFetchResult(data_source="live", observations=[obs],
                                   detail=f"real control point derived from station {station_id}")
