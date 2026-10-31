"""Semantic Scholar Graph API: backward references and forward citations with the `isInfluential` flag (#03)."""

import urllib.parse

GRAPH = "https://api.semanticscholar.org/graph/v1/paper"
REF_FIELDS = "isInfluential,paperId,title,authors,year,venue,citationCount,externalIds"
FETCH_LIMIT = 100  # fetch a wide page, sort locally, then cut to the caller's limit
_KEYS = {"references": "citedPaper", "citations": "citingPaper"}


def _core():
    import noul_core

    return noul_core


def paper_identifier(paper):
    """S2 lookup key for a paper record: s2 id, DOI:, PMID: or ARXIV: (None when it has none)."""
    if paper.get("s2_id"):
        return str(paper["s2_id"])
    if paper.get("doi"):
        return f"DOI:{paper['doi']}"
    if paper.get("pmid"):
        return f"PMID:{paper['pmid']}"
    if paper.get("arxiv_id"):
        return f"ARXIV:{paper['arxiv_id']}"
    return None


def fetch_edges(identifier, kind, limit=FETCH_LIMIT, timeout=15):
    """kind: "references" (backward) or "citations" (forward) -> [(parsed_paper, is_influential)].
    Network/HTTP errors (e.g. 429) propagate so the caller can fall back to OpenAlex."""
    core = _core()
    url = f"{GRAPH}/{urllib.parse.quote(identifier, safe=':/')}/{kind}?" + urllib.parse.urlencode(
        {"fields": REF_FIELDS, "limit": int(limit)}
    )
    data = core._http_json(url, timeout=timeout)
    out = []
    for edge in data.get("data") or []:
        p = core._parse_s2_paper((edge or {}).get(_KEYS[kind]))
        if p:
            out.append((p, bool(edge.get("isInfluential"))))
    return out
