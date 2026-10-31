"""Templates and metadata for polyglot code and experimental protocol scaffolding (Phase 5: #16, #21)."""

TARGET_ALIASES = {
    "auto": "auto",
    "pytorch": "pytorch",
    "torch": "pytorch",
    "python": "python",
    "statsmodels": "statsmodels",
    "econ": "statsmodels",
    "r": "r",
    "protocol": "clinical_protocol",
    "clinical_protocol": "clinical_protocol",
    "markdown": "clinical_protocol",
}

PYTORCH_DEPENDENCIES = ["torch>=2.0.0"]
STATSMODELS_DEPENDENCIES = ["numpy>=1.24", "pandas>=2.0", "statsmodels>=0.14", "linearmodels>=5.0"]
R_DEPENDENCIES = ["fixest", "sandwich", "lmtest"]
PROTOCOL_DEPENDENCIES = ["numpy>=1.24", "scipy>=1.10"]
