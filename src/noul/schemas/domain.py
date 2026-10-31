"""Scientific macro-domains (#19) and peer-review verdicts."""

from enum import Enum


class ScientificDomain(str, Enum):
    COMPUTER_SCIENCE = "computer_science"
    BIOMEDICAL = "biomedical"
    ECONOMICS = "economics"
    GENERAL_STEM = "general_stem"


class ReviewVerdict(str, Enum):
    ACCEPT = "accept"
    ACCEPT_WITH_RESERVATIONS = "accept_with_reservations"
    BORDERLINE = "borderline"
    REJECT = "reject"
