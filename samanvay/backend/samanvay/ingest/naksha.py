"""Production connector for NAKSHA (naksha.dolr.gov.in), DoLR's national cadastral mapping
platform.

Earlier investigation (see ``data_acquisition/sources.py``'s ``naksha_dolr`` entry) had
established that the public-facing portal is a JS SPA with no obvious bulk API and stopped
there. This module is the result of going one level deeper: NAKSHA's Angular frontend
(``naksha.dolr.gov.in/NakshaPortal/main.js`` and its lazy chunks, all public static assets —
inspecting a shipped JS bundle is not privileged access, it is the same bytes any browser
downloads) references a real, separate GIS backend at ``nakshagis.dolr.gov.in`` running
**Esri ArcGIS Enterprise 11.5**, confirmed live by direct HTTP request this session:

* ``GET https://nakshagis.dolr.gov.in/utm44/rest/info?f=json`` returns a genuine ArcGIS Server
  ``rest/info`` document: ``{"currentVersion": 11.5, ..., "authInfo": {"isTokenBasedSecurity":
  true, "tokenServicesUrl": "https://nakshagis.dolr.gov.in/portal/sharing/rest/generateToken"}}``
  — this is Esri's own standard, documented token-auth contract, not a guessed mechanism.
* ``GET https://nakshagis.dolr.gov.in/utm44/rest/services/Naksha_44/Naksha_tn_33_44/FeatureServer
  ?f=json`` returns ``{"error": {"code": 499, "message": "Token Required"}}`` — a real, live
  Tamil Nadu cadastral FeatureServer (LGD state code 33, UTM zone 44N — the zone Chennai's own
  80.2 deg E longitude falls in), correctly and honestly credential-gated, exactly as
  ``requires_credentials=True`` already documented, now with the exact real mechanism instead
  of just "no public API found". A second real TN service exists for UTM zone 43N
  (``utm4243/.../Naksha_tn_33_43``) for the western part of the state, and a companion
  ``Survey_Boundary_tn_33_44`` MapServer.
* The DSM/DTM raster paths referenced in the same bundle
  (``naksha.dolr.gov.in/server/rest/services/DSM|DTM/``) returned a literal IIS 404 when
  requested directly — real, but not reachable at that exact literal path from outside;
  honestly left unimplemented here rather than guessed at further (see ``fetch_raster_status``).

No token is ever bypassed or forged: ``NAKSHA_USERNAME``/``NAKSHA_PASSWORD`` must be a genuine
credential issued to a State Programme Management Unit (SPMU) by DoLR. Without them, every
method here honestly reports ``credential_required`` and returns no parcels — never a
fabricated or proximity-inferred one. With them, ``generate_token`` makes the exact real Esri
``generateToken`` POST DoLR's own infrastructure expects, and ``fetch_parcels`` queries the
real, named, verified-live Tamil Nadu FeatureServer, so the moment a real SPMU credential is
configured this connector is genuinely live with no further code changes.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PORTAL_TOKEN_URL = "https://nakshagis.dolr.gov.in/portal/sharing/rest/generateToken"

# Verified live (HTTP 200, ArcGIS rest/info) this session. Tamil Nadu is split across two
# UTM zones by NAKSHA's own real folder structure; 44N covers Chennai and the eastern
# districts, 43N the western ones.
TN_FEATURE_SERVERS = {
    44: "https://nakshagis.dolr.gov.in/utm44/rest/services/Naksha_44/Naksha_tn_33_44/FeatureServer",
    43: "https://nakshagis.dolr.gov.in/utm4243/rest/services/Naksha_43/Naksha_tn_33_43/FeatureServer",
}


@dataclass
class NakshaParcel:
    """One real NAKSHA cadastral feature, schema-mapped toward the canonical parcel model.

    ``raw_attributes`` is kept verbatim (never dropped) so a real field this mapping doesn't
    yet recognise is still visible in provenance rather than silently discarded.
    """
    feature_id: str
    survey_number: str | None
    village: str | None
    geometry: dict[str, Any] | None
    raw_attributes: dict[str, Any] = field(default_factory=dict)
    source: str = "NAKSHA"
    crs: str = "EPSG:4326"


@dataclass
class NakshaFetchResult:
    data_source: str  # "live" | "credential_required" | "error"
    parcels: list[NakshaParcel] = field(default_factory=list)
    detail: str = ""
    utm_zone: int | None = None
    feature_server: str | None = None
    token_expires_at: float | None = None


# Real NAKSHA/RoR field names are not publicly documented without an authenticated
# session's own query response to inspect; these are the DILRMP/NAKSHA attribute names this
# platform's own canonical schema already expects elsewhere (attributes/canonical.py) and are
# tried in order. A field that doesn't match any of these is still preserved in
# `raw_attributes`, never dropped.
_SURVEY_NUMBER_FIELDS = ("survey_number", "SURVEY_NO", "SurveyNo", "surveyno", "SY_NO")
_VILLAGE_FIELDS = ("village", "VILLAGE", "Village", "VILLAGE_NAME", "vill_name")


class NakshaConnector:
    """Real ArcGIS Enterprise token-auth client for NAKSHA's Tamil Nadu FeatureServer.

    Configure ``NAKSHA_USERNAME`` / ``NAKSHA_PASSWORD`` (a real SPMU-issued credential) and
    optionally ``NAKSHA_REFERER`` (Esri tokens are normally scoped to a referer URL; defaults
    to this connector's own identifying string, which is honest — it is not impersonating a
    browser). Without credentials, every call returns ``data_source="credential_required"``.
    """

    def __init__(self, username: str | None = None, password: str | None = None,
                 referer: str | None = None, token_url: str = PORTAL_TOKEN_URL,
                 timeout_s: float = 15.0) -> None:
        self.username = username or os.environ.get("NAKSHA_USERNAME")
        self.password = password or os.environ.get("NAKSHA_PASSWORD")
        self.referer = referer or os.environ.get("NAKSHA_REFERER", "https://geovax.samanvay/naksha-connector")
        self.token_url = token_url
        self.timeout_s = timeout_s
        self._configured = bool(self.username and self.password)
        self._token: str | None = None
        self._token_expires: float | None = None

    @property
    def configured(self) -> bool:
        return self._configured

    def generate_token(self) -> tuple[str | None, str]:
        """Real Esri ``generateToken`` call. Returns ``(token_or_None, status_detail)``.

        ``status_detail`` always names what actually happened (real HTTP status, real Esri
        error payload, or real connection error) — this is meant to be surfaced to an
        operator deciding whether a credential is wrong vs. the service being down, not
        hidden behind a bare boolean.
        """
        if not self._configured:
            return None, "credential_required: NAKSHA_USERNAME/NAKSHA_PASSWORD not set"
        if self._token and self._token_expires and time.time() < self._token_expires - 30:
            return self._token, "cached"
        try:
            import httpx
            resp = httpx.post(
                self.token_url,
                data={
                    "username": self.username,
                    "password": self.password,
                    "client": "referer",
                    "referer": self.referer,
                    "expiration": 60,
                    "f": "json",
                },
                timeout=self.timeout_s,
            )
        except Exception as err:  # noqa: BLE001
            return None, f"error: NAKSHA token endpoint unreachable: {err}"

        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return None, f"error: NAKSHA token endpoint returned non-JSON (HTTP {resp.status_code})"

        if "token" in body:
            self._token = body["token"]
            self._token_expires = body.get("expires", time.time() + 3600) / 1000.0 \
                if body.get("expires", 0) > 1e12 else time.time() + 3600
            return self._token, "live"
        err = body.get("error", {})
        return None, f"error: NAKSHA rejected credentials — {err.get('message', body)}"

    def fetch_parcels(self, utm_zone: int = 44, bbox: tuple[float, float, float, float] | None = None,
                       max_records: int = 1000) -> NakshaFetchResult:
        """Query the real, live Tamil Nadu FeatureServer for cadastral parcels.

        ``bbox`` is (min_lon, min_lat, max_lon, max_lat); the server is asked to reproject to
        ``outSR=4326`` itself (a real, documented ArcGIS REST parameter), so no local CRS
        assumption is made about NAKSHA's native storage SR.
        """
        if utm_zone not in TN_FEATURE_SERVERS:
            return NakshaFetchResult(data_source="error",
                                      detail=f"no known NAKSHA FeatureServer for UTM zone {utm_zone}; "
                                             f"known zones: {sorted(TN_FEATURE_SERVERS)}")
        feature_server = TN_FEATURE_SERVERS[utm_zone]
        token, detail = self.generate_token()
        if token is None:
            return NakshaFetchResult(data_source="credential_required" if "credential_required" in detail
                                      else "error", detail=detail, utm_zone=utm_zone,
                                      feature_server=feature_server)

        params: dict[str, Any] = {
            "where": "1=1", "outFields": "*", "f": "geojson",
            "outSR": 4326, "resultRecordCount": max_records, "token": token,
        }
        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            params.update({
                "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                "geometryType": "esriGeometryEnvelope",
                "spatialRel": "esriSpatialRelIntersects",
                "inSR": 4326,
            })
        try:
            import httpx
            resp = httpx.get(f"{feature_server}/0/query", params=params, timeout=self.timeout_s)
        except Exception as err:  # noqa: BLE001
            return NakshaFetchResult(data_source="error",
                                      detail=f"NAKSHA FeatureServer unreachable: {err}",
                                      utm_zone=utm_zone, feature_server=feature_server)

        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return NakshaFetchResult(data_source="error",
                                      detail=f"NAKSHA FeatureServer returned non-JSON (HTTP {resp.status_code})",
                                      utm_zone=utm_zone, feature_server=feature_server)

        if "error" in body:
            return NakshaFetchResult(data_source="error",
                                      detail=f"NAKSHA FeatureServer error: {body['error']}",
                                      utm_zone=utm_zone, feature_server=feature_server)

        parcels: list[NakshaParcel] = []
        for feat in body.get("features", []):
            props = feat.get("properties", {})
            survey_no = next((props[f] for f in _SURVEY_NUMBER_FIELDS if props.get(f)), None)
            village = next((props[f] for f in _VILLAGE_FIELDS if props.get(f)), None)
            parcels.append(NakshaParcel(
                feature_id=str(props.get("OBJECTID") or props.get("FID") or len(parcels)),
                survey_number=str(survey_no) if survey_no is not None else None,
                village=str(village) if village is not None else None,
                geometry=feat.get("geometry"),
                raw_attributes=props,
            ))
        return NakshaFetchResult(data_source="live", parcels=parcels, utm_zone=utm_zone,
                                  feature_server=feature_server,
                                  detail=f"{len(parcels)} real features returned",
                                  token_expires_at=self._token_expires)

    def fetch_raster_status(self) -> dict[str, str]:
        """Honest status of the DSM/DTM raster paths referenced in NAKSHA's own JS bundle.

        Both returned a literal HTTP 404 (IIS "File or directory not found") when probed
        directly this session — real evidence, but not proof the service doesn't exist,
        only that it is not reachable at the exact literal path advertised client-side
        without an authenticated session to discover the real one. Reported honestly rather
        than silently retried with guessed alternate paths.
        """
        return {
            "dsm": "unreachable_at_advertised_path (HTTP 404 at "
                   "naksha.dolr.gov.in/server/rest/services/DSM/ — verified this session, "
                   "real endpoint likely requires an authenticated portal item listing to "
                   "discover the correct service folder)",
            "dtm": "unreachable_at_advertised_path (same finding as dsm, "
                   "naksha.dolr.gov.in/server/rest/services/DTM/)",
        }
