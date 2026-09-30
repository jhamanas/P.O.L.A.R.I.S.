"""Configuration loading and validation for Antarctic Station Digital Twin.

Loads params.yaml (global parameters) and station configs, validates required
fields, and provides typed access helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise TypeError(f"{path} did not parse to a dict")
    return data


def load_params(path: Path | str = "params.yaml") -> dict[str, Any]:
    """Load global parameters file.  Returns raw dict."""
    return load_yaml(Path(path))


def load_station(path: Path | str) -> dict[str, Any]:
    """Load a station config and validate minimum required fields."""
    cfg = load_yaml(Path(path))
    _validate_station(cfg, path)
    return cfg


def param_value(params: dict[str, Any], key: str) -> float:
    """Extract the numeric value from a params entry like {value, unit, source}."""
    entry = params[key]
    if isinstance(entry, dict):
        return float(entry["value"])
    return float(entry)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_REQUIRED_STATION_KEYS = {"name", "crew", "weather", "zones", "generators", "storage"}


def _validate_station(cfg: dict[str, Any], path: Path | str) -> None:
    """Raise ValueError if required keys are missing from a station config."""
    missing = _REQUIRED_STATION_KEYS - set(cfg.keys())
    if missing:
        raise ValueError(
            f"Station config {path} missing required keys: {missing}"
        )
    # Validate that every asset list entry has an 'id'
    for section in ("zones", "generators", "storage"):
        for i, item in enumerate(cfg.get(section, [])):
            if "id" not in item:
                raise ValueError(
                    f"Station config {path}: {section}[{i}] missing 'id'"
                )
