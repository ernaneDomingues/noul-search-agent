"""Data contracts (pydantic v2 when available, stdlib fallback otherwise)."""
from noul.schemas._compat import PYDANTIC_AVAILABLE
from noul.schemas.analysis import (
    BooleanQuerySuggestion,
    ComparativeMatrixResult,
    ComparisonDimension,
    ThematicInsightsResult,
)
from noul.schemas.domain import ReviewVerdict, ScientificDomain
from noul.schemas.paper import ArtifactStatus, PaperReference, UnifiedPaper

__all__ = [
    "PYDANTIC_AVAILABLE", "ScientificDomain", "ReviewVerdict", "ArtifactStatus", "PaperReference", "UnifiedPaper",
    "ComparisonDimension", "ComparativeMatrixResult", "ThematicInsightsResult", "BooleanQuerySuggestion",
]
