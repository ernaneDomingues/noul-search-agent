"""Scientific macro-domains (#19)."""

from enum import Enum


class ScientificDomain(str, Enum):
    COMPUTER_SCIENCE = "computer_science"
    BIOMEDICAL = "biomedical"
    ECONOMICS = "economics"
    GENERAL_STEM = "general_stem"
