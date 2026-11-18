"""Base URLs of the scholarly APIs and the lookups several modules build the same way."""

import re
import urllib.parse

from noul import errors as _errors
from noul.net import http as _nhttp

OPENALEX_WORKS = "https://api.openalex.org/works"
S2_PAPER = "https://api.semanticscholar.org/graph/v1/paper"
S2_RECOMMENDATIONS = "https://api.semanticscholar.org/recommendations/v1/papers/forpaper"
EUROPEPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def openalex_work(*, doi=None, openalex_id=None, pmid=None):
    """URL of one OpenAlex work by DOI, OpenAlex id or PMID (the first given), with the polite-pool e-mail."""
    if doi is not None:
        key = "https://doi.org/" + urllib.parse.quote(doi, safe="")
    elif openalex_id is not None:
        key = urllib.parse.quote(openalex_id, safe="")
    elif pmid is not None:
        key = "pmid:" + urllib.parse.quote(str(pmid), safe="")
    else:
        raise ValueError("openalex_work needs a doi, an openalex_id or a pmid")
    return f"{OPENALEX_WORKS}/{key}{_nhttp._mailto_qs()}"


# New-style (2401.01234) and old-style (hep-th/9901001, math.GT/0309136) arXiv ids, with an optional version.
_ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")


def s2_paper(identifier, fields, prefix="", safe=""):
    """URL of one Semantic Scholar paper: an S2 id, or `prefix="ARXIV:"` with an arXiv id.

    An arXiv id must have one of arXiv's two shapes (only the old shape keeps its one `/`): anything else could
    walk to another endpoint of the API with the operator's key."""
    if prefix == "ARXIV:":
        if not _ARXIV_ID.fullmatch(str(identifier)):
            raise _errors.HTTPError(400, f"Invalid arXiv id: {str(identifier)[:80]}")
        safe = "/"
    return f"{S2_PAPER}/{prefix}{urllib.parse.quote(identifier, safe=safe)}?fields={fields}"
