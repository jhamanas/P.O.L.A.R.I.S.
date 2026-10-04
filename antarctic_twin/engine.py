"""Simulation engine: drives the step loop, manages history, supports replay.

The engine wires together config → asset graph → weather → step loop.
It stores the full state history for later analysis and visualization.

Phase 1 additions:
  - Passes seeded RNG values for fault injection (deterministic faults)
  - Separate fault RNG stream so faults don't perturb weather sequence
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .asset_graph import AssetGraph
from .config import load_params, load_station
from .state import StationState, initialize_state, step
from .types import AssetType, Environment
from .weather import WeatherGenerator


@dataclass
class SimulationResult:
    """Container for a completed simulation run."""
    station_name: str
    seed: int
    dt_hours: float
    days: int
    history: list[StationState]
    weather_history: list[Environment]

    @property
    def n_steps(self) -> int:
        return len(self.history) - 1

    @property
    def final_state(self) -> StationState:
        return self.history[-1]


class SimulationEngine:
    """Runs the Antarctic station simulation.

    Usage:
        engine = SimulationEngine(station_config, params, seed=42)
        result = engine.run(days=365)
    """

    def __init__(self, station_config: dict[str, Any],
                 params_config: dict[str, Any],
                 seed: int = 42):
        self.station_config = station_config
        self.params = params_config
        self.seed = seed
        self.graph = AssetGraph(station_config)

        # Initialize state
        crew = station_config["crew"]["winter"]
        self.initial_state = initialize_state(self.graph, crew)

    def run(self, days: int = 365, dt_hours: float = 1.0) -> SimulationResult:
        """Run the simulation for a given number of days.

        Crew count automatically switches between summer and winter
        based on day-of-year: summer crew during days [0,60) and [320,365)
        (Antarctic summer / resupply season), winter crew otherwise.

        Args:
            days: Number of days to simulate.
            dt_hours: Timestep in hours.

        Returns:
            SimulationResult with full state and weather history.
        """
        # Re-initialize RNGs to ensure run() is deterministic and repeatable
        self.weather = WeatherGenerator(
            station_weather=self.station_config["weather"],
            params=self.params,
            seed=self.seed,
        )
        self.fault_rng = np.random.default_rng(self.seed + 1_000_000)
        
        state = self.initial_state.copy()
        history: list[StationState] = [state]
        weather_history: list[Environment] = []

        gen_ids = [g.id for g in self.graph.get_by_type(AssetType.GENERATOR)]
        total_steps = int(days * 24 / dt_hours)

        # Crew switching: read summer/winter counts from config
        crew_cfg = self.station_config.get("crew", {})
        summer_crew = crew_cfg.get("summer", crew_cfg.get("winter", 15))
        winter_crew = crew_cfg.get("winter", 15)

        # Resupply day
        from .config import param_value
        resupply_cfg = self.station_config.get("resupply", {})
        resupply_day = float(
            resupply_cfg.get(
                "nominal_day",
                param_value(self.params, "resupply_default_day"),
            )
        )

        # Treat the resupply event as a cumulative simulation day.
        # This preserves the normal day-350 refill while allowing delayed
        # scenarios such as day 410 to represent a genuine later arrival.
        next_resupply_day = resupply_day

        while next_resupply_day <= state.time_hours / 24.0:
            next_resupply_day += 365.0

        for i in range(total_steps):
            current_sim_day = state.time_hours / 24.0
            day_of_year = current_sim_day % 365.25

            # Auto crew switch: Antarctic summer = Nov-Feb (days 0-60, 320-365)
            is_summer = day_of_year < 60 or day_of_year >= 320
            target_crew = summer_crew if is_summer else winter_crew
            if state.crew_count != target_crew:
                state = state.copy()
                state.crew_count = target_crew

            # Apply resupply when the simulation crosses the cumulative
            # resupply event date.
            step_end_day = current_sim_day + dt_hours / 24.0
            crossed_resupply = current_sim_day < next_resupply_day <= step_end_day

            if crossed_resupply:
                state = state.copy()

                # Refill all storage assets to their configured
                # initial/capacity level.
                for asset in self.graph.get_by_type(AssetType.STORAGE):
                    if asset.id in state.storage:
                        target_level = asset.params.get(
                            "initial_level_l",
                            asset.params.get("capacity_l", 0.0),
                        )

                        # Food uses kilograms.
                        if "initial_level_kg" in asset.params:
                            target_level = asset.params["initial_level_kg"]
                        elif "capacity_kg" in asset.params:
                            target_level = asset.params["capacity_kg"]

                        state.storage[asset.id].level = target_level

                # Schedule the next annual resupply event.
                next_resupply_day += 365.0

            # Generate weather
            env = self.weather.get_weather(day_of_year, dt_hours)
            weather_history.append(env)

            # Generate fault RNG values (one per generator per step)
            fault_rng_values = {
                gid: float(self.fault_rng.random()) for gid in gen_ids
            }

            # Step
            state = step(state, env, dt_hours, self.graph, self.params,
                         fault_rng_values=fault_rng_values)
            history.append(state)

        return SimulationResult(
            station_name=self.station_config["name"],
            seed=self.seed,
            dt_hours=dt_hours,
            days=days,
            history=history,
            weather_history=weather_history,
        )


def run_station(station_path: str | Path, params_path: str | Path = "params.yaml",
                seed: int = 42, days: int = 365) -> SimulationResult:
    """Convenience function: load config and run a full simulation."""
    station_config = load_station(station_path)
    params = load_params(params_path)
    engine = SimulationEngine(station_config, params, seed=seed)
    return engine.run(days=days)