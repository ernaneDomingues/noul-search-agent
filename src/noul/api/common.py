"""What the tool modules share: resolving a paper, the public projection of a paper record (`_slim`), integrity signals, the evidence vocabulary and access to the project store.

It registers no tool, so importing it from any tool module leaves the order of `tools/list` alone."""

import json
import sqlite3
import time

from noul import errors as _errors
from noul import ids as _ids
from noul import log as _log
from noul import workspace as _workspace
from noul.evidence import classify as _classify
from noul.evidence import contract as _contract
from noul.evidence import integrity as _integrity
from noul.evidence import untrusted as _untrusted
from noul.library import store as _store
from noul.net import cache as _cache
from noul.providers import domain_classifier as _domain_classifier
from noul.search import engine as _engine

EVIDENCE = list(_classify.EV_W)


STANCE_ALIASES = {
    "supportive": "supportive",
    "support": "supportive",
    "supports": "supportive",
    "yes": "supportive",
    "positive": "supportive",
    "favorable": "supportive",
    "favoravel": "supportive",
    "favorável": "supportive",
    "contrary": "contrary",
    "contradicts": "contrary",
    "against": "contrary",
    "no": "contrary",
    "negative": "contrary",
    "contrario": "contrary",
    "contrário": "contrary",
    "inconclusive": "inconclusive",
    "possibly": "inconclusive",
    "mixed": "inconclusive",
    "neutral": "inconclusive",
    "unclear": "inconclusive",
    "inconclusivo": "inconclusive",
}


def _with_integrity(result, paper_id):
    """Add `subject.retraction` and the integrity signals (first) to an analytic result about a paper."""
    try:
        paper = _integrity.enrich(_resolve(paper_id), versions=False)
    except _errors.HTTPError:
        return result  # the tool itself resolved the paper; a lookup failure here must not hide its result
    check_failed = paper.pop(_integrity.CHECK_FAILED, False)
    _cache.put_paper(paper["id"], paper)
    retraction, signals = _contract.integrity_signals(paper)
    if not signals and not check_failed:
        return result
    out = dict(result)
    if check_failed:
        out["limitations"] = [*(out.get("limitations") or []), _integrity.CHECK_FAILED_WARNING]
    if not signals:
        return out
    out["subject"] = {**(out.get("subject") or {}), **({"retraction": retraction} if retraction else {})}
    out["signals"] = [s.model_dump(mode="json") for s in signals] + list(out.get("signals") or [])
    if (retraction or {}).get("notice_doi"):
        out["untrusted_fields"] = [*(out.get("untrusted_fields") or []), "subject.retraction.notice_doi"]
    if out.get("rendered_markdown"):
        # The Markdown was rendered before these signals existed: show them as a banner above the report.
        lines = [f"> **{s.description}** {s.detail}" for s in signals]
        out["rendered_markdown"] = "\n".join(lines) + "\n\n" + out["rendered_markdown"]
    return out


def _slim(p, idx=None, abstract_chars=1500):
    out = {
        k: p.get(k)
        for k in (
            "id",
            "title",
            "authors",
            "year",
            "journal",
            "doi",
            "pmid",
            "arxiv_id",
            "citations",
            "evidence",
            "impact",
            "tier",
            "open_access",
            "pdf_url",
            "sources",
            "domain",
            "has_code",
            "has_weights",
            "has_dataset",
            "artifacts",
            "retraction",
            "concern",
            "versions",
        )
        if p.get(k) is not None
        or k in ("id", "title", "authors", "year", "journal", "doi", "citations", "evidence", "open_access")
    }
    out["authors"] = (out.get("authors") or [])[:4]
    # Titles and abstracts are third-party text (anyone can publish a preprint): defuse delimiter look-alikes.
    out["title"] = _untrusted.neutralize(out.get("title")) if out.get("title") else out.get("title")
    out["abstract"] = _untrusted.neutralize((p.get("abstract") or "")[:abstract_chars])
    if idx is not None:
        out["n"] = idx
    return out


def _domain_override(domain):
    """Validate a user-supplied domain ('cs', 'bio', 'econ', 'stem', ...) -> canonical value or None."""
    if domain is None or not str(domain).strip():
        return None
    dc = _domain_classifier
    try:
        return dc.resolve_domain(override_domain=domain).value
    except ValueError as e:
        raise _errors.HTTPError(400, str(e)) from e


def _paper_domain(p):
    if p.get("domain"):
        return p["domain"]
    try:
        return _domain_classifier.resolve_domain(paper=p).value
    except Exception:
        return None


def _resolve(id_or_doi):
    return _engine.resolve_paper(id_or_doi)


def _dossier_paths(directory):
    base = _workspace.safe_output_dir(directory, "research")
    return (
        base,
        *(
            _workspace.safe_output_dir(base / name, "research")
            for name in ("README.md", "papers.json", "references.bib")
        ),
    )


def _cached_paper(pid):
    if not pid:
        return None
    p = _cache.get_paper(str(pid))
    if p:
        return p
    doi = _ids._norm_doi(pid)
    if doi:
        return _cache.get_paper(_ids._paper_id(doi=doi))
    return None


def _project_call(fn, *args, writes=False, **kwargs):
    """Store errors as HTTP-style errors: unknown project -> 404, invalid values -> 400. Only write calls run the
    one-time dossier import (read tools never create or change the store)."""
    if writes:
        _import_dossiers_once()
    try:
        return fn(*args, **kwargs)
    except _store.ProjectNotFound as e:
        raise _errors.HTTPError(404, f"Project {e.args[0]!r} not found; call project_list") from e
    except ValueError as e:
        raise _errors.HTTPError(400, str(e)) from e


def _import_dossiers_once():
    """One-time migration: research dossiers saved before projects existed become projects (status included)."""
    if _store.meta_get("dossiers_imported"):
        return
    index = _cache.cache_get("dossiers:index", max_age=0) or {}
    for slug, info in index.items() if isinstance(index, dict) else ():
        # One bad entry (moved folder, huge or malformed papers.json, odd topic) must not block the others.
        try:
            _, _, json_path, _ = _dossier_paths(info["path"])
            data = {}
            if json_path.is_file() and json_path.stat().st_size <= _DOSSIER_JSON_MAX_BYTES:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            _store.create_project(str(info.get("topic") or slug), slug=str(slug))
            papers = [
                _paper_record(p)
                for p in (data.get("papers") or [] if isinstance(data, dict) else [])
                if isinstance(p, dict) and isinstance(p.get("id"), str)
            ][: _store.MAX_IDS]
            if papers:
                _store.add_papers(str(slug), papers, status="included", reason="Imported from the research dossier")
        except (_errors.HTTPError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as e:
            _log.logger.warning("dossier %r not imported as a project (%s)", slug, type(e).__name__)
    _store.meta_set("dossiers_imported", str(int(time.time())))


_DOSSIER_JSON_MAX_BYTES = 20 * 1024 * 1024


def _paper_record(p):
    record = _slim(p, abstract_chars=10000)
    record["authors"] = list(p.get("authors") or [])  # exports need every author; `_slim` keeps a few
    record.pop("rank", None)
    return record
