"""Result models of the tools whose output is a dump of a Pydantic model (advertised as MCP `outputSchema`).

The analytic tools (review, claims, failure modes, draft audit, rebuttal, submission readiness) follow the
evidence-vs-judgement contract: subclasses of `noul.schemas.evidence.AnalysisResult`."""

from noul.schemas.authoring import (
    BibtexSanitizeReport,
    CitationInfillerReport,
    DraftAuditReport,
    ManuscriptBlueprint,
    RebuttalReport,
    RelatedWorkSynthesis,
    SubmissionGatekeeperReport,
)
from noul.schemas.code import CodeScaffold
from noul.schemas.evidence import AnalysisResult
from noul.schemas.fulltext import FulltextResult, QueryFulltextsResult
from noul.schemas.project import (
    ProjectCreated,
    ProjectDetail,
    ProjectList,
    ProjectNoteAdded,
    ProjectPapersChange,
    ProjectStatusChange,
)
from noul.schemas.review import PeerReviewReport
from noul.schemas.technical import ArxivVersionDiffReport, ClaimsGroundingReport, FailureModesReport

__all__ = [
    "AnalysisResult",
    "ArxivVersionDiffReport",
    "BibtexSanitizeReport",
    "CitationInfillerReport",
    "ClaimsGroundingReport",
    "CodeScaffold",
    "DraftAuditReport",
    "FailureModesReport",
    "FulltextResult",
    "ManuscriptBlueprint",
    "PeerReviewReport",
    "ProjectCreated",
    "ProjectDetail",
    "ProjectList",
    "ProjectNoteAdded",
    "ProjectPapersChange",
    "ProjectStatusChange",
    "QueryFulltextsResult",
    "RebuttalReport",
    "RelatedWorkSynthesis",
    "SubmissionGatekeeperReport",
]
