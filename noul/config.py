"""Persistent configuration (config.json + env vars). Re-exported from the zero-dependency core."""
from noul_core import get_config, set_config

__all__ = ["get_config", "set_config"]
