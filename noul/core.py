"""Facade over the zero-dependency `noul_core` plus orchestration of the Phase 1 tools.

`noul_tools` can load this module as its core (`NOUL_APP_DIR=<repo>`), so it re-exports the full
`noul_core` API that the tool layer relies on."""
from noul_core import *  # noqa: F401,F403
from noul_core import (  # noqa: F401
    DEFAULT_SOURCES,
    EV_W,
    SOURCES,
    HTTPError,
    __version__,
    _norm_doi,
    _paper_id,
    _validate_public_url,
    cache_get,
    cache_set,
    citation_graph,
    classify_evidence,
    download_paper_pdf,
    export,
    fulltext,
    get_config,
    pick_chunks,
    related_papers,
    resolve_paper,
    search,
    set_config,
)
