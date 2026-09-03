"""Base adapter for State Cadastral APIs."""
from abc import ABC, abstractmethod
from typing import Iterator
import geopandas as gpd

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
        """
        pass
        
    @abstractmethod
    def standardize(self, raw_filepath: str) -> gpd.GeoDataFrame:
        """Standardize the raw state data into the SAMANVAY canonical schema.
        
        Args:
            raw_filepath: Path to the raw downloaded file.
            
        Returns:
            GeoDataFrame conforming to the SAMANVAY canonical parcel schema.
        """
        pass
