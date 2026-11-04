"""Citation export (BibTeX, RIS, CSV, CSL-JSON)."""

import csv
import io
import json
import re

from noul import errors as _errors

_PREPRINT_DOI = ("10.48550/", "10.31219/", "10.31234/", "10.2139/ssrn")
_PREPRINT_VENUE = re.compile(r"arxiv|biorxiv|medrxiv|chemrxiv|psyarxiv|ssrn|preprint", re.I)
_CONFERENCE_VENUE = re.compile(r"conference|proceedings|symposium|workshop", re.I)
# Journals with "Proceedings" in the name.
_PROCEEDINGS_JOURNAL = re.compile(
    r"national academy|mayo clinic|royal society|of the ieee\s*$|american mathematical society|nutrition society|"
    r"acm on\b|vldb endowment",
    re.I,
)


def work_type(paper):
    """`preprint`, `conference` or `journal`, from the DOI prefix and the venue name."""
    doi = str(paper.get("doi") or "").lower()
    venue = str(paper.get("journal") or "")
    # 10.1101 is also the prefix of the Cold Spring Harbor journals: without a preprint venue only if none is known.
    if doi.startswith(_PREPRINT_DOI) or _PREPRINT_VENUE.search(venue) or (doi.startswith("10.1101/") and not venue):
        return "preprint"
    return "conference" if _CONFERENCE_VENUE.search(venue) and not _PROCEEDINGS_JOURNAL.search(venue) else "journal"


def split_name(name):
    """(family, given, literal) of an author string: "Given Family" or "Family, Given"; one word stays literal."""
    clean = " ".join(str(name or "").split())
    if "," in clean:
        family, _, given = clean.partition(",")
        return family.strip(), given.strip(), ""
    if " " not in clean:
        return "", "", clean
    words = clean.split(" ")
    start = len(words) - 1
    while start > 1 and words[start - 1].islower():  # particles belong to the family name: "van der Waals"
        start -= 1
    return " ".join(words[start:]), " ".join(words[:start]), ""


def arxiv_id_from_doi(doi):
    text = str(doi or "").lower()
    return text.removeprefix("10.48550/arxiv.") if text.startswith("10.48550/arxiv.") else None


def _is_retracted(paper):
    return bool((paper.get("retraction") or {}).get("is_retracted"))


def _cite_key(paper, idx):
    first = ((paper.get("authors") or ["anon"])[0]).split()[-1].lower()
    first = re.sub(r"[^a-z0-9]", "", first) or "ref"
    return f"{first}{paper.get('year') or 'nd'}_{idx}"


def csl_item(paper, idx):
    """One CSL-JSON item (https://citeproc-js.readthedocs.io), the format Zotero, Mendeley and Pandoc import."""
    item = {
        "id": _cite_key(paper, idx),
        "type": {"preprint": "article", "conference": "paper-conference"}.get(work_type(paper), "article-journal"),
        "title": paper.get("title") or "",
    }
    authors = []
    for family, given, literal in map(split_name, paper.get("authors") or []):
        authors.append({"literal": literal} if literal else {"family": family, "given": given})
    if authors:
        item["author"] = authors
    if str(paper.get("year") or "").isdigit():
        item["issued"] = {"date-parts": [[int(paper["year"])]]}
    if paper.get("journal"):
        item["container-title"] = paper["journal"]
    if paper.get("doi"):
        item["DOI"] = paper["doi"]
    if paper.get("pmid"):
        item["PMID"] = str(paper["pmid"])
    if _is_retracted(paper):
        item["note"] = "RETRACTED"
    return item


def export(papers, format="bibtex"):
    fmt = (format or "bibtex").lower()
    if fmt == "csl-json":
        items = [csl_item(p, idx) for idx, p in enumerate(papers, 1)]
        return json.dumps(items, ensure_ascii=False, indent=2), "application/vnd.citationstyles.csl+json"

    if fmt == "bibtex":
        entries = []
        for idx, p in enumerate(papers, 1):
            key = _cite_key(p, idx)
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
            if _is_retracted(p):
                lines.append("  note = {RETRACTED},")
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
            if _is_retracted(p):
                lines.append("N1  - RETRACTED")
            lines.append("ER  - ")
            entries.append("\n".join(lines))
        return "\n\n".join(entries), "application/x-research-info-systems"

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["id", "title", "authors", "year", "journal", "doi", "citations", "evidence", "open_access", "retracted"]
        )
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
                    _is_retracted(p),
                ]
            )
        return buf.getvalue(), "text/csv"

    raise _errors.HTTPError(400, f"Unsupported export format: {format}")
