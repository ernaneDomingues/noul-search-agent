"""Logging for Noul: always to stderr (stdout carries the MCP stdio protocol), level from NOUL_LOG_LEVEL.

Libraries log through `noul.log.logger`; entry points (CLI, servers) call `configure()` once."""

import logging
import os
import sys

logger = logging.getLogger("noul")
logger.addHandler(logging.NullHandler())

_CONFIGURED = False


def configure(level=None):
    """Attach a single stderr handler to the `noul` logger. Level: argument > NOUL_LOG_LEVEL > WARNING."""
    global _CONFIGURED
    name = (level or os.environ.get("NOUL_LOG_LEVEL") or "WARNING").upper()
    logger.setLevel(getattr(logging, name, logging.WARNING))
    if _CONFIGURED:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    _CONFIGURED = True
    return logger
