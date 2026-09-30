"""Tests for the step contract and physical invariants."""

from antarctic_twin.asset_graph import AssetGraph
from antarctic_twin.config import load_params, load_station
from antarctic_twin.state import initialize_state, step
from antarctic_twin.weather import WeatherGenerator
from tests.conftest import REPO_ROOT as BASE


def _setup():
    cfg = load_station(BASE / "stations" / "bharati.yaml")
    params = load_params(BASE / "params.yaml")
    graph = AssetGraph(cfg)
    state = initialize_state(graph, crew_count=15)
    weather = WeatherGenerator(cfg["weather"], params, seed=42)
    return state, graph, params, weather


def test_step_advances_time():
    state, graph, params, weather = _setup()
    env = weather.get_weather(0.0)
    new = step(state, env, 1.0, graph, params)
    assert new.time_hours == state.time_hours + 1.0


def test_step_does_not_mutate_input():
    """step() must not mutate the input state."""
    state, graph, params, weather = _setup()
    env = weather.get_weather(0.0)
    original_time = state.time_hours
    original_fuel = next(iter(state.storage.values())).level if state.storage else None
    _ = step(state, env, 1.0, graph, params)
    assert state.time_hours == original_time
    if original_fuel is not None:
        assert next(iter(state.storage.values())).level == original_fuel


def test_fuel_decreases():
    """Fuel should decrease when generators are running."""
    state, graph, params, weather = _setup()
    fuel_stores = graph.storage_by_commodity("fuel")
    assert len(fuel_stores) > 0
    initial_fuel = state.storage[fuel_stores[0].id].level

    env = weather.get_weather(100.0)  # mid-year
    new = step(state, env, 1.0, graph, params)
    final_fuel = new.storage[fuel_stores[0].id].level
    assert final_fuel < initial_fuel


def test_water_stays_positive():
    """Water should never go negative, even after many steps."""
    state, graph, params, weather = _setup()
    for i in range(500):
        env = weather.get_weather(float(i))
        state = step(state, env, 1.0, graph, params)
    for store in graph.storage_by_commodity("water"):
        assert state.storage[store.id].level >= 0.0


def test_food_decreases():
    """Food should decrease over time."""
    state, graph, params, weather = _setup()
    food_stores = graph.storage_by_commodity("food")
    assert len(food_stores) > 0
    initial_food = state.storage[food_stores[0].id].level

    for i in range(24):  # 1 day
        env = weather.get_weather(float(i) / 24.0)
        state = step(state, env, 1.0, graph, params)

    final_food = state.storage[food_stores[0].id].level
    assert final_food < initial_food
    # Check roughly correct: 15 crew × 2.5 kg/day ≈ 37.5 kg
    consumed = initial_food - final_food
    assert 30 < consumed < 50  # reasonable range


def test_no_negative_stocks_full_year():
    """No stock (fuel, water, food) should ever be negative in a full year."""
    state, graph, params, weather = _setup()
    for i in range(365 * 24):
        env = weather.get_weather(float(i) / 24.0)
        state = step(state, env, 1.0, graph, params)
        for sid, ss in state.storage.items():
            assert ss.level >= 0.0, f"{sid} went negative at hour {i}"


def test_lower_temperature_increases_heating():
    """Colder outside temperature should require more heating."""
    from antarctic_twin.types import Environment
    state, graph, params, _ = _setup()

    mild_env = Environment(temperature=-5.0, wind_speed=5.0, solar_irradiance=0.0,
                           is_storm=False, day_of_year=190.0, hour=12.0)
    cold_env = Environment(temperature=-30.0, wind_speed=5.0, solar_irradiance=0.0,
                           is_storm=False, day_of_year=190.0, hour=12.0)

    mild_state = step(state, mild_env, 1.0, graph, params)
    cold_state = step(state, cold_env, 1.0, graph, params)

    assert cold_state.total_heating_demand_kw > mild_state.total_heating_demand_kw
