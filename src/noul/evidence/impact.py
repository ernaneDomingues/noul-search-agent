"""Transparent citation/venue impact band (`tier`) - not a JCR/SJR quartile."""

import re
import time


def _norm_venue(venue):
    v = re.sub(r"\([^)]*\)", " ", (venue or "").lower()).replace("&", " and ")
    v = re.sub(r"[^a-z0-9]+", " ", v).strip()
    v = re.sub(r"^(?:19|20)\d\d\s+", "", v)
    return re.sub(r"^the\s+", "", v)


# Full normalized venue names, compared by equality (never by substring: "science" must not
# match "Journal of Food Science", nor "cell" match "Stem Cells International").
EXACT_ELITE_VENUES = frozenset(
    _norm_venue(v)
    for v in (
        "Nature",
        "Science",
        "Cell",
        "The Lancet",
        "Lancet",
        "New England Journal of Medicine",
        "The New England Journal of Medicine",
        "NEJM",
        "JAMA",
        "Journal of the American Medical Association",
        "BMJ",
        "British Medical Journal",
        "Annals of Internal Medicine",
        "Cochrane Database of Systematic Reviews",
        "Circulation",
        "European Heart Journal",
        "PNAS",
        "Proceedings of the National Academy of Sciences",
        "Proceedings of the National Academy of Sciences of the United States of America",
        "NeurIPS",
        "Neural Information Processing Systems",
        "Advances in Neural Information Processing Systems",
        "ICML",
        "International Conference on Machine Learning",
        "ICLR",
        "International Conference on Learning Representations",
        "ACL",
        "Annual Meeting of the Association for Computational Linguistics",
        "CVPR",
        "Computer Vision and Pattern Recognition",
        "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
        "ACM Computing Surveys",
        "American Economic Review",
        "Quarterly Journal of Economics",
        "Econometrica",
        "Journal of Finance",
    )
)


def _estimate_impact(citations=0, year=None, venue=""):
    """Return (impact, tier). `tier` is a transparent citation/venue band (top/high/standard/emerging),
    not a JCR/SJR quartile."""
    cites = int(citations or 0)
    age = max(1, (time.gmtime().tm_year - int(year))) if year else 5
    cites_per_year = cites / age
    is_elite = _norm_venue(venue) in EXACT_ELITE_VENUES
    if is_elite or cites_per_year >= 25 or cites >= 200:
        return "high", "top"
    if cites_per_year >= 5 or cites >= 35:
        return "medium", "high"
    if cites >= 5:
        return "modest", "standard"
    return "emerging", "emerging"
