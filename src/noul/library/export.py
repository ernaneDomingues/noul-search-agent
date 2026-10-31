"""Citation export (BibTeX, RIS, CSV)."""

import csv
import io
import re

from noul import errors as _errors


def export(papers, format="bibtex"):
    fmt = (format or "bibtex").lower()
    if fmt == "bibtex":
        entries = []
        for idx, p in enumerate(papers, 1):
            first = ((p.get("authors") or ["anon"])[0]).split()[-1].lower()
            first = re.sub(r"[^a-z0-9]", "", first) or "ref"
            key = f"{first}{p.get('year') or 'nd'}_{idx}"
            authors_bib = " and ".join(p.get("authors") or [])
            lines = [
                f"@article{{{key},",
                f"  title = {{{p.get('title') or ''}}},",
                f"  author = {{{authors_bib}}},",
                f"  year = {{{p.get('year') or ''}}},",
                f"  journal = {{{p.get('journal') or ''}}},",
            ]
            if p.get("doi"):
                lines.append(f"  doi = {{{p['doi']}}},")
            lines.append("}")
            entries.append("\n".join(lines))
        return "\n\n".join(entries), "application/x-bibtex"

    if fmt == "ris":
        entries = []
        for p in papers:
            lines = ["TY  - JOUR", f"TI  - {p.get('title') or ''}"]
            for a in p.get("authors") or []:
                lines.append(f"AU  - {a}")
            if p.get("year"):
                lines.append(f"PY  - {p['year']}")
            if p.get("journal"):
                lines.append(f"JO  - {p['journal']}")
            if p.get("doi"):
                lines.append(f"DO  - {p['doi']}")
            lines.append("ER  - ")
            entries.append("\n".join(lines))
        return "\n\n".join(entries), "application/x-research-info-systems"

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "title", "authors", "year", "journal", "doi", "citations", "evidence", "open_access"])
        for p in papers:
            writer.writerow(
                [
                    p.get("id") or "",
                    p.get("title") or "",
                    "; ".join(p.get("authors") or []),
                    p.get("year") or "",
                    p.get("journal") or "",
                    p.get("doi") or "",
                    p.get("citations") or 0,
                    p.get("evidence") or "",
                    bool(p.get("open_access")),
                ]
            )
        return buf.getvalue(), "text/csv"

    raise _errors.HTTPError(400, f"Unsupported export format: {format}")
