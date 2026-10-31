"""OpenAlex fallback for the citation graph (#03): reuses `noul.library.graph.citation_graph`.
OpenAlex has no influence flag, so a paper counts as influential with >= 50 citations."""

from noul.library import graph as _graph

INFLUENTIAL_CITATIONS = 50


def fetch_citation_graph(paper_id):
    """-> {"backward": [(paper, is_influential)], "forward": [...]}"""
    g = _graph.citation_graph(paper_id)

    def pairs(items):
        return [(p, int(p.get("citations") or 0) >= INFLUENTIAL_CITATIONS) for p in items or [] if p]

    return {"backward": pairs(g.get("references")), "forward": pairs(g.get("citing"))}
