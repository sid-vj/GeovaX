"""Base adapter for State Cadastral APIs."""
from abc import ABC, abstractmethod

import geopandas as gpd


class SourceUnavailable(Exception):
    """Raised when a state cadastral portal has no reachable bulk-download path.

    This is a real, informative failure — it names what was actually checked and why it
    didn't work — never silently swallowed into an empty result and never papered over with
    fabricated data. Callers should surface ``str(exc)`` to the operator/API response.
    """


class BaseCadastralAdapter(ABC):
    """Abstract base class for fetching and standardizing state cadastral data."""

    def __init__(self, state_lgd_code: str):
        self.state_lgd_code = state_lgd_code

    @abstractmethod
    def fetch_district(self, district_lgd_code: str) -> str:
        """Fetch cadastral data for a specific district.

        Args:
            district_lgd_code: LGD code for the district.

        Returns:
            Path to the downloaded raw file (GeoJSON, Shapefile, etc.).

        Raises:
            SourceUnavailable: if the state portal has no reachable bulk path for this
                district (the common case for both adapters currently implemented — see
                their docstrings for what was actually checked).
        """

    @abstractmethod
    def standardize(self, raw_filepath: str) -> gpd.GeoDataFrame:
        """Standardize the raw state data into the SAMANVAY canonical schema.

        Args:
            raw_filepath: Path to the raw downloaded file.

        Returns:
            GeoDataFrame conforming to the SAMANVAY canonical parcel schema.
        """
