"""Input checks shared by the technical tools."""

from noul import errors as _errors


def _paper_id(paper_id):
    if not isinstance(paper_id, str) or not paper_id.strip():
        raise _errors.HTTPError(400, "paper_id must be a non-empty string")
    return paper_id.strip()
