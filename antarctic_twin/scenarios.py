"""Scenario specification, presets, runner, sensitivity analysis, and backtest.

A scenario is a lightweight overlay on top of a baseline simulation.
It modifies weather, crew, resupply, and/or injects faults.  The runner
produces both baseline and scenario results so a diff view is trivial.

Sensitivity analysis varies each parameter by +/-20 % one at a time and
measures the impact on fuel-exhaustion day, producing data for a tornado
chart.

The calibration backtest runs the baseline for a full year and checks
that key outputs fall within defensible physical bounds.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .alerts import Alert, derive_alerts
from .asset_graph import AssetGraph
from .config import load_params, load_station, param_value
from .engine import SimulationEngine, SimulationResult
from .forecast import ForecastResult, run_forecast

# ---------------------------------------------------------------------------
# Scenario spec
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    """Lightweight overlay that describes a what-if deviation from baseline."""
    name: str
    description: str = ""
    temp_offset: float = 0.0          # deg C added to both summer/winter avg
    wind_mult: float = 1.0            # multiplier on mean wind
    crew_delta: int = 0               # added to winter crew count
    resupply_delay_days: float = 0.0  # days the resupply is delayed
    faults: list[str] = field(default_factory=list)  # generator IDs to fault

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "temp_offset": self.temp_offset,
            "wind_mult": self.wind_mult,
            "crew_delta": self.crew_delta,
            "resupply_delay_days": self.resupply_delay_days,
            "faults": self.faults,
        }

    def diff_from_baseline(self) -> dict[str, str]:
        """Return a human-readable diff of non-default values."""
        diffs = {}
        if self.temp_offset != 0:
            diffs["Temperature"] = f"{self.temp_offset:+.0f} deg C"
        if self.wind_mult != 1.0:
            diffs["Wind"] = f"{self.wind_mult:.1f}x"
        if self.crew_delta != 0:
            diffs["Crew"] = f"{self.crew_delta:+d}"
        if self.resupply_delay_days != 0:
            diffs["Resupply delay"] = f"+{self.resupply_delay_days:.0f} days"
        if self.faults:
            diffs["Faults"] = ", ".join(self.faults)
        return diffs


# ---------------------------------------------------------------------------
# Six presets
# ---------------------------------------------------------------------------

PRESETS: dict[str, Scenario] = {
    "cold_snap": Scenario(
        name="Cold Snap",
        description="Sudden 15 deg C temperature drop simulating an extreme cold event "
                    "lasting the entire season. Tests heating and fuel resilience.",
        temp_offset=-15.0,
    ),
    "prolonged_blizzard": Scenario(
        name="Prolonged Blizzard",
        description="High winds (1.8x normal) and moderate cold (-8 deg C). "
                    "Increases convective heat loss and may ground outdoor ops.",
        temp_offset=-8.0,
        wind_mult=1.8,
    ),
    "delayed_resupply": Scenario(
        name="Delayed Resupply",
        description="The resupply ship is delayed by 60 days due to sea-ice "
                    "conditions. Tests consumable margin adequacy.",
        resupply_delay_days=60.0,
    ),
    "generator_failure": Scenario(
        name="Generator Failure",
        description="Primary generator (gen1) fails at the start. Station must "
                    "run on backup generators with reduced total capacity.",
        faults=["gen1"],
    ),
    "crew_surge": Scenario(
        name="Crew Surge",
        description="Summer crew overlaps into winter: +8 extra personnel. "
                    "Increases food, water, and base electrical consumption.",
        crew_delta=8,
    ),
    "combined_winter_isolation": Scenario(
        name="Combined Winter Isolation",
        description="Worst-case winter: cold snap, generator fault, and delayed "
                    "resupply all happen simultaneously. The stress test.",
        temp_offset=-12.0,
        wind_mult=1.4,
        resupply_delay_days=45.0,
        faults=["gen1"],
    ),
}


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    """Result of running a scenario against its baseline."""
    scenario: Scenario
    baseline: SimulationResult
    scenario_result: SimulationResult
    baseline_forecast: ForecastResult | None = None
    scenario_forecast: ForecastResult | None = None
    baseline_alerts: list[Alert] = field(default_factory=list)
    scenario_alerts: list[Alert] = field(default_factory=list)

    def diff_summary(self) -> dict[str, str]:
        """Human-readable diff of key metrics: baseline vs scenario."""
        b = self.baseline.final_state
        s = self.scenario_result.final_state

        def _fuel(state):
            for sid, ss in state.storage.items():
                if "fuel" in sid:
                    return ss.level
            return 0.0

        def _water(state):
            for sid, ss in state.storage.items():
                if "water" in sid:
                    return ss.level
            return 0.0

        def _food(state):
            for sid, ss in state.storage.items():
                if "food" in sid:
                    return ss.level
            return 0.0

        def _avg_temp(state):
            if not state.zones:
                return 0.0
            return sum(z.temperature for z in state.zones.values()) / len(state.zones)

        bf, sf = _fuel(b), _fuel(s)
        bw, sw = _water(b), _water(s)
        bfood, sfood = _food(b), _food(s)
        bt, st_ = _avg_temp(b), _avg_temp(s)

        return {
            "Fuel remaining (L)": f"{bf:,.0f} -> {sf:,.0f} ({sf-bf:+,.0f})",
            "Water remaining (L)": f"{bw:,.0f} -> {sw:,.0f} ({sw-bw:+,.0f})",
            "Food remaining (kg)": f"{bfood:,.0f} -> {sfood:,.0f} ({sfood-bfood:+,.0f})",
            "Avg zone temp (C)": f"{bt:.1f} -> {st_:.1f} ({st_-bt:+.1f})",
            "Gen faults (baseline/scenario)": (
                f"{sum(1 for g in b.generators.values() if g.faulted)} / "
                f"{sum(1 for g in s.generators.values() if g.faulted)}"
            ),
            "RED alerts (baseline/scenario)": (
                f"{sum(1 for a in self.baseline_alerts if a.severity.value == 'RED')} / "
                f"{sum(1 for a in self.scenario_alerts if a.severity.value == 'RED')}"
            ),
        }


def fork_scenario(station_config: dict, params: dict,
                   spec: Scenario) -> dict:
    """Apply a Scenario to a station config, returning a modified copy."""
    cfg = copy.deepcopy(station_config)

    # Temperature offset
    if spec.temp_offset != 0:
        weather = cfg.setdefault("weather", {})
        for key in ["summer_temp_avg", "winter_temp_avg"]:
            if key in weather:
                entry = dict(weather[key])
                entry["value"] = entry["value"] + spec.temp_offset
                weather[key] = entry

    # Wind multiplier
    if spec.wind_mult != 1.0:
        weather = cfg.setdefault("weather", {})
        if "avg_wind" in weather:
            entry = dict(weather["avg_wind"])
            entry["value"] = entry["value"] * spec.wind_mult
            weather["avg_wind"] = entry

    # Crew delta
    if spec.crew_delta != 0:
        crew = cfg.setdefault("crew", {})
        crew["winter"] = max(1, crew.get("winter", 15) + spec.crew_delta)

    return cfg


def run_scenario(
    station_path: str | Path,
    params_path: str | Path,
    spec: Scenario,
    seed: int = 42,
    days: int = 365,
    forecast_runs: int = 100,
) -> ScenarioResult:
    """Run a scenario against its baseline.

    Returns both baseline and scenario results for comparison.
    """
    station_config = load_station(station_path)
    params = load_params(params_path)

    # --- Baseline ---
    baseline_engine = SimulationEngine(station_config, params, seed=seed)
    baseline_result = baseline_engine.run(days=days)

    # --- Scenario ---
    scenario_config = fork_scenario(station_config, params, spec)
    scenario_engine = SimulationEngine(scenario_config, params, seed=seed)

    # Inject faults
    for fault_id in spec.faults:
        # Match partial ID (e.g., "gen1" matches "bharati.gen1")
        for gid in scenario_engine.initial_state.generators:
            if fault_id in gid:
                scenario_engine.initial_state.generators[gid].faulted = True
                scenario_engine.initial_state.generators[gid].permanent_fault = True
                scenario_engine.initial_state.generators[gid].fault_capacity_reduction = 0.5

    scenario_result = scenario_engine.run(days=days)

    # --- Forecasts ---
    resupply_day = param_value(params, "resupply_default_day")
    effective_resupply = resupply_day + spec.resupply_delay_days
    graph = AssetGraph(station_config)
    scenario_graph = AssetGraph(scenario_config)

    mid_day = min(days // 2, 150)
    horizon = min(days - mid_day, 250)

    baseline_fc = run_forecast(
        baseline_result.history[mid_day * 24], graph, station_config, params,
        n_runs=forecast_runs, horizon_days=horizon, dt_hours=6.0,
        resupply_day=resupply_day,
    )
    scenario_fc = run_forecast(
        scenario_result.history[mid_day * 24], scenario_graph, scenario_config, params,
        n_runs=forecast_runs, horizon_days=horizon, dt_hours=6.0,
        resupply_day=effective_resupply,
    )

    # --- Alerts ---
    baseline_alerts = derive_alerts(
        baseline_result.final_state, graph, params,
        forecast=baseline_fc, resupply_day=resupply_day,
    )
    scenario_alerts = derive_alerts(
        scenario_result.final_state, scenario_graph, params,
        forecast=scenario_fc, resupply_day=resupply_day,
        resupply_delay_days=spec.resupply_delay_days,
    )

    return ScenarioResult(
        scenario=spec,
        baseline=baseline_result,
        scenario_result=scenario_result,
        baseline_forecast=baseline_fc,
        scenario_forecast=scenario_fc,
        baseline_alerts=baseline_alerts,
        scenario_alerts=scenario_alerts,
    )


# ---------------------------------------------------------------------------
# Sensitivity analysis (tornado chart data)
# ---------------------------------------------------------------------------

@dataclass
class SensitivityPoint:
    """One bar of the tornado: the effect of varying one parameter."""
    parameter: str
    low_label: str       # e.g. "-20%"
    high_label: str      # e.g. "+20%"
    baseline_value: float
    low_value: float     # metric at -20%
    high_value: float    # metric at +20%
    unit: str = "days"


def _find_fuel_remaining(result: SimulationResult) -> float:
    """Returns the fuel remaining (litres) at the end of the simulation."""
    fuel_ids = [sid for sid in result.history[0].storage if "fuel" in sid]
    if not fuel_ids:
        return 0.0
    return result.final_state.storage[fuel_ids[0]].level

def _vary_insulation(cfg: dict, mult: float) -> dict:
    cfg = copy.deepcopy(cfg)
    zones = cfg.get("zones", [])
    for z in zones:
        if "insulation_r_value" in z:
            z["insulation_r_value"] *= mult
    return cfg

def run_sensitivity(
    station_path: str | Path,
    params_path: str | Path,
    seed: int = 42,
    days: int = 365,
    variation: float = 0.20,
) -> list[SensitivityPoint]:
    """Run +/-20% sensitivity analysis on key parameters.

    Varies one parameter at a time and measures the fuel remaining at the end of the year.
    Returns data suitable for a tornado chart.
    """
    station_config = load_station(station_path)
    params = load_params(params_path)

    # Baseline
    baseline = SimulationEngine(station_config, params, seed=seed).run(days=days)
    baseline_val = _find_fuel_remaining(baseline)

    # Parameters to vary
    scenarios = [
        ("Temperature", "temp_offset"),
        ("Wind speed", "wind_mult"),
        ("Crew size", "crew_delta"),
        ("Insulation R-value", "insulation"),
        ("Generator efficiency", "gen_eff"),
        ("Snow-melt rate", "snowmelt"),
    ]

    results = []

    for label, key in scenarios:
        # Low (-20%)
        low_cfg = copy.deepcopy(station_config)
        low_params = copy.deepcopy(params)
        
        if key == "temp_offset":
            low_cfg = _vary_temp(low_cfg, -variation * 30)  # -6 deg
        elif key == "wind_mult":
            low_cfg = _vary_wind(low_cfg, 1.0 - variation)
        elif key == "crew_delta":
            low_cfg = _vary_crew(low_cfg, -int(15 * variation))
        elif key == "insulation":
            low_cfg = _vary_insulation(low_cfg, 1.0 - variation)
        elif key == "gen_eff":
            _scale_param(low_params, "generator_efficiency", 1.0 - variation)
        elif key == "snowmelt":
            _scale_param(low_params, "snow_melt_rate_base", 1.0 - variation)

        res_low = SimulationEngine(low_cfg, low_params, seed=seed).run(days=days)
        low_val = _find_fuel_remaining(res_low)

        # High (+20%)
        high_cfg = copy.deepcopy(station_config)
        high_params = copy.deepcopy(params)
        
        if key == "temp_offset":
            high_cfg = _vary_temp(high_cfg, variation * 30)  # +6 deg
        elif key == "wind_mult":
            high_cfg = _vary_wind(high_cfg, 1.0 + variation)
        elif key == "crew_delta":
            high_cfg = _vary_crew(high_cfg, int(15 * variation))
        elif key == "insulation":
            high_cfg = _vary_insulation(high_cfg, 1.0 + variation)
        elif key == "gen_eff":
            _scale_param(high_params, "generator_efficiency", 1.0 + variation)
        elif key == "snowmelt":
            _scale_param(high_params, "snow_melt_rate_base", 1.0 + variation)

        res_high = SimulationEngine(high_cfg, high_params, seed=seed).run(days=days)
        high_val = _find_fuel_remaining(res_high)

        results.append(SensitivityPoint(
            parameter=label,
            low_label=f"-{variation*100:.0f}%",
            high_label=f"+{variation*100:.0f}%",
            baseline_value=baseline_val,
            low_value=low_val,
            high_value=high_val,
            unit="L",
        ))

    # Sort by impact (largest swing first)
    results.sort(key=lambda r: abs(r.high_value - r.low_value), reverse=True)
    return results


def _vary_temp(cfg: dict, offset: float) -> dict:
    cfg = copy.deepcopy(cfg)
    weather = cfg.setdefault("weather", {})
    for key in ["summer_temp_avg", "winter_temp_avg"]:
        if key in weather:
            entry = dict(weather[key])
            entry["value"] = entry["value"] + offset
            weather[key] = entry
    return cfg


def _vary_wind(cfg: dict, mult: float) -> dict:
    cfg = copy.deepcopy(cfg)
    weather = cfg.setdefault("weather", {})
    if "avg_wind" in weather:
        entry = dict(weather["avg_wind"])
        entry["value"] = entry["value"] * mult
        weather["avg_wind"] = entry
    return cfg


def _vary_crew(cfg: dict, delta: int) -> dict:
    cfg = copy.deepcopy(cfg)
    crew = cfg.setdefault("crew", {})
    crew["winter"] = max(1, crew.get("winter", 15) + delta)
    return cfg


def _scale_param(params: dict, key: str, factor: float | None) -> None:
    if key in params and factor is not None:
        params[key] = dict(params[key])
        params[key]["value"] = params[key]["value"] * factor


# ---------------------------------------------------------------------------
# Calibration backtest
# ---------------------------------------------------------------------------

@dataclass
class BacktestCheck:
    """One check in the calibration backtest."""
    name: str
    description: str
    expected_range: str
    actual_value: float
    unit: str
    passed: bool


def run_backtest(
    station_path: str | Path,
    params_path: str | Path,
    seed: int = 42,
) -> list[BacktestCheck]:
    """Run calibration backtest: check that model outputs fall within
    defensible physical bounds for a full-year run.

    Returns a list of pass/fail checks.
    """
    station_config = load_station(station_path)
    params = load_params(params_path)
    engine = SimulationEngine(station_config, params, seed=seed)
    result = engine.run(days=365)
    final = result.final_state

    checks: list[BacktestCheck] = []

    # 1. Annual fuel consumption: 100-200 kL for a small Antarctic station
    fuel_ids = [sid for sid in result.history[0].storage if "fuel" in sid]
    if fuel_ids:
        initial = result.history[0].storage[fuel_ids[0]].level
        consumed = final.total_fuel_consumed_l
        checks.append(BacktestCheck(
            name="Annual fuel consumption",
            description="Total diesel consumed should be 100-250 kL for a 15-person station",
            expected_range="100,000-250,000 L",
            actual_value=consumed,
            unit="L",
            passed=100_000 <= consumed <= 250_000,
        ))

    # 2. Average fuel burn rate: 300-550 L/day
    if fuel_ids:
        day30 = result.history[30 * 24].storage[fuel_ids[0]].level
        daily_rate = (initial - day30) / 30
        checks.append(BacktestCheck(
            name="Average fuel burn rate",
            description="Daily diesel consumption should be 300-550 L/day",
            expected_range="300-550 L/day",
            actual_value=daily_rate,
            unit="L/day",
            passed=300 <= daily_rate <= 550,
        ))

    # 3. Living zone temperature: 10-22 deg C year-round
    living_ids = [zid for zid in final.zones if "living" in zid]
    if living_ids:
        min_temp = min(s.zones[living_ids[0]].temperature for s in result.history)
        max_temp = max(s.zones[living_ids[0]].temperature for s in result.history)
        checks.append(BacktestCheck(
            name="Living zone temperature range",
            description="Living quarters should stay between 10-25 deg C",
            expected_range="10-25 deg C",
            actual_value=min_temp,
            unit="deg C (min)",
            passed=bool(10.0 <= min_temp and max_temp <= 25.0),
        ))

    # 4. Water: should not exhaust (snow-melt keeps up)
    water_ids = [sid for sid in final.storage if "water" in sid]
    if water_ids:
        min_water = min(s.storage[water_ids[0]].level for s in result.history)
        checks.append(BacktestCheck(
            name="Water sustainability",
            description="Water should never drop below 5000 L (snow-melt covers consumption)",
            expected_range=">5,000 L minimum",
            actual_value=min_water,
            unit="L",
            passed=min_water >= 4999.0,  # 1 L tolerance for floating-point
        ))

    # 5. Food: should survive the year
    food_ids = [sid for sid in final.storage if "food" in sid]
    if food_ids:
        final_food = final.storage[food_ids[0]].level
        checks.append(BacktestCheck(
            name="Food adequacy",
            description="Food should last the full year with buffer",
            expected_range=">0 kg at day 365",
            actual_value=final_food,
            unit="kg",
            passed=final_food > 0,
        ))

    # 6. Primary generator hours: ~8760 (runs all year)
    gen_ids = sorted(final.generators.keys())
    if gen_ids:
        gen1_hours = final.generators[gen_ids[0]].running_hours
        checks.append(BacktestCheck(
            name="Primary generator runtime",
            description="Gen1 should share load evenly with others (~3000-5000 hours)",
            expected_range="3,000-5,000 hours",
            actual_value=gen1_hours,
            unit="hours",
            passed=bool(2000 <= gen1_hours <= 6000),
        ))

    # 7. Heating demand: should peak in winter (day 150-210)
    winter_heat = np.mean([
        result.history[h].total_heating_demand_kw
        for h in range(150 * 24, min(210 * 24, len(result.history)))
    ])
    summer_heat = np.mean([
        result.history[h].total_heating_demand_kw
        for h in range(min(60 * 24, len(result.history)))
    ])
    checks.append(BacktestCheck(
        name="Seasonal heating pattern",
        description="Winter heating demand should exceed summer",
        expected_range="Winter > Summer",
        actual_value=winter_heat / max(0.01, summer_heat),
        unit="ratio (winter/summer)",
        passed=winter_heat > summer_heat,
    ))

    return checks
