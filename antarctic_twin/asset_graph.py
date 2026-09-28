"""Asset graph: hierarchical, typed asset registry with stable IDs.

The graph is built entirely from station YAML config.  Each asset has:
  - A stable string ID (e.g. "bharati.gen1")
  - A type (generator, zone, storage, equipment, renewable)
  - A label for display
  - A params dict with physical parameters
  - Optional parent/children for hierarchy

The graph supports lookup by ID, filtering by type, and enumeration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import AssetType


@dataclass
class Asset:
    """A single asset in the station graph."""
    id: str
    type: AssetType
    label: str
    params: dict[str, Any]
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)


class AssetGraph:
    """Registry of all assets for one station, built from config YAML.

    The station name is used as the root node; zones, generators, storage,
    equipment and renewables are children of the root.
    """

    def __init__(self, station_config: dict[str, Any]):
        self.station_name: str = station_config["name"]
        self.assets: dict[str, Asset] = {}

        root_id = self.station_name.lower()

        # --- Zones ---
        for z in station_config.get("zones", []):
            self._add(z["id"], AssetType.ZONE, z.get("label", z["id"]),
                      _strip_meta(z), parent=root_id)

        # --- Generators ---
        for g in station_config.get("generators", []):
            self._add(g["id"], AssetType.GENERATOR, g.get("label", g["id"]),
                      _strip_meta(g), parent=root_id)

        # --- Renewables ---
        for r in station_config.get("renewables", []):
            self._add(r["id"], AssetType.RENEWABLE, r.get("label", r["id"]),
                      _strip_meta(r), parent=root_id)

        # --- Storage ---
        for s in station_config.get("storage", []):
            self._add(s["id"], AssetType.STORAGE, s.get("label", s["id"]),
                      _strip_meta(s), parent=root_id)

        # --- Equipment ---
        for e in station_config.get("equipment", []):
            self._add(e["id"], AssetType.EQUIPMENT, e.get("label", e["id"]),
                      _strip_meta(e), parent=root_id)

    # ----- public API -----

    def get(self, asset_id: str) -> Asset:
        """Get asset by ID.  Raises KeyError if not found."""
        return self.assets[asset_id]

    def get_by_type(self, asset_type: AssetType) -> list[Asset]:
        """Return all assets of a given type."""
        return [a for a in self.assets.values() if a.type == asset_type]

    def all_ids(self) -> list[str]:
        """Return all asset IDs in insertion order."""
        return list(self.assets.keys())

    def storage_by_commodity(self, commodity: str) -> list[Asset]:
        """Return storage assets matching a commodity name (fuel, water, food)."""
        return [
            a for a in self.get_by_type(AssetType.STORAGE)
            if a.params.get("commodity") == commodity
        ]

    # ----- internal -----

    def _add(self, id: str, type: AssetType, label: str,
             params: dict[str, Any], parent: str | None = None) -> None:
        asset = Asset(id=id, type=type, label=label, params=params,
                      parent_id=parent)
        self.assets[id] = asset


def _strip_meta(d: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of dict with metadata keys (id, label, source, type) removed,
    leaving only physical parameters."""
    skip = {"id", "label", "source", "type"}
    return {k: v for k, v in d.items() if k not in skip}
