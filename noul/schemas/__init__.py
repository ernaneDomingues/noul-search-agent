"""Data contracts (pydantic v2 when available, stdlib fallback otherwise)."""
from noul.schemas._compat import PYDANTIC_AVAILABLE
from noul.schemas.analysis import (
    BooleanQuerySuggestion,
    ComparativeMatrixResult,
    ComparisonDimension,
    ThematicInsightsResult,
)
from noul.schemas.domain import ReviewVerdict, ScientificDomain
from noul.schemas.hardware import HardwareFeasibility, PrecisionMemory
from noul.schemas.paper import ArtifactStatus, PaperReference, UnifiedPaper
from noul.schemas.technical import (
    AblationEntry,
    ArxivVersionDiffReport,
    BenchmarkTable,
    FailureModeEntry,
    FailureModesReport,
    GroundedClaim,
    MathItem,
    PromptExtractionResult,
    VersionChangeItem,
)

__all__ = [
    "PYDANTIC_AVAILABLE", "ScientificDomain", "ReviewVerdict", "ArtifactStatus", "PaperReference", "UnifiedPaper",
    "ComparisonDimension", "ComparativeMatrixResult", "ThematicInsightsResult", "BooleanQuerySuggestion",
    "PrecisionMemory", "HardwareFeasibility", "BenchmarkTable", "GroundedClaim", "MathItem", "AblationEntry",
    "PromptExtractionResult", "FailureModeEntry", "FailureModesReport", "VersionChangeItem", "ArxivVersionDiffReport",
]
