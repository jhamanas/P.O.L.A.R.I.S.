"""Simulation engine: drives the step loop, manages history, supports replay.

The engine wires together config → asset graph → weather → step loop.
It stores the full state history for later analysis and visualization.

Phase 1 additions:
  - Passes seeded RNG values for fault injection (deterministic faults)
  - Separate fault RNG stream so faults don't perturb weather sequence
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .asset_graph import AssetGraph
from .config import load_station, load_params
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

        # Weather generator (seeded)
        self.weather = WeatherGenerator(
            station_weather=station_config["weather"],
            params=params_config,
            seed=seed,
        )

        # Separate RNG stream for fault injection — independent of weather
        # so adding/removing generators doesn't change the weather sequence
        self.fault_rng = np.random.default_rng(seed + 1_000_000)

        # Initialize state
        crew = station_config["crew"]["winter"]
        self.initial_state = initialize_state(self.graph, crew)

    def run(self, days: int = 365, dt_hours: float = 1.0,
            crew_schedule: dict[str, int] | None = None) -> SimulationResult:
        """Run the simulation for a given number of days.

        Args:
            days: Number of days to simulate.
            dt_hours: Timestep in hours.
            crew_schedule: Optional dict mapping day ranges to crew counts.

        Returns:
            SimulationResult with full state and weather history.
        """
        state = self.initial_state.copy()
        history: list[StationState] = [state]
        weather_history: list[Environment] = []

        gen_ids = [g.id for g in self.graph.get_by_type(AssetType.GENERATOR)]
        total_steps = int(days * 24 / dt_hours)

        for i in range(total_steps):
            day_of_year = (state.time_hours / 24.0) % 365.25

            # Update crew count based on schedule
            if crew_schedule:
                current_day = state.time_hours / 24.0
                for range_str, count in crew_schedule.items():
                    start, end = map(float, range_str.split("-"))
                    if start <= (current_day % 365) < end:
                        state = state.copy()
                        state.crew_count = count
                        break

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
