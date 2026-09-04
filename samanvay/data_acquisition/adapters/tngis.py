"""Adapter for TNGIS (Tamil Nadu) Cadastral API.

Directly probed: TNGIS runs a live public GeoServer 2.12.2 at
``tngis.tn.gov.in/geoserver/`` (workspaces include ``tngis``, ``tngis_basemap``,
``admin_master``), but every WFS endpoint on it — ``/geoserver/wfs``,
``/geoserver/tngis/wfs``, ``/geoserver/tngis/ows?service=WFS`` — returns "Service WFS is
disabled", a deliberate server-side policy rather than a missing feature, and its Downloads
page is gated behind a registration/login form with no files reachable unauthenticated.

This adapter performs that same real check at fetch time (not a cached assumption) and
raises ``SourceUnavailable`` with the specific server response when the bulk path is closed,
rather than silently returning a placeholder path. The real, currently-working route to
TNGIS-derived cadastral geometry is the community mirror in
``data_acquisition/sources.py`` (``tngis_cadastre``, tier="mirror") — this adapter exists so
that if TNGIS ever re-enables WFS or ships a documented bulk API, switching to the official
tier-1 path is a one-place change, not a rewrite.
"""
from __future__ import annotations

import logging

import geopandas as gpd

from .base import BaseCadastralAdapter, SourceUnavailable

logger = logging.getLogger(__name__)

WFS_PROBE_URL = "https://tngis.tn.gov.in/geoserver/tngis/ows"


class TNGISAdapter(BaseCadastralAdapter):
    """Adapter for Tamil Nadu Geographic Information System (TNeGA)."""

    def __init__(self):
        super().__init__(state_lgd_code="33")

    def fetch_district(self, district_lgd_code: str) -> str:
        try:
            import httpx
            resp = httpx.get(
                WFS_PROBE_URL,
                params={"service": "WFS", "version": "1.0.0", "request": "GetCapabilities"},
                timeout=10.0,
            )
            body = resp.text
        except Exception as err:  # noqa: BLE001
            raise SourceUnavailable(
                f"TNGIS GeoServer unreachable while checking WFS for district "
                f"{district_lgd_code}: {err}"
            ) from err

        if "disabled" in body.lower() or resp.status_code != 200:
            raise SourceUnavailable(
                f"TNGIS WFS is disabled at the server (verified live at {WFS_PROBE_URL}; "
                f"HTTP {resp.status_code}). TNGIS shares its cadastral layers with "
                "departments via Government Order, not a public bulk API. Use the "
                "'tngis_cadastre' community-mirror entry in data_acquisition/sources.py "
                "instead (tier='mirror', provenance gap documented there)."
            )

        # If TNGIS ever re-enables WFS, this is where a real GetFeature request for the
        # district would go. Left unimplemented rather than guessed at, since the feature
        # type names/schema for a live cadastral layer aren't known without WFS access.
        raise SourceUnavailable(
            "TNGIS WFS responded but district-level cadastral GetFeature is not yet "
            "implemented against a schema this adapter has never been able to inspect."
        )

    def standardize(self, raw_filepath: str) -> gpd.GeoDataFrame:
        return gpd.read_file(raw_filepath).rename(columns={"kide": "survey_number"})
