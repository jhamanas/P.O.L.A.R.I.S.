"""Shared types, enums and data structures for the Antarctic Station Digital Twin."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class AssetType(str, Enum):
    """Types of assets in the station asset graph."""
    GENERATOR = "generator"
    ZONE = "zone"
    STORAGE = "storage"
    EQUIPMENT = "equipment"
    RENEWABLE = "renewable"


@dataclass(frozen=True, slots=True)
class Environment:
    """Immutable snapshot of external conditions at a single timestep.

    Frozen so it cannot be accidentally mutated during a step.
    """
    temperature: float       # °C
    wind_speed: float        # m/s
    solar_irradiance: float  # W/m²
    is_storm: bool
    day_of_year: float       # fractional day (0-365)
    hour: float              # fractional hour (0-24)
