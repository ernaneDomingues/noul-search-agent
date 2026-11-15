"""BibTeX: parsing, keys, clean-up and the upgrade of preprint entries to their published version (`sanitize_bibtex`)."""

import re
from pathlib import Path

from noul import errors as _errors
from noul import ids as _ids
from noul import workspace as _workspace
from noul.evidence import integrity as _integrity
from noul.providers import arxiv_client
from noul.schemas.authoring import (
    BibtexEntryUpdate,
    BibtexSanitizeReport,
)
from noul.tools.authoring import common as _common


def _make_bibtex_key(paper: dict, used_keys: set | None = None) -> str:
    authors = paper.get("authors") or ["anon"]
    first_author = str(authors[0] or "anon").strip()
    if "," in first_author:
        surname = first_author.split(",", 1)[0].strip().lower()
    else:
        surname = first_author.split()[-1].strip().lower() if first_author.split() else "anon"
    surname = re.sub(r"[^a-z0-9]", "", surname) or "anon"
    year = str(paper.get("year") or "nd")
    title_words = [
        w.lower()
        for w in re.findall(r"[A-Za-z0-9]{3,}", paper.get("title") or "")
        if w.lower() not in _common._STOPWORDS
    ]
    word = re.sub(r"[^a-z0-9]", "", title_words[0]) if title_words else "paper"
    base_key = f"{surname}{year}{word}"
    if used_keys is None:
        return base_key
    key = base_key
    suffix = 2
    while key in used_keys:
        key = f"{base_key}_{suffix}"
        suffix += 1
    used_keys.add(key)
    return key


def _format_bibtex_entry_from_paper(paper: dict, key: str) -> str:
    venue = str(paper.get("journal") or paper.get("venue") or "arXiv").strip()
    is_conf = arxiv_client.is_conference_venue(venue)
    etype = "inproceedings" if is_conf else "article"
    authors_bib = " and ".join(paper.get("authors") or ["Anonymous"])
    title = paper.get("title") or ""
    year = paper.get("year") or ""
    lines = [
        f"@{etype}{{{key},",
        f"  title = {{{title}}},",
        f"  author = {{{authors_bib}}},",
        f"  year = {{{year}}},",
    ]
    if is_conf:
        lines.append(f"  booktitle = {{{venue}}},")
    else:
        lines.append(f"  journal = {{{venue}}},")
    if paper.get("doi"):
        lines.append(f"  doi = {{{paper['doi']}}},")
    elif paper.get("arxiv_id"):
        lines.append(f"  eprint = {{{paper['arxiv_id']}}},")
    lines.append("}")
    return "\n".join(lines)


_BIB_ENTRY_START_RE = re.compile(r"@([a-zA-Z]+)\s*\{\s*([^,\s]+)\s*,", re.M)


_PROTECTED_ACRONYMS = (
    "LLMs",
    "LLM",
    "GPUs",
    "GPU",
    "RLHF",
    "Transformer",
    "BERT",
    "GPT-4",
    "GPT",
    "CONSORT",
    "STROBE",
    "PRISMA",
    "DNA",
    "RNA",
    "COVID-19",
    "MMLU",
    "GSM8K",
)


def parse_bibtex_entries(raw_bib: str) -> list[dict]:
    """Parse `.bib` text into a list of dicts: `{'entry_type': str, 'key': str, 'fields': OrderedDict}`."""
    entries: list[dict] = []
    text = raw_bib or ""
    pos = 0
    while pos < len(text):
        m = _BIB_ENTRY_START_RE.search(text, pos)
        if not m:
            break
        etype = m.group(1).lower().strip()
        key = m.group(2).strip()
        brace_open = text.find("{", m.start())
        depth = 0
        end_idx = len(text)
        for i in range(brace_open, len(text)):
            if text[i] == "{" and (i == 0 or text[i - 1] != "\\"):
                depth += 1
            elif text[i] == "}" and (i == 0 or text[i - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
        body = text[m.end() : end_idx]
        pos = end_idx + 1
        if etype in ("comment", "preamble", "string"):
            continue

        fields: dict[str, str] = {}
        f_pos = 0
        while f_pos < len(body):
            fm = re.search(r"([a-zA-Z0-9_:-]+)\s*=\s*", body[f_pos:])
            if not fm:
                break
            fname = fm.group(1).lower().strip()
            val_start = f_pos + fm.end()
            while val_start < len(body) and body[val_start].isspace():
                val_start += 1
            if val_start >= len(body):
                break
            ch = body[val_start]
            if ch == "{":
                d = 0
                v_end = val_start
                for j in range(val_start, len(body)):
                    if body[j] == "{" and (j == 0 or body[j - 1] != "\\"):
                        d += 1
                    elif body[j] == "}" and (j == 0 or body[j - 1] != "\\"):
                        d -= 1
                        if d == 0:
                            v_end = j
                            break
                fval = body[val_start + 1 : v_end].strip()
                f_pos = v_end + 1
            elif ch == '"':
                v_end = val_start + 1
                while v_end < len(body):
                    if body[v_end] == '"' and body[v_end - 1] != "\\":
                        break
                    v_end += 1
                fval = body[val_start + 1 : v_end].strip()
                f_pos = v_end + 1
            else:
                comma = body.find(",", val_start)
                v_end = comma if comma != -1 else len(body)
                fval = body[val_start:v_end].strip().rstrip(",").strip()
                f_pos = v_end + 1
            fields[fname] = re.sub(r"\s+", " ", fval).strip()

        entries.append({"entry_type": etype, "key": key, "fields": fields})
    return entries


def _normalize_authors_field(raw_author: str) -> str:
    if not raw_author:
        return ""
    s = re.sub(r"\s*;\s*", " and ", raw_author.strip())
    s = re.sub(r"\s+&\s+", " and ", s)
    parts = [p.strip() for p in re.split(r"\band\b", s) if p.strip()]
    return " and ".join(parts)


def _escape_latex_outside_math(value: str, protect_acronyms: bool = False) -> tuple[str, int]:
    if not value:
        return "", 0
    fixes = 0
    segments = re.split(r"(\$[^$]*\$)", value)
    out_segs = []
    for seg in segments:
        if seg.startswith("$") and seg.endswith("$"):
            out_segs.append(seg)
            continue
        for ch in ("&", "%", "_"):
            pattern = re.compile(rf"(?<!\\){re.escape(ch)}")
            matches = len(pattern.findall(seg))
            if matches:
                fixes += matches
                seg = pattern.sub(lambda _, c=ch: f"\\{c}", seg)
        if protect_acronyms:
            for acr in _PROTECTED_ACRONYMS:
                seg = re.sub(rf"(?<!\{{)\b({re.escape(acr)})\b(?!\}})", lambda m: f"{{{m.group(1)}}}", seg)
        out_segs.append(seg)
    return "".join(out_segs), fixes


def _entry_completeness_score(entry: dict) -> int:
    f = entry.get("fields") or {}
    score = 0
    if not arxiv_client.is_arxiv_preprint_entry(entry):
        score += 10
    if f.get("doi") and not str(f["doi"]).lower().startswith("10.48550/arxiv"):
        score += 5
    for k in ("journal", "booktitle", "pages", "volume", "number", "publisher", "year", "author"):
        if f.get(k):
            score += 1
    return score


def _choose_canonical_bib_key(keys: list[str], fields: dict, used_keys: set) -> str:
    # Prefer a clean human-readable key like 'vaswani2017attention' over 'vaswani2017_dup' or 'arxiv2305'
    clean_candidates = [
        k
        for k in keys
        if re.match(r"^[a-z]+[0-9]{4}[a-z0-9]+$", k, re.I)
        and not k.lower().startswith("arxiv")
        and not re.search(r"_(?:dup|copy|old|\d+)$", k, re.I)
    ]
    if clean_candidates:
        chosen = clean_candidates[0]
    else:
        authors_raw = fields.get("author") or "anon"
        first_author = authors_raw.split(" and ")[0].strip()
        surname = (
            first_author.split(",")[0].strip().lower()
            if "," in first_author
            else (first_author.split()[-1].lower() if first_author.split() else "anon")
        )
        surname = re.sub(r"[^a-z0-9]", "", surname) or "anon"
        year = re.sub(r"[^0-9]", "", str(fields.get("year") or ""))[:4] or "nd"
        t_words = [
            w.lower()
            for w in re.findall(r"[A-Za-z0-9]{3,}", re.sub(r"[{}]", "", fields.get("title") or ""))
            if w.lower() not in _common._STOPWORDS
        ]
        word = re.sub(r"[^a-z0-9]", "", t_words[0]) if t_words else "paper"
        chosen = keys[0] if keys and not keys[0].lower().startswith("arxiv") else f"{surname}{year}{word}"

    base = chosen
    idx = 2
    while chosen in used_keys:
        chosen = f"{base}_{idx}"
        idx += 1
    used_keys.add(chosen)
    return chosen


# One Crossref lookup per entry (cached weekly): bound the cost of very large bibliographies.
MAX_INTEGRITY_CHECKS = 200


MAX_CROSSREF_FAILURES_IN_A_ROW = 3  # an unreachable Crossref must not cost a timeout per entry


def sanitize_bibtex(
    bibtex_input: str,
    update_preprints: bool = True,
    output_path: str | None = None,
) -> dict:
    """Issue #25: Parse and sanitize a `.bib` file or BibTeX string:
    - Upgrade arXiv/CoRR preprints (`@misc` / `arXiv`) to their peer-reviewed conference (`@inproceedings`)
      or journal (`@article`) version with definitive DOI;
    - Deduplicate identical works while preserving the cleanest citation key;
    - Escape unescaped LaTeX characters (`&`, `%`, `_`) and protect uppercase acronyms (`{LLM}`, `{GPU}`, ...)."""
    raw_bib, _, _ = _common._load_text_or_workspace_file(bibtex_input)
    parsed = parse_bibtex_entries(raw_bib)
    if not parsed:
        raise _errors.HTTPError(400, "No valid BibTeX entries found in input.")

    out_file: Path | None = None
    if output_path is not None and str(output_path).strip():
        out_file = _workspace.validate_path(str(output_path).strip(), must_exist=False)
        if out_file.suffix.lower() != ".bib":
            raise _errors.HTTPError(400, "output_path must be a .bib file.")

    # Group duplicates by (norm_doi | arxiv_id | norm_title)
    groups: list[list[dict]] = []
    group_index_by_sig: dict[str, int] = {}

    for entry in parsed:
        f = entry["fields"]
        doi = _ids._norm_doi(f.get("doi"))
        if doi and doi.startswith("10.48550/arxiv"):
            doi = None
        ax_id = arxiv_client.extract_arxiv_id_from_entry(entry)
        norm_t = arxiv_client._norm_title_key(f.get("title") or "")

        sigs = []
        if doi:
            sigs.append(f"doi:{doi}")
        if ax_id:
            sigs.append(f"ax:{ax_id.lower()}")
        if norm_t:
            sigs.append(f"title:{norm_t}")

        matched_idx = next((group_index_by_sig[s] for s in sigs if s in group_index_by_sig), None)
        if matched_idx is None:
            matched_idx = len(groups)
            groups.append([entry])
        else:
            groups[matched_idx].append(entry)
        for s in sigs:
            group_index_by_sig[s] = matched_idx

    duplicates_removed = 0
    preprints_upgraded = 0
    retracted_entries = 0
    integrity_checks = 0
    crossref_failures_in_a_row = 0
    unchecked_dois = 0
    dois_completed = 0
    latex_escapes_fixed = 0
    updates: list[BibtexEntryUpdate] = []
    sanitized_blocks: list[str] = []
    used_keys: set = set()
    warnings: list[str] = []

    for grp in groups:
        grp_sorted = sorted(grp, key=_entry_completeness_score, reverse=True)
        primary = grp_sorted[0]
        merged_fields = dict(primary["fields"])
        etype = primary["entry_type"]
        all_keys = [e["key"] for e in grp]

        for other in grp_sorted[1:]:
            duplicates_removed += 1
            for fk, fv in other["fields"].items():
                if fv and not merged_fields.get(fk):
                    merged_fields[fk] = fv

        notes: list[str] = []
        old_venue = merged_fields.get("booktitle") or merged_fields.get("journal") or etype
        had_doi_before = bool(
            merged_fields.get("doi") and not str(merged_fields["doi"]).lower().startswith("10.48550/arxiv")
        )
        action = "deduplicated" if len(grp) > 1 else "kept"
        if len(grp) > 1:
            notes.append(f"Merged {len(grp)} duplicate entries ({', '.join(all_keys)}).")

        # Check preprint upgrade
        candidate_entry = {"entry_type": etype, "key": primary["key"], "fields": merged_fields}
        if update_preprints and arxiv_client.is_arxiv_preprint_entry(candidate_entry):
            resolved = arxiv_client.resolve_published_venue_for_preprint(candidate_entry)
            if resolved:
                preprints_upgraded += 1
                action = "upgraded_preprint"
                etype = resolved["entry_type"]
                venue_name = resolved["venue"]
                if etype == "inproceedings":
                    merged_fields["booktitle"] = venue_name
                    if arxiv_client._PREPRINT_VENUE_RE.search(str(merged_fields.get("journal") or "")):
                        merged_fields.pop("journal", None)
                else:
                    merged_fields["journal"] = venue_name
                if resolved.get("year"):
                    merged_fields["year"] = str(resolved["year"])
                if resolved.get("doi"):
                    merged_fields["doi"] = resolved["doi"]
                    if not had_doi_before:
                        dois_completed += 1
                if resolved.get("arxiv_id") and not merged_fields.get("eprint"):
                    merged_fields["eprint"] = str(resolved["arxiv_id"])
                notes.append(f"Upgraded arXiv preprint to {etype} in '{venue_name}'.")
        elif not had_doi_before and update_preprints:
            # Try to complete missing DOI for already-published entry if available in cache/APIs
            resolved = arxiv_client.resolve_published_venue_for_preprint(candidate_entry)
            if resolved and resolved.get("doi"):
                merged_fields["doi"] = resolved["doi"]
                dois_completed += 1
                if action == "kept":
                    action = "doi_completed"
                notes.append(f"Completed missing DOI: {resolved['doi']}.")

        # Standardize authors
        if merged_fields.get("author"):
            merged_fields["author"] = _normalize_authors_field(merged_fields["author"])

        # Escape LaTeX characters & protect acronyms
        entry_fixes = 0
        for field_name in ("title", "journal", "booktitle"):
            if merged_fields.get(field_name):
                fixed_val, cnt = _escape_latex_outside_math(
                    merged_fields[field_name], protect_acronyms=(field_name == "title")
                )
                merged_fields[field_name] = fixed_val
                entry_fixes += cnt
        if entry_fixes > 0:
            latex_escapes_fixed += entry_fixes
            if action == "kept":
                action = "latex_sanitized"
            notes.append(f"Escaped {entry_fixes} unescaped LaTeX special character(s).")

        canonical_key = _choose_canonical_bib_key(all_keys, merged_fields, used_keys)
        new_venue = merged_fields.get("booktitle") or merged_fields.get("journal") or etype

        # Integrity: a retracted paper in the bibliography is reported (kept: citing a retraction can be legitimate).
        facts = {}
        if merged_fields.get("doi"):
            if integrity_checks < MAX_INTEGRITY_CHECKS and crossref_failures_in_a_row < MAX_CROSSREF_FAILURES_IN_A_ROW:
                integrity_checks += 1
                facts = _integrity.crossref_facts(merged_fields["doi"])
                crossref_failures_in_a_row = crossref_failures_in_a_row + 1 if facts.get("check_failed") else 0
                unchecked_dois += 1 if facts.get("check_failed") else 0
            else:
                unchecked_dois += 1
        if facts.get("retraction"):
            retracted_entries += 1
            desc = _integrity.describe(facts)
            notes.append(f"RETRACTED ({desc}): cite it only as a retracted paper, or remove it.")
            warnings.append(f"Entry '{canonical_key}' is a retracted paper ({desc}).")
        if facts.get("concern"):
            notes.append("The publisher issued an expression of concern about this paper.")

        # Format clean BibTeX block
        ordered_keys = [
            k
            for k in (
                "title",
                "author",
                "year",
                "booktitle",
                "journal",
                "volume",
                "number",
                "pages",
                "publisher",
                "doi",
                "eprint",
                "url",
            )
            if merged_fields.get(k)
        ]
        for k in merged_fields:
            if k not in ordered_keys and merged_fields[k]:
                ordered_keys.append(k)

        bib_lines = [f"@{etype}{{{canonical_key},"]
        for k in ordered_keys:
            bib_lines.append(f"  {k} = {{{merged_fields[k]}}},")
        bib_lines.append("}")
        sanitized_blocks.append("\n".join(bib_lines))

        updates.append(
            BibtexEntryUpdate(
                original_key=primary["key"],
                canonical_key=canonical_key,
                title=merged_fields.get("title") or "",
                action=action,
                old_venue_or_type=old_venue,
                new_venue_or_type=new_venue,
                doi=merged_fields.get("doi"),
                notes=notes,
            )
        )

    if unchecked_dois:
        warnings.append(
            f"Retraction status unknown for {unchecked_dois} entries with a DOI (at most {MAX_INTEGRITY_CHECKS} checks "
            "per run, and Crossref must answer): the absence of a flag does not clear them."
        )
    sanitized_text = "\n\n".join(sanitized_blocks) + ("\n" if sanitized_blocks else "")
    if out_file is not None:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(sanitized_text, encoding="utf-8")

    report = BibtexSanitizeReport(
        entries_total=len(parsed),
        entries_out=len(sanitized_blocks),
        duplicates_removed=duplicates_removed,
        preprints_upgraded=preprints_upgraded,
        dois_completed=dois_completed,
        latex_escapes_fixed=latex_escapes_fixed,
        retracted_entries=retracted_entries,
        updates=updates,
        sanitized_bibtex=sanitized_text.strip(),
        warnings=warnings,
    )
    return report.model_dump()
