"""Data contracts (Pydantic v2 models)."""

from noul.schemas.analysis import (
    BooleanQuerySuggestion,
    ComparativeMatrixResult,
    ComparisonDimension,
    ResearchGapReport,
    ResearchHypothesis,
    ThematicInsightsResult,
)
from noul.schemas.authoring import (
    BibtexEntryUpdate,
    BibtexSanitizeReport,
    BlueprintSection,
    CitationInfillerReport,
    CitationRecommendationItem,
    DraftAuditFinding,
    DraftAuditReport,
    GatekeeperCheckItem,
    ManuscriptBlueprint,
    RebuttalPoint,
    RebuttalReport,
    RelatedWorkSynthesis,
    RelatedWorkThemeCluster,
    SubmissionGatekeeperReport,
)
from noul.schemas.code import CodeScaffold
from noul.schemas.domain import ScientificDomain
from noul.schemas.hardware import HardwareFeasibility, PrecisionMemory
from noul.schemas.paper import ArtifactStatus, PaperReference, UnifiedPaper
from noul.schemas.review import PeerReviewReport, ReviewPerspective
from noul.schemas.technical import (
    AblationEntry,
    ArxivVersionDiffReport,
    BenchmarkTable,
    FailureModesReport,
    GroundedClaim,
    MathItem,
    PromptExtractionResult,
    VersionChangeItem,
)

__all__ = [
    "ScientificDomain",
    "ArtifactStatus",
    "PaperReference",
    "UnifiedPaper",
    "ComparisonDimension",
    "ComparativeMatrixResult",
    "ThematicInsightsResult",
    "BooleanQuerySuggestion",
    "ResearchHypothesis",
    "ResearchGapReport",
    "ReviewPerspective",
    "PeerReviewReport",
    "CodeScaffold",
    "PrecisionMemory",
    "HardwareFeasibility",
    "BenchmarkTable",
    "GroundedClaim",
    "MathItem",
    "AblationEntry",
    "PromptExtractionResult",
    "FailureModesReport",
    "VersionChangeItem",
    "ArxivVersionDiffReport",
    "CitationRecommendationItem",
    "CitationInfillerReport",
    "BlueprintSection",
    "ManuscriptBlueprint",
    "DraftAuditFinding",
    "DraftAuditReport",
    "RelatedWorkThemeCluster",
    "RelatedWorkSynthesis",
    "BibtexEntryUpdate",
    "BibtexSanitizeReport",
    "RebuttalPoint",
    "RebuttalReport",
    "GatekeeperCheckItem",
    "SubmissionGatekeeperReport",
]
