"""Send papers to the user's Zotero library through the Zotero Web API v3 (https://www.zotero.org/support/dev/web_api/v3).

The API key travels only in a header to a fixed host, and never appears in results, logs or error messages.
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
import urllib.parse
from typing import Any

from noul import config as _config
from noul import errors as _errors
from noul import ids as _ids
from noul.evidence import untrusted as _untrusted
from noul.library import export as _exporting
from noul.net import http as _nhttp

API = "https://api.zotero.org"
MAX_ITEMS = 50  # the API refuses larger write requests with 413
MAX_PAPERS = 200
MAX_INDEX_PAGES = 50  # 100 items per page
_DOI_IN_EXTRA = re.compile(r"^DOI:\s*(\S+)", re.I | re.M)
_MAX_COLLECTION_PAGES = 20
_COLLECTION_KEY = re.compile(r"^[A-Z0-9]{8}$")
_REFUSED = {
    400: "Zotero refused the request as invalid.",
    403: "Zotero refused the API key: it is invalid, or lacks write access to this library.",
    404: "Zotero does not know this user id or collection.",
    409: "The Zotero library is locked; try again later.",
    412: "Zotero already received this write.",
    413: "Too many items for one Zotero request.",
    429: "Zotero is rate limiting these requests; try again later.",
}


def _refusal(exc: _errors.HTTPError) -> _errors.HTTPError:
    """A clean error for a failed Zotero call, built from the status alone."""
    return _errors.HTTPError(exc.code, _REFUSED.get(exc.code, f"Zotero could not be reached (status {exc.code})."))


def _credentials() -> tuple[str, str]:
    found = _config.zotero_credentials()
    if not found:
        raise _errors.HTTPError(
            400,
            "Zotero is not configured: set NOUL_ZOTERO_API_KEY and NOUL_ZOTERO_USER_ID, or run "
            "`noul config --zotero-user-id ID --zotero-api-key-stdin`.",
        )
    return found


def _headers(key: str) -> dict[str, str]:
    return {"Zotero-API-Key": key, "Zotero-API-Version": "3", "Accept": "application/json"}


def _rows(path: str, key: str, **params: Any) -> list[dict[str, Any]]:
    """The object entries of a list endpoint. Raises HTTPError, or ValueError when the answer is not a list:
    an unreadable answer must not pass for "nothing there"."""
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    answer = json.loads(_nhttp._http_get(url, headers=_headers(key), public_only=True))
    if not isinstance(answer, list):
        raise ValueError("not a list")
    return [row for row in answer if isinstance(row, dict)]


def _creators(paper: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for family, given, literal in map(_exporting.split_name, paper.get("authors") or []):
        if literal:
            out.append({"creatorType": "author", "name": literal})
        else:
            out.append({"creatorType": "author", "firstName": given, "lastName": family})
    return out


def to_item(paper: dict[str, Any], collection: str | None = None, retracted: bool = False) -> dict[str, Any]:
    """A Zotero item from a paper record. Only fields valid for the item type: the API rejects unknown ones."""
    kind = _exporting.work_type(paper)
    item_type, venue_field = {
        "preprint": ("preprint", "repository"),
        "conference": ("conferencePaper", "proceedingsTitle"),
    }.get(kind, ("journalArticle", "publicationTitle"))
    item: dict[str, Any] = {
        "itemType": item_type,
        "title": paper.get("title") or "",
        "creators": _creators(paper),
        "date": str(paper.get("year") or ""),
        venue_field: paper.get("journal") or "",
        "DOI": _ids._norm_doi(paper.get("doi")) or "",
        "abstractNote": (paper.get("abstract") or "")[:10000],
        "extra": f"PMID: {paper['pmid']}" if paper.get("pmid") else "",
        "collections": [collection] if collection else [],
        "tags": [{"tag": "retracted"}] if retracted else [],
        "relations": {},
    }
    if item_type == "preprint":
        arxiv_id = paper.get("arxiv_id") or _exporting.arxiv_id_from_doi(paper.get("doi"))
        if arxiv_id:
            item["archiveID"] = f"arXiv:{arxiv_id}"
    return item


def _title_key(title: Any) -> str:
    """Letters and digits of any script, without markup, case or accents differences that NFKC folds."""
    text = unicodedata.normalize("NFKC", re.sub(r"<[^>]{1,40}>", "", str(title or ""))).casefold()
    return "".join(ch for ch in text if ch.isalnum())


class LibraryIndex:
    """DOIs and titles of the items in a library, read once per call. The API's quick search covers titles and
    creators only, as a phrase, so it cannot find an item by DOI or a title written differently."""

    def __init__(self, prefix: str, key: str) -> None:
        self.prefix, self.key = prefix, key
        self.dois: dict[str, str] = {}
        self.titles: dict[str, tuple[str, bool]] = {}  # title key -> (item key, item has a DOI)
        self.complete = False
        for page in range(MAX_INDEX_PAGES):
            rows = _rows(f"{prefix}/items", key, itemType="-attachment", limit=100, start=page * 100)
            for row in rows:
                self._add(row)
            if len(rows) < 100:
                self.complete = True
                break

    def _add(self, row: dict[str, Any]) -> None:
        data = _data(row)
        item_key = _safe_key(row.get("key") or data.get("key")) or "unknown"
        doi = (_ids._norm_doi(data.get("DOI")) or "").lower()
        if not doi and (found := _DOI_IN_EXTRA.search(str(data.get("extra") or ""))):
            doi = (_ids._norm_doi(found.group(1)) or "").lower()
        if doi:
            self.dois.setdefault(doi, item_key)
        title = _title_key(data.get("title"))
        if title:
            self.titles.setdefault(title, (item_key, bool(doi)))

    def find(self, paper: dict[str, Any]) -> str | None:
        """The item already holding this paper: same DOI, or same title when one of the two has no DOI."""
        doi = (_ids._norm_doi(paper.get("doi")) or "").lower()
        if doi and doi in self.dois:
            return self.dois[doi]
        title = _title_key(paper.get("title"))
        if title and title in self.titles:
            item_key, has_doi = self.titles[title]
            if not (doi and has_doi):
                return item_key
        if not self.complete and str(paper.get("title") or "").strip():
            return self._search(paper, doi, title)
        return None

    def _search(self, paper: dict[str, Any], doi: str, title: str) -> str | None:
        """Beyond the items read: a title search, which can still miss an item whose title is written differently."""
        query = str(paper.get("title")).strip()[:200]
        for row in _rows(f"{self.prefix}/items", self.key, q=query, itemType="-attachment", limit=100):
            data = _data(row)
            other = (_ids._norm_doi(data.get("DOI")) or "").lower()
            same_title = title and _title_key(data.get("title")) == title
            if (doi and other == doi) or (same_title and not (doi and other)):
                return _safe_key(row.get("key")) or "unknown"
        return None


def collection_key(name_or_key: str, key_path: str, key: str) -> str | None:
    """Resolve a collection given by its key or by its exact name (ignoring case). Never creates one."""
    wanted = name_or_key.strip()
    for page in range(_MAX_COLLECTION_PAGES):
        rows = _rows(f"{key_path}/collections", key, limit=100, start=page * 100)
        for row in rows:
            data = _data(row)
            if row.get("key") == wanted or str(data.get("name") or "").strip().lower() == wanted.lower():
                found = str(row.get("key") or "")
                if _COLLECTION_KEY.match(found):
                    return found
        if len(rows) < 100:
            break
    return None


def _data(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data")
    return data if isinstance(data, dict) else {}


def _safe_key(value: Any) -> str | None:
    """A Zotero object key from a response, or None when it does not have the shape of one."""
    return value if isinstance(value, str) and _COLLECTION_KEY.match(value) else None


def _brief(paper: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"id": paper.get("id"), "title": paper.get("title"), **extra}


def check_credentials() -> None:
    _credentials()


def check_size(papers: list[dict[str, Any]]) -> None:
    if len(papers) > MAX_PAPERS:
        raise _errors.HTTPError(400, f"At most {MAX_PAPERS} papers per call ({len(papers)} given); split them.")


def _read_library(prefix: str, key: str) -> LibraryIndex:
    try:
        return LibraryIndex(prefix, key)
    except _errors.HTTPError as exc:
        raise _refusal(exc) from None
    except ValueError:
        raise _errors.HTTPError(502, "Zotero answered with something unreadable; nothing was written.") from None


def push(papers: list[dict[str, Any]], collection: str | None = None) -> dict[str, Any]:
    """Create the papers in the user's library, skipping the ones already there. `papers` may carry `retraction`."""
    check_size(papers)
    user_id, key = _credentials()
    prefix = f"/users/{user_id}"
    out: dict[str, Any] = {
        "zotero_user_id": user_id,  # shown so that a changed account can be noticed
        "created": [],
        "skipped": [],
        "failed": [],
        "warnings": [],
        "retracted_ids": [],
        "unchecked_ids": [],
    }
    target = None
    if collection:
        try:
            target = collection_key(collection, prefix, key)
        except _errors.HTTPError as exc:
            raise _refusal(exc) from None
        except ValueError:
            raise _errors.HTTPError(502, "Zotero answered with something unreadable; nothing was written.") from None
        if not target:
            raise _errors.HTTPError(404, "No such Zotero collection: create it in Zotero first, or check the name.")
    library = _read_library(prefix, key)
    if not library.complete:
        out["warnings"].append(
            f"The library has more than {MAX_INDEX_PAGES * 100} items: beyond those, duplicates were looked for by "
            "title only, so one may have been created."
        )
    present = "already in the library" + (" (left in its collections)" if target else "")
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paper in papers:
        identity = (_ids._norm_doi(paper.get("doi")) or "").lower() or _title_key(paper.get("title"))
        if identity and identity in seen:
            out["skipped"].append(_brief(paper, reason="repeated in this call"))
            continue
        seen.add(identity)
        try:
            found = library.find(paper)
        except (_errors.HTTPError, ValueError):  # unknown is not "absent": writing now could create a duplicate
            out["failed"].append(_brief(paper, reason="Could not check Zotero for a duplicate."))
            continue
        if found:
            out["skipped"].append(_brief(paper, reason=present, zotero_key=found))
        else:
            pending.append(paper)
    for start in range(0, len(pending), MAX_ITEMS):
        _write(pending[start : start + MAX_ITEMS], target, prefix, key, out)
    out["retracted_ids"] = [str(p.get("id")) for p in papers if (p.get("retraction") or {}).get("is_retracted")]
    if out["retracted_ids"]:
        out["warnings"].append("Some papers are retracted (`retracted_ids`): tagged `retracted` if they were created.")
    # Ids derive from DOIs, which depositors write: they stay in structured fields, out of the warnings.
    out["untrusted_fields"] = [
        f"{group}[].{field}" for group in ("created", "skipped", "failed") for field in ("id", "title")
    ] + ["failed[].reason", "retracted_ids", "unchecked_ids", "incomplete_ids"]
    return out


def _write(pending: list[dict[str, Any]], target: str | None, prefix: str, key: str, out: dict[str, Any]) -> None:
    items = [to_item(p, target, bool((p.get("retraction") or {}).get("is_retracted"))) for p in pending]
    headers = {**_headers(key), "Zotero-Write-Token": secrets.token_hex(16)}
    try:
        answer = _nhttp._http_post_json(f"{API}{prefix}/items", items, headers=headers)
    except _errors.HTTPError as exc:
        if exc.code in (401, 403) and not out["created"]:
            raise _refusal(exc) from None
        reason = _REFUSED.get(exc.code, f"Zotero could not be reached (status {exc.code}).")
        if exc.code >= 500:  # the write may have happened: a new call skips what did
            reason = f"{reason} The items may have been created; run the call again to skip any that were."
        out["failed"].extend(_brief(p, reason=reason) for p in pending)
        return
    success = {**(answer.get("success") or {}), **(answer.get("successful") or {})}
    unchanged = answer.get("unchanged") or {}
    failed = answer.get("failed") or {}
    for index, paper in enumerate(pending):
        slot = str(index)
        if slot in success:
            value = success[slot]
            found = value.get("key") if isinstance(value, dict) else value
            out["created"].append(_brief(paper, zotero_key=_safe_key(found)))
        elif slot in unchanged:
            out["skipped"].append(_brief(paper, reason="already in the library", zotero_key=_safe_key(unchanged[slot])))
        else:
            entry = failed.get(slot)
            info = entry if isinstance(entry, dict) else {}
            message = _untrusted.label(str(info.get("message") or "no result").replace(key, "[key]"), 160)
            code = info.get("code") if isinstance(info.get("code"), int) else "unknown"
            out["failed"].append(_brief(paper, reason=f"Zotero refused the item (code {code}): {message}"))
