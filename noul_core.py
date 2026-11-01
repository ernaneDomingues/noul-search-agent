"""Deprecated compatibility shim: `noul_core` now lives in the `noul` package (noul.search, noul.net, noul.library, ...). Removed in 2.0.0."""

import importlib
import sys
import warnings
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if (_SRC / "noul").is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))  # source checkout without an install

_HINT = (
    "noul-search-agent: missing dependency '{}'. Install uv (https://docs.astral.sh/uv/) and run "
    "`uv run --project <path-to-noul-search-agent> noul-install --all` again (or `pip install noul-search-agent`)."
)


def _load(*modules):
    loaded = []
    for name in modules:
        try:
            loaded.append(importlib.import_module(name))
        except ModuleNotFoundError as exc:
            if exc.name and not exc.name.startswith("noul"):
                raise ImportError(_HINT.format(exc.name)) from exc
            raise
    return loaded


warnings.warn(
    "`noul_core` is deprecated; use the `noul` package (noul.search, noul.net, noul.library, ...).",
    DeprecationWarning,
    stacklevel=2,
)

_MODULES = _load(
    "noul.config",
    "noul.errors",
    "noul.evidence.classify",
    "noul.evidence.impact",
    "noul.ids",
    "noul.library.downloads",
    "noul.library.export",
    "noul.library.fulltext",
    "noul.library.graph",
    "noul.net.cache",
    "noul.net.http",
    "noul.providers.arxiv_search",
    "noul.providers.crossref",
    "noul.providers.europepmc",
    "noul.providers.openalex",
    "noul.providers.pubmed",
    "noul.providers.s2",
    "noul.search.engine",
)
_EXPORTS = [
    "ARXIV_RE",
    "DEFAULT_SOURCES",
    "DOI_RE",
    "EMAIL_RE",
    "EV_W",
    "EXACT_ELITE_VENUES",
    "HTTPError",
    "MAX_DOWNLOAD_BYTES",
    "MIN_HTML_FULLTEXT_CHARS",
    "NEG_PATTERNS",
    "S2_PAPER_FIELDS",
    "SEARCH_TIMEOUT",
    "SOURCES",
    "UA",
    "_EMPIRICAL_RE",
    "_HTML_BOILERPLATE_RE",
    "_META_RE",
    "_OBSERVATIONAL_RE",
    "_PRECLINICAL_RE",
    "_PROVIDERS",
    "_PUBLIC_OPENER",
    "_PublicRedirectHandler",
    "_RCT_RE",
    "_REVIEW_RE",
    "_SR_RE",
    "_STRONG_DESIGNS",
    "_annotate_paper",
    "_cache_dir",
    "_clean_text",
    "_config_file",
    "_conn",
    "_db_path",
    "_email",
    "_ensure_openalex_work",
    "_estimate_impact",
    "_europepmc_pdf_url",
    "_eutils_params",
    "_extract_pdf_text",
    "_fetch_openalex_batch",
    "_html_to_text",
    "_http_get",
    "_http_json",
    "_ip_is_public",
    "_load_config_file",
    "_mailto_qs",
    "_merge_papers",
    "_norm_doi",
    "_norm_venue",
    "_paper_id",
    "_parse_europepmc_result",
    "_parse_openalex_work",
    "_parse_pubmed_xml",
    "_parse_s2_paper",
    "_provider",
    "_reconstruct_openalex_abstract",
    "_resolve_email",
    "_safe_filename",
    "_score_paper",
    "_search_arxiv",
    "_search_crossref",
    "_search_europepmc",
    "_search_openalex",
    "_search_pubmed",
    "_search_s2",
    "_unique",
    "_user_agent",
    "_valid_email",
    "_validate_public_url",
    "_with_mailto",
    "cache_get",
    "cache_set",
    "citation_graph",
    "classify_evidence",
    "download_paper_pdf",
    "export",
    "fulltext",
    "get_config",
    "related_papers",
    "resolve_paper",
    "resolve_pdf_url",
    "search",
    "set_config",
]
globals().update({k: getattr(m, k) for m in _MODULES for k in _EXPORTS if hasattr(m, k)})
globals()["__version__"] = importlib.import_module("noul").__version__
