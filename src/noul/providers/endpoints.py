"""Base URLs of the scholarly APIs and the lookups several modules build the same way."""

import urllib.parse

from noul.net import http as _nhttp

OPENALEX_WORKS = "https://api.openalex.org/works"
S2_PAPER = "https://api.semanticscholar.org/graph/v1/paper"
S2_RECOMMENDATIONS = "https://api.semanticscholar.org/recommendations/v1/papers/forpaper"
EUROPEPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def openalex_work(*, doi=None, openalex_id=None, pmid=None):
    """URL of one OpenAlex work by DOI, OpenAlex id or PMID (the first given), with the polite-pool e-mail."""
    if doi:
        key = "https://doi.org/" + urllib.parse.quote(doi, safe="")
    elif openalex_id:
        key = urllib.parse.quote(openalex_id, safe="")
    else:
        key = "pmid:" + urllib.parse.quote(str(pmid), safe="")
    return f"{OPENALEX_WORKS}/{key}{_nhttp._mailto_qs()}"


def s2_paper(identifier, fields, safe=""):
    """URL of one Semantic Scholar paper (`identifier` is an S2 id or a prefixed id such as `ARXIV:2410.1`)."""
    return f"{S2_PAPER}/{urllib.parse.quote(identifier, safe=safe)}?fields={fields}"
