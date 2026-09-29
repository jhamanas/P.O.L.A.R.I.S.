"""Antarctic Station Digital Twin -- core package."""

from .types import AssetType, Environment
from .config import load_params, load_station, param_value
from .asset_graph import AssetGraph, Asset
from .state import (
    StationState, ZoneState, GeneratorState, StorageState, BatteryState,
    initialize_state, step,
)
from .energy import (
    dispatch_energy, wind_turbine_power, solar_panel_power, battery_dispatch,
    update_condition, check_fault,
    EnergyDispatchResult, GeneratorDispatchResult, BatteryResult,
)
from .weather import WeatherGenerator
from .engine import SimulationEngine, SimulationResult, run_station
from .forecast import run_forecast, ForecastResult, ConsumableForecast
from .alerts import derive_alerts, Alert, AlertSeverity, AlertCategory
from .scenarios import (
    ScenarioSpec, PRESETS, run_scenario, ScenarioResult,
    run_sensitivity, SensitivityPoint,
    run_backtest, BacktestCheck,
)
from .database import (
    Role, User, AuditLogger, AuditEntry,
    check_permission, get_provenance, ROLE_PERMISSIONS,
)
from .interfaces import DataSource, YamlDataSource

__all__ = [
    # Types
    "AssetType", "Environment",
    # Config
    "load_params", "load_station", "param_value",
    # Asset graph
    "AssetGraph", "Asset",
    # State
    "StationState", "ZoneState", "GeneratorState", "StorageState", "BatteryState",
    "initialize_state", "step",
    # Energy
    "dispatch_energy", "wind_turbine_power", "solar_panel_power", "battery_dispatch",
    "update_condition", "check_fault",
    "EnergyDispatchResult", "GeneratorDispatchResult", "BatteryResult",
    # Weather
    "WeatherGenerator",
    # Engine
    "SimulationEngine", "SimulationResult", "run_station",
    # Forecast
    "run_forecast", "ForecastResult", "ConsumableForecast",
    # Alerts
    "derive_alerts", "Alert", "AlertSeverity", "AlertCategory",
    # Scenarios
    "ScenarioSpec", "PRESETS", "run_scenario", "ScenarioResult",
    "run_sensitivity", "SensitivityPoint",
    "run_backtest", "BacktestCheck",
    # Database & RBAC
    "Role", "User", "AuditLogger", "AuditEntry",
    "check_permission", "get_provenance", "ROLE_PERMISSIONS",
    # Data interface
    "DataSource", "YamlDataSource",
]
