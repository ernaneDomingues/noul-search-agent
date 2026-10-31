"""Result models of the tools whose output is a dump of a Pydantic model (advertised as MCP `outputSchema`).

The other tools return plain dicts for now; they get output models with the evidence-vs-judgement contract
(v2 Phase 3)."""

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
from noul.schemas.technical import ArxivVersionDiffReport, FailureModesReport

__all__ = [
    "ArxivVersionDiffReport",
    "BibtexSanitizeReport",
    "CitationInfillerReport",
    "CodeScaffold",
    "DraftAuditReport",
    "FailureModesReport",
    "ManuscriptBlueprint",
    "RebuttalReport",
    "RelatedWorkSynthesis",
    "SubmissionGatekeeperReport",
]
