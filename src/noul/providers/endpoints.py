"""Base URLs of the scholarly APIs and the lookups several modules build the same way."""

import urllib.parse

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


def s2_paper(identifier, fields, prefix="", safe=""):
    """URL of one Semantic Scholar paper: an S2 id, or `prefix="ARXIV:"` with an arXiv id (`safe="/."`)."""
    return f"{S2_PAPER}/{prefix}{urllib.parse.quote(identifier, safe=safe)}?fields={fields}"
