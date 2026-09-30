"""Energy dispatch: renewables → battery → generators.

Implements the merit-order dispatch for an Antarctic station:
  1. Calculate total electrical demand
  2. Harvest available renewable energy (wind, solar)
  3. Discharge battery if renewables insufficient
  4. Dispatch generators (fewest possible) for remaining demand
  5. Charge battery from excess renewable/generator capacity
  6. Compute waste heat from generators for space heating

All functions are pure — no hidden state, no randomness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .types import AssetType, Environment
from .asset_graph import AssetGraph, Asset
from .config import param_value


# ---------------------------------------------------------------------------
# Renewable energy models
# ---------------------------------------------------------------------------

def wind_turbine_power(wind_speed: float, asset: Asset) -> float:
    """Calculate wind turbine output (kW) from wind speed using a cubic power curve.

    Uses cut-in, rated, and cut-out wind speeds from asset params.
    Power curve:  0 below cut-in, cubic ramp to rated, rated between
    rated and cut-out, 0 above cut-out.
    """
    p = asset.params
    cut_in = p.get("cut_in_wind", 3.5)    # m/s
    rated = p.get("rated_wind", 12.0)      # m/s
    cut_out = p.get("cut_out_wind", 25.0)  # m/s
    capacity = p.get("capacity_kw", 5.0)   # kW

    if wind_speed < cut_in or wind_speed > cut_out:
        return 0.0
    if wind_speed >= rated:
        return capacity
    # Cubic interpolation between cut-in and rated
    fraction = ((wind_speed - cut_in) / (rated - cut_in)) ** 3
    return capacity * fraction


def solar_panel_power(solar_irradiance: float, asset: Asset) -> float:
    """Calculate solar panel output (kW) from irradiance.

    Simple linear model: P = capacity × (irradiance / reference_irradiance) × efficiency.
    """
    p = asset.params
    capacity = p.get("capacity_kw", 5.0)
    efficiency = p.get("efficiency", 0.18)
    ref_irradiance = p.get("reference_irradiance", 1000.0)  # W/m²

    return capacity * (solar_irradiance / ref_irradiance) * (efficiency / 0.18)


# ---------------------------------------------------------------------------
# Battery model
# ---------------------------------------------------------------------------

@dataclass
class BatteryResult:
    """Result of battery charge/discharge operation."""
    soc: float          # new state-of-charge (kWh)
    power_kw: float     # actual power delivered (+) or absorbed (-) in kW
    energy_kwh: float   # actual energy delivered/absorbed in kWh


def battery_dispatch(soc: float, demand_kw: float, dt_hours: float,
                     asset: Asset) -> BatteryResult:
    """Dispatch battery: positive demand = discharge, negative = charge.

    Respects capacity, rate, and depth-of-discharge limits.

    Args:
        soc: Current state of charge (kWh).
        demand_kw: Power demand (positive = discharge, negative = charge).
        dt_hours: Timestep in hours.
        asset: Battery asset with params.

    Returns:
        BatteryResult with new SOC and actual power.
    """
    p = asset.params
    capacity_kwh = p.get("capacity_kwh", 50.0)
    max_charge_kw = p.get("max_charge_kw", 20.0)
    max_discharge_kw = p.get("max_discharge_kw", 20.0)
    efficiency = p.get("round_trip_efficiency", 0.90)
    min_soc_frac = p.get("min_soc_fraction", 0.10)  # don't discharge below 10%

    min_soc = capacity_kwh * min_soc_frac
    one_way_eff = math.sqrt(efficiency)  # split round-trip across charge + discharge

    if demand_kw > 0:
        # Discharge
        max_power = min(demand_kw, max_discharge_kw)
        max_energy = (soc - min_soc) * one_way_eff  # available energy after DOD limit
        actual_energy = min(max_power * dt_hours, max(0.0, max_energy))
        actual_power = actual_energy / dt_hours if dt_hours > 0 else 0.0
        new_soc = soc - actual_energy / one_way_eff  # SOC decreases more than delivered
    else:
        # Charge (demand_kw is negative)
        charge_power = min(-demand_kw, max_charge_kw)
        room = capacity_kwh - soc
        actual_energy = min(charge_power * dt_hours * one_way_eff, max(0.0, room))
        actual_power = -(actual_energy / one_way_eff) / dt_hours if dt_hours > 0 else 0.0
        new_soc = soc + actual_energy

    new_soc = max(0.0, min(capacity_kwh, new_soc))

    return BatteryResult(soc=new_soc, power_kw=actual_power,
                         energy_kwh=actual_energy if demand_kw > 0 else -actual_energy)


# ---------------------------------------------------------------------------
# Generator dispatch
# ---------------------------------------------------------------------------

@dataclass
class GeneratorDispatchResult:
    """Result of dispatching one generator for one timestep."""
    running: bool
    load_fraction: float
    power_kw: float
    fuel_rate_lph: float  # L/hr
    fuel_used_l: float    # L this step
    waste_heat_kw: float  # recoverable waste heat


def dispatch_generator(demand_kw: float, dt_hours: float, asset: Asset,
                       is_faulted: bool, fault_capacity_reduction: float,
                       gen_efficiency: float, waste_heat_recovery: float) -> GeneratorDispatchResult:
    """Dispatch a single generator to meet demand.

    Args:
        demand_kw: Electrical demand for this generator.
        dt_hours: Timestep.
        asset: Generator asset.
        is_faulted: Whether the generator has an active fault.
        fault_capacity_reduction: Fraction of capacity lost to fault (0-1).
        gen_efficiency: Electrical efficiency of the generator.
        waste_heat_recovery: Fraction of waste heat that is recoverable.

    Returns:
        GeneratorDispatchResult.
    """
    p = asset.params
    capacity_kw = p.get("capacity_kw", 50.0)

    # Faulted generators have reduced capacity
    if is_faulted:
        effective_capacity = capacity_kw * (1.0 - fault_capacity_reduction)
    else:
        effective_capacity = capacity_kw

    if effective_capacity <= 0 or demand_kw <= 0:
        return GeneratorDispatchResult(
            running=False, load_fraction=0.0, power_kw=0.0,
            fuel_rate_lph=0.0, fuel_used_l=0.0, waste_heat_kw=0.0
        )

    load = min(1.0, demand_kw / effective_capacity)
    load = max(0.1, load)  # minimum idle load
    actual_power = effective_capacity * load

    # Fuel interpolation
    rate_full = p.get("fuel_rate_full_load", 15.0)
    rate_half = p.get("fuel_rate_half_load", 9.5)
    if load <= 0.5:
        fuel_rate = rate_half * (load / 0.5)
    else:
        fuel_rate = rate_half + (rate_full - rate_half) * (load - 0.5) / 0.5

    fuel_used = fuel_rate * dt_hours

    # Waste heat: total thermal = fuel_energy - electrical
    # fuel_energy (kW) = fuel_rate (L/hr) × density × energy_density / 3600
    # Simplified: waste_heat = electrical × (1/eff - 1) × recovery
    waste_heat = actual_power * (1.0 / gen_efficiency - 1.0) * waste_heat_recovery

    return GeneratorDispatchResult(
        running=True, load_fraction=load, power_kw=actual_power,
        fuel_rate_lph=fuel_rate, fuel_used_l=fuel_used, waste_heat_kw=waste_heat
    )


# ---------------------------------------------------------------------------
# Full energy dispatch
# ---------------------------------------------------------------------------

@dataclass
class EnergyDispatchResult:
    """Complete energy dispatch result for one timestep."""
    total_demand_kw: float
    renewable_kw: float
    battery_kw: float        # positive = discharging
    generator_kw: float
    waste_heat_kw: float
    total_fuel_consumed_l: float
    battery_soc_kwh: float
    excess_kw: float         # generation exceeding demand (→ battery charge)
    unmet_demand_kw: float   # demand that couldn't be met (load shedding)

    # Per-generator details
    gen_results: dict[str, GeneratorDispatchResult]


def dispatch_energy(total_demand_kw: float, env: Environment, dt_hours: float,
                    graph: AssetGraph, generator_states: dict,
                    battery_soc: float, params: dict[str, Any]) -> EnergyDispatchResult:
    """Full merit-order energy dispatch.

    Order: renewables → battery → generators (fewest needed).

    Args:
        total_demand_kw: Total station electrical demand.
        env: Current weather conditions.
        dt_hours: Timestep.
        graph: Asset graph.
        generator_states: Dict of gen_id → GeneratorState with fault info.
        battery_soc: Current battery state of charge (kWh). 0 if no battery.
        params: Global params.

    Returns:
        EnergyDispatchResult.
    """
    gen_efficiency = param_value(params, "generator_efficiency")
    waste_heat_recovery = param_value(params, "generator_waste_heat_recovery")
    remaining_demand = total_demand_kw

    # ---- 1. Renewables ----
    renewable_kw = 0.0
    for asset in graph.get_by_type(AssetType.RENEWABLE):
        rtype = asset.params.get("type", "wind")
        if rtype == "wind":
            renewable_kw += wind_turbine_power(env.wind_speed, asset)
        elif rtype == "solar":
            renewable_kw += solar_panel_power(env.solar_irradiance, asset)

    remaining_demand -= renewable_kw

    # ---- 2. Battery discharge (if demand remains) ----
    battery_kw = 0.0
    new_battery_soc = battery_soc
    battery_assets = [a for a in graph.get_by_type(AssetType.STORAGE)
                      if a.params.get("commodity") == "battery"]

    if remaining_demand > 0 and battery_assets:
        bat = battery_assets[0]
        result = battery_dispatch(battery_soc, remaining_demand, dt_hours, bat)
        battery_kw = result.power_kw
        new_battery_soc = result.soc
        remaining_demand -= battery_kw

    # ---- 3. Generator dispatch (merit order by lowest running hours) ----
    all_gens = sorted(
        graph.get_by_type(AssetType.GENERATOR),
        key=lambda g: (generator_states[g.id].running_hours if g.id in generator_states else 0.0, g.id)
    )
    total_fuel = 0.0
    total_gen_kw = 0.0
    total_waste_heat = 0.0
    gen_results: dict[str, GeneratorDispatchResult] = {}

    gen_demand = max(0.0, remaining_demand)

    # Determine how many generators needed
    available_gens = []
    for g in all_gens:
        gs = generator_states.get(g.id)
        is_faulted = getattr(gs, 'faulted', False) if gs else False
        fault_reduction = getattr(gs, 'fault_capacity_reduction', 0.5) if gs else 0.5
        cap = g.params.get("capacity_kw", 50.0)
        if is_faulted:
            cap *= (1.0 - fault_reduction)
        available_gens.append((g, cap, is_faulted, fault_reduction))

    # Greedily bring generators online until demand is met
    gens_to_run = []
    cumulative_cap = 0.0
    for g, cap, faulted, reduction in available_gens:
        if gen_demand <= 0:
            break
        gens_to_run.append((g, cap, faulted, reduction))
        cumulative_cap += cap
        if cumulative_cap >= gen_demand:
            break

    # Always run at least 1 generator (station needs power for critical
    # systems like comms and life-support that aren't explicitly modeled).
    # This safeguard ensures at least one generator is available for grid stability,
    # even when renewables cover the entire load. Note that running at 0 kW demand
    # still incurs a 10% minimum load fuel burn penalty (spinning reserve).
    if not gens_to_run and available_gens:
        g, cap, faulted, reduction = available_gens[0]
        gens_to_run.append((g, cap, faulted, reduction))
        cumulative_cap = cap

    # Distribute load proportionally among running generators based on their effective capacity
    running_cap_total = sum(cap for (g, cap, faulted, reduction) in gens_to_run)
    
    for g, cap, faulted, reduction in gens_to_run:
        share = gen_demand * (cap / running_cap_total) if running_cap_total > 0 else 0.0
        gen_result = dispatch_generator(
            share, dt_hours, g, faulted, reduction,
            gen_efficiency, waste_heat_recovery
        )
        gen_results[g.id] = gen_result
        total_fuel += gen_result.fuel_used_l
        total_gen_kw += gen_result.power_kw
        total_waste_heat += gen_result.waste_heat_kw

    # Mark non-running generators
    for g in all_gens:
        if g.id not in gen_results:
            gen_results[g.id] = GeneratorDispatchResult(
                running=False, load_fraction=0.0, power_kw=0.0,
                fuel_rate_lph=0.0, fuel_used_l=0.0, waste_heat_kw=0.0
            )

    remaining_demand -= total_gen_kw

    # ---- 4. Charge battery from excess ----
    total_supply = renewable_kw + battery_kw + total_gen_kw
    excess = total_supply - total_demand_kw
    if excess > 0 and battery_assets:
        bat = battery_assets[0]
        charge_result = battery_dispatch(new_battery_soc, -excess, dt_hours, bat)
        new_battery_soc = charge_result.soc

    unmet = max(0.0, remaining_demand)

    return EnergyDispatchResult(
        total_demand_kw=total_demand_kw,
        renewable_kw=renewable_kw,
        battery_kw=battery_kw,
        generator_kw=total_gen_kw,
        waste_heat_kw=total_waste_heat,
        total_fuel_consumed_l=total_fuel,
        battery_soc_kwh=new_battery_soc,
        excess_kw=max(0.0, excess),
        unmet_demand_kw=unmet,
        gen_results=gen_results,
    )


# ---------------------------------------------------------------------------
# Equipment condition model
# ---------------------------------------------------------------------------

def update_condition(condition: float, dt_hours: float, load_fraction: float, mtbf_hours: float = 5000.0) -> float:
    if load_fraction == 0:
        return condition
    wear_multiplier = 1.0
    if load_fraction > 0.8:
        wear_multiplier = 3.0
    wear = (1.0 / (2.0 * mtbf_hours)) * dt_hours * wear_multiplier
    return max(0.0, condition - wear)


def check_fault(condition: float, dt_hours: float, rng_value: float,
                base_fault_rate: float = 0.0001) -> bool:
    """Check if a fault occurs this timestep.

    Fault probability increases as condition degrades:
      P(fault per hour) = base_rate × (1 / condition²)

    Args:
        condition: Current condition score (0-1).
        dt_hours: Timestep.
        rng_value: Pre-generated random value [0,1) from seeded RNG.
        base_fault_rate: Base fault probability per hour at condition=1.

    Returns:
        True if a fault occurs.
    """
    if condition <= 0.01:
        return True  # guaranteed fault at zero condition
    fault_rate = base_fault_rate / (condition ** 2)
    prob = 1.0 - math.exp(-fault_rate * dt_hours)  # Poisson probability
    return rng_value < prob

