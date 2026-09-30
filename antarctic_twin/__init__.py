"""Antarctic Station Digital Twin -- core package."""

from .alerts import Alert, AlertCategory, AlertSeverity, derive_alerts
from .asset_graph import Asset, AssetGraph
from .config import load_params, load_station, param_value
from .database import (
    ROLE_PERMISSIONS,
    AuditEntry,
    AuditLogger,
    Role,
    User,
    check_permission,
    get_provenance,
)
from .energy import (
    BatteryResult,
    EnergyDispatchResult,
    GeneratorDispatchResult,
    battery_dispatch,
    check_fault,
    dispatch_energy,
    solar_panel_power,
    update_condition,
    wind_turbine_power,
)
from .engine import SimulationEngine, SimulationResult, run_station
from .forecast import ConsumableForecast, ForecastResult, run_forecast
from .interfaces import DataSource, YamlDataSource
from .scenarios import (
    PRESETS,
    BacktestCheck,
    Scenario,
    ScenarioResult,
    SensitivityPoint,
    run_backtest,
    run_scenario,
    run_sensitivity,
)
from .state import (
    BatteryState,
    GeneratorState,
    StationState,
    StorageState,
    ZoneState,
    initialize_state,
    step,
)
from .types import AssetType, Environment
from .weather import WeatherGenerator

__all__ = [
    "PRESETS",
    "ROLE_PERMISSIONS",
    "Alert",
    "AlertCategory",
    "AlertSeverity",
    "Asset",
    # Asset graph
    "AssetGraph",
    # Types
    "AssetType",
    "AuditEntry",
    "AuditLogger",
    "BacktestCheck",
    "BatteryResult",
    "BatteryState",
    "ConsumableForecast",
    # Data interface
    "DataSource",
    "EnergyDispatchResult",
    "Environment",
    "ForecastResult",
    "GeneratorDispatchResult",
    "GeneratorState",
    # Database & RBAC
    "Role",
    # Scenarios
    "Scenario",
    "ScenarioResult",
    "SensitivityPoint",
    # Engine
    "SimulationEngine",
    "SimulationResult",
    # State
    "StationState",
    "StorageState",
    "User",
    # Weather
    "WeatherGenerator",
    "YamlDataSource",
    "ZoneState",
    "battery_dispatch",
    "check_fault",
    "check_permission",
    # Alerts
    "derive_alerts",
    # Energy
    "dispatch_energy",
    "get_provenance",
    "initialize_state",
    # Config
    "load_params",
    "load_station",
    "param_value",
    "run_backtest",
    # Forecast
    "run_forecast",
    "run_scenario",
    "run_sensitivity",
    "run_station",
    "solar_panel_power",
    "step",
    "update_condition",
    "wind_turbine_power",
]
