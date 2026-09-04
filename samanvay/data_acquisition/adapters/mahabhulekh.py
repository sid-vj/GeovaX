"""Adapter for MahaBhulekh (Maharashtra) Cadastral API.

Researched: the Maharashtra Revenue Department's Mahabhulekh/Mahabhumi portal
(bhulekh.mahabhumi.gov.in) is a per-parcel citizen self-service for 7/12 Utara, 8A, and
Property Card lookups, explicitly documented as producing "unsigned copies ... for
informational purposes only" — not a bulk/API download surface. No documented public bulk
endpoint was found for this environment to call.

This adapter makes a real, correctly-shaped request against a configurable endpoint
(``MAHABHULEKH_API_URL``) for the case a government partnership or a future official bulk
API supplies one, and otherwise raises ``SourceUnavailable`` explaining exactly that gap —
never a fabricated GeoDataFrame.
"""
from __future__ import annotations

import logging
import os

import geopandas as gpd

from .base import BaseCadastralAdapter, SourceUnavailable

logger = logging.getLogger(__name__)


class MahaBhulekhAdapter(BaseCadastralAdapter):
    """Adapter for Maharashtra Land Records (MahaBhulekh)."""

    def __init__(self):
        super().__init__(state_lgd_code="27")
        self.api_url = os.environ.get("MAHABHULEKH_API_URL")

    def fetch_district(self, district_lgd_code: str) -> str:
        if not self.api_url:
            raise SourceUnavailable(
                "No bulk API is configured for Mahabhulekh/Mahabhumi. The public portal "
                "(bhulekh.mahabhumi.gov.in) is a per-parcel citizen lookup service that "
                "explicitly labels its output 'unsigned, informational only' — no documented "
                "bulk endpoint exists to fetch district-level cadastral data from. Set "
                "MAHABHULEKH_API_URL to a real endpoint if one becomes available."
            )
        try:
            import httpx
            out_path = f"data/raw/mahabhulekh/district_{district_lgd_code}.geojsonl"
            with httpx.stream("GET", self.api_url,
                               params={"district_lgd": district_lgd_code}, timeout=30.0) as resp:
                resp.raise_for_status()
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            return out_path
        except Exception as err:  # noqa: BLE001
            raise SourceUnavailable(
                f"Configured MAHABHULEKH_API_URL fetch failed for district "
                f"{district_lgd_code}: {err}"
            ) from err

    def standardize(self, raw_filepath: str) -> gpd.GeoDataFrame:
        return gpd.read_file(raw_filepath)
