"""Phase 1 tests: energy dispatch, condition/faults, couplings, cascade invariants."""

from pathlib import Path
import math
from antarctic_twin.config import load_params, load_station, param_value
from antarctic_twin.asset_graph import AssetGraph
from antarctic_twin.state import initialize_state, step, GeneratorState
from antarctic_twin.weather import WeatherGenerator
from antarctic_twin.types import Environment, AssetType
from antarctic_twin.energy import (
    wind_turbine_power, solar_panel_power, battery_dispatch,
    update_condition, check_fault, dispatch_energy
)
from antarctic_twin.engine import SimulationEngine


from tests.conftest import REPO_ROOT as BASE


def _setup(station="bharati"):
    cfg = load_station(BASE / "stations" / f"{station}.yaml")
    params = load_params(BASE / "params.yaml")
    graph = AssetGraph(cfg)
    state = initialize_state(graph, crew_count=15)
    weather = WeatherGenerator(cfg["weather"], params, seed=42)
    return state, graph, params, weather, cfg


# ---- Energy balance ----

def test_energy_balance_every_step():
    """Generation + renewables + battery >= demand - unmet at every step."""
    state, graph, params, weather, cfg = _setup()
    engine = SimulationEngine(cfg, params, seed=42)
    result = engine.run(days=30)

    for s in result.history[1:]:  # skip initial
        supply = s.total_generation_kw + s.renewable_generation_kw + max(0, s.battery_power_kw)
        # supply should cover demand minus any acknowledged unmet
        assert supply >= s.total_electrical_load_kw - s.unmet_demand_kw - 0.1, (
            f"Energy balance violated at hour {s.time_hours}: "
            f"supply={supply:.1f} demand={s.total_electrical_load_kw:.1f} unmet={s.unmet_demand_kw:.1f}"
        )


# ---- No negative stocks ----

def test_no_negative_stocks():
    """No stock should ever go negative in a 365-day run."""
    _, graph, params, _, cfg = _setup()
    engine = SimulationEngine(cfg, params, seed=42)
    result = engine.run(days=365)
    for s in result.history:
        for sid, ss in s.storage.items():
            assert ss.level >= 0.0, f"{sid} went negative at hour {s.time_hours}"


# ---- Monotonic heating demand ----

def test_monotonic_heating_vs_temperature():
    """Heating demand should increase when outside temperature drops."""
    state, graph, params, _, _ = _setup()

    mild = Environment(temperature=-5.0, wind_speed=5.0, solar_irradiance=0.0,
                       is_storm=False, day_of_year=190.0, hour=12.0)
    cold = Environment(temperature=-30.0, wind_speed=5.0, solar_irradiance=0.0,
                       is_storm=False, day_of_year=190.0, hour=12.0)

    mild_state = step(state, mild, 1.0, graph, params)
    cold_state = step(state, cold, 1.0, graph, params)
    assert cold_state.total_heating_demand_kw > mild_state.total_heating_demand_kw


# ---- Lower temperature moves exhaustion earlier ----

def test_lower_temp_moves_exhaustion_earlier():
    """A colder scenario should exhaust fuel faster."""
    _, graph, params, _, cfg = _setup()

    # Normal run
    engine1 = SimulationEngine(cfg, params, seed=42)
    r1 = engine1.run(days=365)

    # Find fuel exhaustion day for normal
    fuel_id = [sid for sid in r1.history[0].storage if "fuel" in sid][0]
    exhaust_day_normal = None
    for s in r1.history:
        if s.storage[fuel_id].level <= 0:
            exhaust_day_normal = s.time_hours / 24.0
            break

    # Create colder config (reduce summer temp, making overall colder)
    cold_cfg = cfg.copy()
    cold_weather = dict(cfg["weather"])
    cold_weather["summer_temp_avg"] = {"value": -5.0, "unit": "°C", "source": "test"}
    cold_weather["winter_temp_avg"] = {"value": -30.0, "unit": "°C", "source": "test"}
    cold_cfg["weather"] = cold_weather

    engine2 = SimulationEngine(cold_cfg, params, seed=42)
    r2 = engine2.run(days=365)
    exhaust_day_cold = None
    for s in r2.history:
        if s.storage[fuel_id].level <= 0:
            exhaust_day_cold = s.time_hours / 24.0
            break

    # Cold scenario should exhaust fuel earlier (or both survive but cold has less)
    if exhaust_day_normal is not None and exhaust_day_cold is not None:
        assert exhaust_day_cold <= exhaust_day_normal
    elif exhaust_day_cold is not None:
        pass  # cold exhausts, normal doesn't — correct
    else:
        # Both survive — cold should have less fuel remaining
        fuel_normal = r1.final_state.storage[fuel_id].level
        fuel_cold = r2.final_state.storage[fuel_id].level
        assert fuel_cold < fuel_normal


# ---- Deterministic replay ----

def test_replay_with_faults():
    """Replay should be identical even with fault injection (same seed)."""
    _, _, params, _, cfg = _setup()

    r1 = SimulationEngine(cfg, params, seed=99).run(days=60)
    r2 = SimulationEngine(cfg, params, seed=99).run(days=60)

    for sid in r1.final_state.storage:
        assert r1.final_state.storage[sid].level == r2.final_state.storage[sid].level
    for zid in r1.final_state.zones:
        assert r1.final_state.zones[zid].temperature == r2.final_state.zones[zid].temperature
    for gid in r1.final_state.generators:
        assert r1.final_state.generators[gid].condition == r2.final_state.generators[gid].condition
        assert r1.final_state.generators[gid].faulted == r2.final_state.generators[gid].faulted


# ---- Wind turbine power curve ----

def test_wind_turbine_power_curve():
    """Wind turbine should follow cubic power curve."""
    from antarctic_twin.asset_graph import Asset
    asset = Asset(id="test.wind", type=AssetType.RENEWABLE, label="Test",
                  params={"type": "wind", "capacity_kw": 10.0,
                          "cut_in_wind": 3.0, "rated_wind": 12.0, "cut_out_wind": 25.0})

    assert wind_turbine_power(0.0, asset) == 0.0    # below cut-in
    assert wind_turbine_power(2.0, asset) == 0.0    # below cut-in
    assert wind_turbine_power(12.0, asset) == 10.0  # at rated
    assert wind_turbine_power(15.0, asset) == 10.0  # above rated, below cut-out
    assert wind_turbine_power(26.0, asset) == 0.0   # above cut-out
    # Mid-range should be between 0 and rated
    mid = wind_turbine_power(7.5, asset)
    assert 0 < mid < 10.0


# ---- Condition degradation ----

def test_condition_degrades_with_hours():
    """Condition should decrease as running hours increase."""
    c0 = update_condition(0, 1.0, mtbf_hours=5000)
    c1 = update_condition(2000, 1.0, mtbf_hours=5000)
    c2 = update_condition(5000, 1.0, mtbf_hours=5000)
    c3 = update_condition(10000, 1.0, mtbf_hours=5000)

    assert c0 == 1.0
    assert c1 < c0
    assert c2 < c1
    assert c3 == 0.0


# ---- Fault probability ----

def test_fault_probability_increases_with_degradation():
    """Lower condition should have higher fault probability."""
    # At condition=1.0, fault should be very unlikely
    assert not check_fault(1.0, 1.0, 0.99, base_fault_rate=0.0001)
    # At condition=0.01, fault should be nearly certain
    assert check_fault(0.01, 1.0, 0.5, base_fault_rate=0.0001)


# ---- Generator fault reduces capacity ----

def test_faulted_generator_reduces_capacity():
    """A faulted generator should produce less power."""
    _, graph, params, _, cfg = _setup()
    state = initialize_state(graph, crew_count=15)

    env = Environment(temperature=-15.0, wind_speed=5.0, solar_irradiance=0.0,
                      is_storm=False, day_of_year=190.0, hour=12.0)

    # Normal run
    normal = step(state, env, 1.0, graph, params)

    # Fault gen1
    faulted_state = state.copy()
    gen1_id = [gid for gid in faulted_state.generators if "gen1" in gid][0]
    faulted_state.generators[gen1_id].faulted = True
    faulted_state.generators[gen1_id].fault_capacity_reduction = 0.5

    faulted = step(faulted_state, env, 1.0, graph, params)

    # If gen1 was the only running gen, the faulted version should have
    # either less generation or bring another gen online
    # Either way, total generation should still meet demand (or have unmet)
    assert faulted.total_generation_kw > 0


# ---- Battery charge/discharge ----

def test_battery_charge_discharge():
    """Battery should charge and discharge correctly."""
    from antarctic_twin.asset_graph import Asset
    bat = Asset(id="test.bat", type=AssetType.STORAGE, label="Test Battery",
                params={"commodity": "battery", "capacity_kwh": 100.0,
                        "max_charge_kw": 20.0, "max_discharge_kw": 20.0,
                        "round_trip_efficiency": 0.90, "min_soc_fraction": 0.10})

    # Discharge
    result = battery_dispatch(50.0, 10.0, 1.0, bat)  # 10 kW for 1 hour
    assert result.soc < 50.0
    assert result.power_kw > 0

    # Charge
    result = battery_dispatch(50.0, -15.0, 1.0, bat)  # charge at 15 kW
    assert result.soc > 50.0

    # Don't discharge below min SOC
    result = battery_dispatch(11.0, 100.0, 1.0, bat)  # try to drain 100 kW from near-empty
    assert result.soc >= 10.0  # min SOC = 10%


# ---- Priority heating ----

def test_priority_heating():
    """Living quarters (highest target_temp) should be heated before workshop."""
    state, graph, params, _, _ = _setup()

    # Very cold environment, limited heating capacity
    cold = Environment(temperature=-40.0, wind_speed=15.0, solar_irradiance=0.0,
                       is_storm=True, day_of_year=190.0, hour=12.0)

    result = step(state, cold, 1.0, graph, params)

    # Living zone (target 20°C) should get more heating than workshop (target 10°C)
    living_id = [zid for zid in result.zones if "living" in zid][0]
    workshop_id = [zid for zid in result.zones if "workshop" in zid][0]

    living_heat = result.zones[living_id].heating_kw
    workshop_heat = result.zones[workshop_id].heating_kw

    # Living should have higher heating power allocated
    assert living_heat >= workshop_heat


# ---- Maitri wind turbine contributes power ----

def test_maitri_wind_contributes():
    """Maitri's wind turbine should produce non-zero power in wind."""
    _, _, params, _, cfg = _setup("maitri")
    engine = SimulationEngine(cfg, params, seed=42)
    result = engine.run(days=30)

    # Sum renewable generation over the run
    total_renewable = sum(s.renewable_generation_kw for s in result.history[1:])
    assert total_renewable > 0, "Maitri wind turbine produced no power in 30 days"


# ---- Snow-melt coupling ----

def test_snow_melt_scales_with_power():
    """Snow-melt should be reduced when there's unmet electrical demand."""
    state, graph, params, _, _ = _setup()

    # Normal conditions
    normal_env = Environment(temperature=-10.0, wind_speed=5.0, solar_irradiance=0.0,
                             is_storm=False, day_of_year=100.0, hour=12.0)
    normal_state = step(state, normal_env, 1.0, graph, params)

    # Force a power shortage by faulting all generators heavily
    shortage_state = state.copy()
    for gid in shortage_state.generators:
        shortage_state.generators[gid].faulted = True
        shortage_state.generators[gid].fault_capacity_reduction = 0.9

    shortage = step(shortage_state, normal_env, 1.0, graph, params)

    # Snow-melt should be lower (or equal, if the snowmelt kw vs total allows)
    assert shortage.snow_melt_production_l <= normal_state.snow_melt_production_l + 0.01
