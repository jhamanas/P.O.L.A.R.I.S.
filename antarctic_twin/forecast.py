"""Monte Carlo forecaster for consumable exhaustion dates.

Runs N simulations from the current state forward with different weather
seeds, extracting P10/P50/P90 exhaustion dates for each consumable and
margin_days against the resupply window.

The forecaster is vectorised across runs (each run is independent) and
uses a simplified forward model for speed: it re-uses the full step()
engine but with shorter timesteps if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .asset_graph import AssetGraph
from .config import param_value
from .state import StationState, step
from .types import AssetType
from .weather import WeatherGenerator


@dataclass
class ConsumableForecast:
    """Forecast for a single consumable (fuel, water, or food)."""
    asset_id: str
    commodity: str
    current_level: float
    unit: str  # "L" or "kg"

    # Exhaustion day-of-year for each Monte Carlo run (NaN if doesn't exhaust)
    exhaustion_days: np.ndarray  # shape (n_runs,)

    # Percentile exhaustion dates
    p10_days: float  # 10th percentile (optimistic — exhausts later)
    p50_days: float  # median
    p90_days: float  # 90th percentile (pessimistic — exhausts earlier)

    # Margin against resupply
    resupply_day: float
    margin_p10: float  # positive = safe, negative = runs out before resupply
    margin_p50: float
    margin_p90: float

    # Depletion rate (current)
    depletion_rate_per_day: float

    @property
    def fraction_exhausting_before_resupply(self) -> float:
        """Fraction of Monte Carlo runs where exhaustion occurs before resupply."""
        # np.nan means it never exhausted.
        # We need to count runs that DID exhaust AND did so before resupply,
        # divided by the TOTAL number of runs (len(self.exhaustion_days)).
        exhausted = ~np.isnan(self.exhaustion_days)
        exhausted_before = (self.exhaustion_days < self.resupply_day) & exhausted
        return float(np.sum(exhausted_before) / len(self.exhaustion_days))


@dataclass
class ForecastResult:
    """Complete forecast result from a Monte Carlo run."""
    n_runs: int
    forecast_horizon_days: int
    start_day: float
    consumables: dict[str, ConsumableForecast]  # keyed by asset_id

    # Per-run fuel level at end of forecast (for fan charts)
    fuel_trajectories: np.ndarray | None = None  # shape (n_runs, n_days)
    water_trajectories: np.ndarray | None = None
    food_trajectories: np.ndarray | None = None


def run_forecast(
    state: StationState,
    graph: AssetGraph,
    station_config: dict[str, Any],
    params: dict[str, Any],
    n_runs: int = 500,
    horizon_days: int = 180,
    dt_hours: float = 6.0,
    base_seed: int = 10000,
    resupply_day: float | None = None,
    store_trajectories: bool = True,
) -> ForecastResult:
    """Run Monte Carlo forecast from current state.

    Args:
        state: Current station state to forecast from.
        graph: Asset graph.
        station_config: Station configuration dict.
        params: Global parameters.
        n_runs: Number of Monte Carlo runs.
        horizon_days: Days to forecast forward.
        dt_hours: Timestep for forecast (coarser = faster).
        base_seed: Base seed; each run uses base_seed + i.
        resupply_day: Day of year for resupply. If None, read from config.
        store_trajectories: Whether to store daily level trajectories.

    Returns:
        ForecastResult with per-consumable P10/P50/P90.
    """
    if resupply_day is None:
        resupply_cfg = station_config.get("resupply", {})
        resupply_day = float(resupply_cfg.get("nominal_day",
                             param_value(params, "resupply_default_day")))

    # --- Convert everything to cumulative days from day 0 of the sim year ---
    # state.time_hours is cumulative hours since sim start (day 0 = Jan 1).
    # start_cum is the cumulative day the forecast begins.
    start_cum = state.time_hours / 24.0

    # resupply_day is a day-of-year (e.g. 350).  Convert to cumulative day
    # that is the *first occurrence on or after* start_cum.
    # This handles the case where we start forecasting at day 200 and resupply
    # is day 350 (same year) as well as starting at day 300 with resupply
    # at day 30 (next year = cumulative day 395).
    resupply_cum = resupply_day
    while resupply_cum < start_cum:
        resupply_cum += 365.0

    steps_per_day = int(24.0 / dt_hours)
    total_steps = horizon_days * steps_per_day

    # Identify consumable storage assets
    fuel_ids = [a.id for a in graph.storage_by_commodity("fuel")]
    water_ids = [a.id for a in graph.storage_by_commodity("water")]
    food_ids = [a.id for a in graph.storage_by_commodity("food")]
    all_consumable_ids = fuel_ids + water_ids + food_ids

    # Pre-allocate result arrays
    exhaustion_days = {sid: np.full(n_runs, np.nan) for sid in all_consumable_ids}

    # Trajectory storage: daily samples
    trajectories = {}
    if store_trajectories:
        for sid in fuel_ids:
            trajectories[sid] = np.zeros((n_runs, horizon_days))
        for sid in water_ids:
            trajectories[sid] = np.zeros((n_runs, horizon_days))
        for sid in food_ids:
            trajectories[sid] = np.zeros((n_runs, horizon_days))

    # --- Run Monte Carlo ---
    for run_idx in range(n_runs):
        seed = base_seed + run_idx
        weather = WeatherGenerator(
            station_weather=station_config["weather"],
            params=params,
            seed=seed,
        )

        # Start from current state (deep copy)
        sim_state = state.copy()
        # Use a separate fault RNG for this run
        fault_rng = np.random.default_rng(seed + 2_000_000)

        exhausted = {sid: False for sid in all_consumable_ids}
        
        # Read crew config for scheduling
        crew_cfg = station_config.get("crew", {})
        summer_crew = crew_cfg.get("summer", crew_cfg.get("winter", 15))
        winter_crew = crew_cfg.get("winter", 15)

        for step_idx in range(total_steps):
            # day_of_year for weather (must be mod-365 for seasonal patterns)
            day_of_year = (sim_state.time_hours / 24.0) % 365.25
            
            # Auto crew switch: Antarctic summer = Nov-Feb (days 0-60, 320-365)
            is_summer = day_of_year < 60 or day_of_year >= 320
            target_crew = summer_crew if is_summer else winter_crew
            if sim_state.crew_count != target_crew:
                sim_state = sim_state.copy()
                sim_state.crew_count = target_crew
                
            env = weather.get_weather(day_of_year, dt_hours)

            # Generate fault RNG values
            gen_ids = [g.id for g in graph.get_by_type(AssetType.GENERATOR)]
            fault_rng_values = {gid: float(fault_rng.random()) for gid in gen_ids}

            sim_state = step(sim_state, env, dt_hours, graph, params,
                           fault_rng_values=fault_rng_values)

            # Check for exhaustion — use cumulative day, NOT mod-365
            current_cum_day = sim_state.time_hours / 24.0

            for sid in all_consumable_ids:
                if not exhausted[sid] and sim_state.storage[sid].level <= 0:
                    exhaustion_days[sid][run_idx] = current_cum_day
                    exhausted[sid] = True

            # Store trajectory (daily sample)
            if store_trajectories and step_idx % steps_per_day == 0:
                day_idx = step_idx // steps_per_day
                if day_idx < horizon_days:
                    for sid, traj in trajectories.items():
                        traj[run_idx, day_idx] = sim_state.storage[sid].level

    # --- Compute statistics ---
    consumable_forecasts = {}

    for sid in all_consumable_ids:
        days = exhaustion_days[sid]
        days_for_stats = days.copy()
        # Use a large finite number instead of np.inf, because np.percentile with inf returns nan
        large_finite = start_cum + horizon_days + 1000.0
        days_for_stats[np.isnan(days_for_stats)] = large_finite

        p10 = float(np.percentile(days_for_stats, 90))
        p50 = float(np.percentile(days_for_stats, 50))
        p90 = float(np.percentile(days_for_stats, 10))

        # If percentile is large, cap it at start_cum + horizon_days for display
        p10 = min(p10, start_cum + horizon_days)
        p50 = min(p50, start_cum + horizon_days)
        p90 = min(p90, start_cum + horizon_days)

        # Determine commodity and units
        commodity = "fuel" if sid in fuel_ids else "water" if sid in water_ids else "food"
        unit = "kg" if commodity == "food" else "L"

        # Current depletion rate (from first few MC runs, averaged)
        current_level = state.storage[sid].level
        if store_trajectories and sid in trajectories and n_runs > 0:
            # Average rate from now until resupply (or end of horizon if resupply > horizon)
            target_day_idx = min(horizon_days - 1, int(resupply_cum - start_cum))
            if target_day_idx > 0:
                day0_levels = trajectories[sid][:, 0]
                target_levels = trajectories[sid][:, target_day_idx]
                avg_rate = float(np.mean(day0_levels - target_levels)) / target_day_idx
                depletion_rate = max(0.0, avg_rate)
            else:
                depletion_rate = 0.0
        else:
            depletion_rate = 0.0

        # Margins are computed against cumulative resupply day
        consumable_forecasts[sid] = ConsumableForecast(
            asset_id=sid,
            commodity=commodity,
            current_level=current_level,
            unit=unit,
            exhaustion_days=days,
            p10_days=p10,
            p50_days=p50,
            p90_days=p90,
            resupply_day=resupply_cum,
            margin_p10=p10 - resupply_cum,
            margin_p50=p50 - resupply_cum,
            margin_p90=p90 - resupply_cum,
            depletion_rate_per_day=depletion_rate,
        )

    # Aggregate trajectories
    fuel_traj = None
    water_traj = None
    food_traj = None
    if store_trajectories:
        if fuel_ids:
            fuel_traj = trajectories[fuel_ids[0]]
        if water_ids:
            water_traj = trajectories[water_ids[0]]
        if food_ids:
            food_traj = trajectories[food_ids[0]]

    return ForecastResult(
        n_runs=n_runs,
        forecast_horizon_days=horizon_days,
        start_day=start_cum,
        consumables=consumable_forecasts,
        fuel_trajectories=fuel_traj,
        water_trajectories=water_traj,
        food_trajectories=food_traj,
    )

