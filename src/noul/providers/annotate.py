"""Enrichment every provider applies to a parsed paper: scientific domain and offline artifact flags.

It lives outside `noul.search.engine` so providers do not import the engine that imports them."""

from noul import log as _log

_PROVIDERS: dict = {}


def _provider(name):
    """Lazily import an optional `noul.providers.<name>` module (None when the package is unavailable,
    e.g. a stripped-down deployment)."""
    if name not in _PROVIDERS:
        try:
            import importlib

            _PROVIDERS[name] = importlib.import_module(f"noul.providers.{name}")
        except ImportError:
            _PROVIDERS[name] = None
    return _PROVIDERS[name]


def annotate_paper(p):
    """Attach the scientific domain (#19) and offline artifact flags (#02). Enrichment must never break a search."""
    dc = _provider("domain_classifier")
    if dc is not None:
        try:
            p["domain"] = dc.resolve_domain(paper=p).value
        except Exception as e:  # noqa: BLE001 - a classifier failure must not fail the lookup
            _log.logger.debug("domain annotation failed: %s", type(e).__name__)
    radar = _provider("artifact_radar")
    if radar is not None:
        try:
            radar.scan_artifacts_from_paper(p, verify_http=False)
        except Exception as e:  # noqa: BLE001 - an artifact-scan failure must not fail the lookup
            _log.logger.debug("artifact annotation failed: %s", type(e).__name__)
    return p
