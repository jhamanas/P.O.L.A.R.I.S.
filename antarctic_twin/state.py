"""Station state and the core step() contract.

The step function is the heart of the simulation.  It takes the current
immutable state, an environment snapshot, and a timestep, and returns a
new state.  The pattern is:

    new_state = step(state, env, dt, graph, params)

Nothing is mutated in-place; every call produces a fresh StationState.
This makes replay, branching (scenarios), and Monte Carlo trivial.

Phase 1 implements:
  - Lumped RC thermal per zone with wind-driven UA and priority heating
  - Energy dispatch: renewables → battery → generators (merit order)
  - Fuel burn curve (load-dependent interpolation)
  - Consumables (fuel, water, food) with snow-melt energy coupling
  - Equipment running hours, condition score, fault flag
  - Waste heat recovery for space heating
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import copy
import math

from .types import AssetType, Environment
from .asset_graph import AssetGraph
from .config import param_value
from .energy import (
    dispatch_energy, update_condition, check_fault,
    EnergyDispatchResult,
)


# ---------------------------------------------------------------------------
# State dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ZoneState:
    """Thermal state of one zone."""
    temperature: float          # °C inside the zone
    heating_kw: float = 0.0     # current heating power applied
    heating_demand_kw: float = 0.0  # uncapped demand for reporting

@dataclass
class GeneratorState:
    """State of one generator."""
    running: bool = True
    load_fraction: float = 0.0          # 0-1, fraction of rated capacity
    running_hours: float = 0.0
    fuel_consumed_l: float = 0.0
    condition: float = 1.0              # 1.0 = perfect, 0.0 = dead
    faulted: bool = False               # active fault flag
    fault_capacity_reduction: float = 0.5  # fraction of capacity lost when faulted

@dataclass
class StorageState:
    """State of one storage tank / store."""
    level: float = 0.0  # L for fuel/water, kg for food

@dataclass
class BatteryState:
    """State of the battery bank."""
    soc_kwh: float = 0.0  # state of charge in kWh

@dataclass
class StationState:
    """Complete mutable state of one station at one point in time."""
    time_hours: float = 0.0
    crew_count: int = 15

    zones: dict[str, ZoneState] = field(default_factory=dict)
    generators: dict[str, GeneratorState] = field(default_factory=dict)
    storage: dict[str, StorageState] = field(default_factory=dict)
    battery: BatteryState = field(default_factory=BatteryState)

    # Aggregate bookkeeping
    total_heating_demand_kw: float = 0.0
    total_electrical_load_kw: float = 0.0
    total_generation_kw: float = 0.0
    renewable_generation_kw: float = 0.0
    battery_power_kw: float = 0.0
    waste_heat_kw: float = 0.0
    snow_melt_production_l: float = 0.0
    unmet_demand_kw: float = 0.0

    def copy(self) -> StationState:
        """Deep copy so mutations don't affect the original."""
        return copy.deepcopy(self)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def initialize_state(graph: AssetGraph, crew_count: int) -> StationState:
    """Create the initial StationState from the asset graph."""
    state = StationState(time_hours=0.0, crew_count=crew_count)

    for z in graph.get_by_type(AssetType.ZONE):
        state.zones[z.id] = ZoneState(
            temperature=z.params.get("target_temp", 18.0) - 2.0
        )

    for g in graph.get_by_type(AssetType.GENERATOR):
        state.generators[g.id] = GeneratorState(
            running=False,  # dispatch will decide
            load_fraction=0.0,
            condition=1.0,
            faulted=False,
        )

    for s in graph.get_by_type(AssetType.STORAGE):
        commodity = s.params.get("commodity", "")
        if commodity == "food":
            level = s.params.get("initial_level_kg", 0.0)
        elif commodity == "battery":
            # Battery SOC tracked separately
            state.battery.soc_kwh = s.params.get("initial_soc_kwh",
                                                   s.params.get("capacity_kwh", 0.0) * 0.8)
            level = 0.0  # not used for battery
        else:
            level = s.params.get("initial_level_l", 0.0)
        state.storage[s.id] = StorageState(level=level)

    return state


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------

def step(state: StationState, env: Environment, dt: float,
         graph: AssetGraph, params: dict[str, Any],
         fault_rng_values: dict[str, float] | None = None) -> StationState:
    """Advance station state by dt hours given environment conditions.

    This is the core contract.  It must:
      1. Never mutate the input state
      2. Return a new StationState
      3. Be deterministic given the same fault_rng_values
      4. Maintain conservation laws (energy, mass)

    Args:
        state: Current station state (not mutated).
        env: Environment snapshot for this timestep.
        dt: Timestep in hours.
        graph: Asset graph for this station.
        params: Global parameters dict.
        fault_rng_values: Pre-generated random values per generator for fault
                         checks.  Keyed by generator ID.  If None, no new
                         faults can occur (but existing faults persist).

    Returns:
        New StationState after dt hours.
    """
    s = state.copy()
    s.time_hours += dt
    dt_seconds = dt * 3600.0

    # ---- Lookup heating equipment capacity ----
    heating_cap_kw = 80.0
    for eq in graph.get_by_type(AssetType.EQUIPMENT):
        if "heating" in eq.id:
            heating_cap_kw = eq.params.get("capacity_kw", 80.0)
    heating_cap_w = heating_cap_kw * 1000.0

    # ---- 1. Thermal dynamics per zone (priority-based heating) ----
    wind_ua_coeff = param_value(params, "wind_ua_coefficient")
    rho = param_value(params, "air_density")
    cp = param_value(params, "specific_heat_air")

    # Compute UA and demand per zone, then allocate heating by priority
    zone_thermal: list[dict[str, Any]] = []
    zones_sorted = sorted(graph.get_by_type(AssetType.ZONE),
                          key=lambda z: -z.params.get("target_temp", 0.0))
    # Priority: highest target_temp first (living > lab > workshop)

    for zone_asset in zones_sorted:
        p = zone_asset.params
        target_temp = p.get("target_temp", 20.0)
        r_value = p.get("insulation_r_value", 3.0)
        envelope_area = p.get("envelope_area_m2", 500.0)
        volume = p.get("volume_m3", 500.0)

        ua_base = envelope_area / r_value
        ua_wind = wind_ua_coeff * envelope_area * env.wind_speed
        ua_total = ua_base + ua_wind
        thermal_mass = rho * volume * cp * 5.0
        demand_w = max(0.0, ua_total * (target_temp - env.temperature))

        zone_thermal.append({
            "id": zone_asset.id,
            "ua_total": ua_total,
            "thermal_mass": thermal_mass,
            "target_temp": target_temp,
            "demand_w": demand_w,
        })

    # Allocate heating: priority zones get heating first, up to their demand
    remaining_cap_w = heating_cap_w
    total_heating_demand = 0.0
    zone_heating_allocation: dict[str, float] = {}

    for zt in zone_thermal:
        allocated = min(zt["demand_w"], remaining_cap_w)
        zone_heating_allocation[zt["id"]] = allocated
        remaining_cap_w -= allocated
        total_heating_demand += zt["demand_w"] / 1000.0

    # Apply thermal dynamics with allocated heating
    for zt in zone_thermal:
        zs = s.zones[zt["id"]]
        heating_w = zone_heating_allocation[zt["id"]]
        ua = zt["ua_total"]
        C = zt["thermal_mass"]

        # Implicit Euler: T_new = (C·T_old + dt·(UA·T_amb + Q_heat)) / (C + dt·UA)
        numerator = C * zs.temperature + dt_seconds * (ua * env.temperature + heating_w)
        denominator = C + dt_seconds * ua
        zs.temperature = numerator / denominator
        zs.heating_kw = heating_w / 1000.0
        zs.heating_demand_kw = zt["demand_w"] / 1000.0

    s.total_heating_demand_kw = total_heating_demand

    # ---- 2. Compute electrical demand ----
    base_electrical_kw = 10.0 + 1.5 * s.crew_count

    # Snow-melt energy
    snowmelt_kw = 0.0
    for eq in graph.get_by_type(AssetType.EQUIPMENT):
        if "snowmelt" in eq.id:
            snowmelt_kw = eq.params.get("energy_kw", 10.0)
    base_electrical_kw += snowmelt_kw

    gen_efficiency = param_value(params, "generator_efficiency")
    waste_heat_recovery = param_value(params, "generator_waste_heat_recovery")

    # Estimate waste heat from running base load on generators
    est_waste_heat_kw = (
        base_electrical_kw / gen_efficiency * (1.0 - gen_efficiency) * waste_heat_recovery
    )
    # Heating shortfall that electric heaters must cover
    heating_shortfall_kw = max(0.0, total_heating_demand - est_waste_heat_kw)
    electric_heating_kw = heating_shortfall_kw * 0.5  # 50% electric backup
    total_demand_kw = base_electrical_kw + electric_heating_kw

    # ---- 3. Equipment condition & faults ----
    gen_mtbf = float(param_value(params, "generator_mtbf_hours")) if "generator_mtbf_hours" in params else 5000.0
    base_fault_rate = float(param_value(params, "generator_fault_rate")) if "generator_fault_rate" in params else 0.0001

    for gen_asset in graph.get_by_type(AssetType.GENERATOR):
        gs = s.generators[gen_asset.id]
        # Update condition based on running hours
        gs.condition = update_condition(gs.running_hours, gs.condition, gen_mtbf)

        # Check for new fault (only if not already faulted)
        if not gs.faulted and fault_rng_values and gen_asset.id in fault_rng_values:
            gs.faulted = check_fault(
                gs.condition, dt, fault_rng_values[gen_asset.id], base_fault_rate
            )

    # ---- 4. Energy dispatch (renewables → battery → generators) ----
    dispatch = dispatch_energy(
        total_demand_kw=total_demand_kw,
        env=env,
        dt_hours=dt,
        graph=graph,
        generator_states=s.generators,
        battery_soc=s.battery.soc_kwh,
        params=params,
    )

    # Update generator states from dispatch results
    for gen_id, gr in dispatch.gen_results.items():
        gs = s.generators[gen_id]
        gs.running = gr.running
        gs.load_fraction = gr.load_fraction
        if gr.running:
            gs.running_hours += dt
            gs.fuel_consumed_l += gr.fuel_used_l

    # Update battery
    s.battery.soc_kwh = dispatch.battery_soc_kwh

    # Record aggregates
    s.total_electrical_load_kw = dispatch.total_demand_kw
    s.total_generation_kw = dispatch.generator_kw
    s.renewable_generation_kw = dispatch.renewable_kw
    s.battery_power_kw = dispatch.battery_kw
    s.waste_heat_kw = dispatch.waste_heat_kw
    s.unmet_demand_kw = dispatch.unmet_demand_kw

    # ---- 5. Fuel storage deduction ----
    for store in graph.storage_by_commodity("fuel"):
        ss = s.storage[store.id]
        ss.level = max(0.0, ss.level - dispatch.total_fuel_consumed_l)

    # ---- 6. Water: snow-melt coupled to available energy ----
    water_rate = param_value(params, "water_consumption_per_capita") / 24.0
    water_consumed = water_rate * s.crew_count * dt

    # Snow-melt production scales with available power
    # If there's unmet demand, snow-melt plant may not run at full capacity
    snowmelt_base_rate = param_value(params, "snow_melt_rate_base") / 24.0  # L/hr
    if s.unmet_demand_kw > 0:
        # Reduce snow-melt proportionally to power shortfall
        snowmelt_fraction = max(0.0, 1.0 - s.unmet_demand_kw / max(1.0, snowmelt_kw))
    else:
        snowmelt_fraction = 1.0

    # Snow-melt also needs energy: convert L water to energy needed
    # Energy = mass × latent heat = (volume × density) × 334 kJ/kg
    snow_melt_energy_kj_per_l = param_value(params, "snow_melt_energy") / 1.0  # kJ/L (water density ~1 kg/L)
    max_melt_from_energy = (snowmelt_kw * 3600.0 * dt) / snow_melt_energy_kj_per_l  # L
    snow_melt = min(snowmelt_base_rate * dt * snowmelt_fraction, max_melt_from_energy)
    s.snow_melt_production_l = snow_melt

    for store in graph.storage_by_commodity("water"):
        ss = s.storage[store.id]
        capacity = store.params.get("capacity_l", 50000.0)
        ss.level = min(capacity, ss.level + snow_melt)
        ss.level = max(0.0, ss.level - water_consumed)

    # ---- 7. Food consumption ----
    food_rate = param_value(params, "food_consumption_per_capita") / 24.0
    food_consumed = food_rate * s.crew_count * dt

    for store in graph.storage_by_commodity("food"):
        ss = s.storage[store.id]
        ss.level = max(0.0, ss.level - food_consumed)

    return s
