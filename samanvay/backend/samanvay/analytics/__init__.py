"""Advanced predictive analytics and legal risk engines."""
from .litigation import (
    calculate_litigation_risk,
    build_litigation_hotspots,
    ECourtsConnector,
    RegistrationConnector,
    LitigationAssessment,
)

__all__ = [
    "calculate_litigation_risk",
    "build_litigation_hotspots",
    "ECourtsConnector",
    "RegistrationConnector",
    "LitigationAssessment",
]
