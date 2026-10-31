"""Open Access PDF downloads into the workspace."""

import re
from pathlib import Path

from noul import errors as _errors
from noul.library import fulltext as _ft
from noul.net import cache as _cache
from noul.net import http as _nhttp
from noul.search import engine as _engine


def _safe_filename(p):
    year = p.get("year") or "nd"
    first_author = ((p.get("authors") or ["Unknown"])[0]).split()[-1]
    first_author = re.sub(r"[^A-Za-z0-9_-]", "", first_author) or "Author"
    title_short = re.sub(r"[^A-Za-z0-9]+", "_", (p.get("title") or "paper")).strip("_")[:55]
    short_id = re.sub(r"[^A-Za-z0-9._-]", "_", p.get("id") or "id")[:25]
    return f"{year}_{first_author}_{title_short}_{short_id}.pdf"


def download_paper_pdf(id, output_dir="./papers"):
    """Download Open Access PDF for a paper to `output_dir`."""
    p = _engine.resolve_paper(id)
    dest_dir = Path(output_dir).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = (dest_dir / _safe_filename(p)).resolve()
    if not target.is_relative_to(dest_dir):
        raise _errors.HTTPError(403, f"PDF path '{target}' is outside the download directory")

    if target.exists() and target.stat().st_size > 1024:
        return {
            "id": p["id"],
            "title": p.get("title"),
            "status": "downloaded",
            "filepath": str(target),
            "source": "local_cache",
        }

    pdf_url, source = _ft.resolve_pdf_url(p)
    if pdf_url:
        try:
            raw = _nhttp._http_get(pdf_url, timeout=25, binary=True, public_only=True)
            if raw.startswith(b"%PDF"):
                target.write_bytes(raw)
                # Also populate fulltext cache if pypdf is installed
                txt = _ft._extract_pdf_text(raw)
                if txt and len(txt) > 200:
                    _cache.cache_set("ft:" + p["id"], {"text": txt, "url": pdf_url, "source": source})
                return {
                    "id": p["id"],
                    "title": p.get("title"),
                    "status": "downloaded",
                    "filepath": str(target),
                    "source": source,
                    "pdf_url": pdf_url,
                }
        except Exception as e:
            err_msg = str(e)
        else:
            err_msg = "URL did not return a direct PDF binary"
    else:
        err_msg = "No Open Access PDF URL found"

    return {
        "id": p["id"],
        "title": p.get("title"),
        "status": "paywalled_or_unavailable",
        "reason": err_msg,
        "doi_url": f"https://doi.org/{p['doi']}" if p.get("doi") else None,
        "pdf_url": pdf_url,
    }
