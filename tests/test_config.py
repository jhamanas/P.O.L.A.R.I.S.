"""Tests for config loading and validation."""

from pathlib import Path
import pytest
from antarctic_twin.config import load_params, load_station, param_value


from tests.conftest import REPO_ROOT as BASE


def test_load_params():
    params = load_params(BASE / "params.yaml")
    assert "diesel_energy_density" in params
    assert param_value(params, "diesel_energy_density") == 38.6


def test_load_station_bharati():
    cfg = load_station(BASE / "stations" / "bharati.yaml")
    assert cfg["name"] == "Bharati"
    assert cfg["crew"]["winter"] == 15
    assert len(cfg["zones"]) >= 2
    assert len(cfg["generators"]) == 3
    assert len(cfg["storage"]) >= 3  # fuel, water, food


def test_load_station_maitri():
    cfg = load_station(BASE / "stations" / "maitri.yaml")
    assert cfg["name"] == "Maitri"
    assert "renewables" in cfg  # Maitri has wind turbine


def test_station_validation_fails_on_missing_keys(tmp_path):
    """A config missing required keys should raise ValueError."""
    with pytest.raises(ValueError, match="missing required keys"):
        load_station.__wrapped__ if hasattr(load_station, '__wrapped__') else None
        # Create a temp file with bad config
        import yaml
        bad = {"name": "Bad"}  # missing crew, weather, zones, etc.
        f_path = tmp_path / "bad_station.yaml"
        with open(f_path, "w") as f:
            yaml.dump(bad, f)
        load_station(str(f_path))


def test_param_value_extracts_number():
    params = {"test": {"value": 42.5, "unit": "m", "source": "test"}}
    assert param_value(params, "test") == 42.5
