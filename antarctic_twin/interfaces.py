"""Data Source interface for platform hardening.

Abstracts how station configuration and parameters are loaded so the
twin engine is agnostic to the backing store.  The YamlDataSource
implementation reads from local YAML files; a future CloudDataSource
could read from a REST API or database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from pathlib import Path

from .config import load_station, load_params


class DataSource(ABC):
    """Abstract interface for loading digital twin configuration."""

    @abstractmethod
    def get_station_config(self, station_name: str) -> dict[str, Any]:
        """Load a station configuration by name."""
        ...

    @abstractmethod
    def get_global_params(self) -> dict[str, Any]:
        """Load global simulation parameters."""
        ...

    @abstractmethod
    def list_stations(self) -> list[str]:
        """Return available station names."""
        ...


class YamlDataSource(DataSource):
    """DataSource backed by local YAML files under a base directory.

    Expected layout::

        base_path/
          params.yaml
          stations/
            bharati.yaml
            maitri.yaml
    """

    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)

    def get_station_config(self, station_name: str) -> dict[str, Any]:
        path = self.base_path / "stations" / f"{station_name}.yaml"
        return load_station(path)

    def get_global_params(self) -> dict[str, Any]:
        return load_params(self.base_path / "params.yaml")

    def list_stations(self) -> list[str]:
        """Discover station YAML files and return their names."""
        stations_dir = self.base_path / "stations"
        if not stations_dir.exists():
            return []
        return sorted(
            p.stem for p in stations_dir.glob("*.yaml")
        )
