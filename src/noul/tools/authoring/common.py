"""What the authoring tools share: draft loading (inline text or a workspace file) and the stopword list."""

from pathlib import Path

from noul import errors as _errors
from noul import workspace as _workspace


def _tools():
    """The tool layer (`noul.api`), imported lazily because `noul.api` imports this module."""
    from noul import api

    return api


_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "over",
        "under",
        "between",
        "among",
        "have",
        "has",
        "had",
        "been",
        "were",
        "was",
        "are",
        "is",
        "be",
        "can",
        "could",
        "would",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "will",
        "while",
        "whereas",
        "however",
        "therefore",
        "thus",
        "hence",
        "recent",
        "studies",
        "study",
        "show",
        "shown",
        "shows",
        "demonstrate",
        "demonstrates",
        "demonstrated",
        "prior",
        "work",
        "works",
        "existing",
        "previous",
        "literature",
        "paper",
        "papers",
        "approach",
        "approaches",
        "method",
        "methods",
        "results",
        "result",
        "indicate",
        "indicates",
        "known",
        "significantly",
        "achieves",
        "outperforms",
        "reduces",
        "improves",
        "state",
        "art",
        "citation",
        "needed",
        "cite",
        "todo",
        "our",
        "we",
        "their",
        "they",
        "these",
        "those",
        "such",
        "also",
        "more",
        "most",
        "other",
        "than",
        "very",
        "well",
    }
)


def _load_text_or_workspace_file(raw_input: str) -> tuple[str, str, Path | None]:
    """Return `(content_text, source_label, resolved_path_or_None)`.
    Validates strict workspace confinement and rejects path traversal / symlinks."""
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise _errors.HTTPError(400, "Input text or file path must be a non-empty string.")
    s = raw_input.strip()
    if _workspace.looks_like_filepath(s):
        path = _workspace.validate_path(s, must_exist=True, readable=True)
        return path.read_text(encoding="utf-8", errors="replace"), path.name, path
    return raw_input, "inline_draft", None
