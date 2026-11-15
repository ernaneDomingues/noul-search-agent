"""Enrichment every provider applies to a parsed paper: scientific domain and offline artifact flags.

It lives outside `noul.search.engine` so providers do not import the engine that imports them."""

_PROVIDERS: dict = {}


def _provider(name):
    """Lazily import an optional `noul.providers.<name>` module (None when the package is unavailable,
    e.g. a stripped-down deployment)."""
    if name not in _PROVIDERS:
        try:
            import importlib

            _PROVIDERS[name] = importlib.import_module(f"noul.providers.{name}")
        except Exception:
            _PROVIDERS[name] = None
    return _PROVIDERS[name]


def annotate_paper(p):
    """Attach the scientific domain (#19) and offline artifact flags (#02). Enrichment must never break a search."""
    dc = _provider("domain_classifier")
    if dc is not None:
        try:
            p["domain"] = dc.resolve_domain(paper=p).value
        except Exception:
            pass
    radar = _provider("artifact_radar")
    if radar is not None:
        try:
            radar.scan_artifacts_from_paper(p, verify_http=False)
        except Exception:
            pass
    return p
