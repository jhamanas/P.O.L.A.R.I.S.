"""Tests for the weather generator."""

from pathlib import Path
from antarctic_twin.config import load_params, load_station
from antarctic_twin.weather import WeatherGenerator


BASE = Path("d:/PS2")


def _make_weather(seed: int = 42) -> WeatherGenerator:
    cfg = load_station(BASE / "stations" / "bharati.yaml")
    params = load_params(BASE / "params.yaml")
    return WeatherGenerator(cfg["weather"], params, seed=seed)


def test_deterministic():
    """Same seed → identical weather sequence."""
    w1 = _make_weather(seed=99)
    w2 = _make_weather(seed=99)
    for day in range(0, 365, 30):
        e1 = w1.get_weather(float(day))
        e2 = w2.get_weather(float(day))
        assert e1.temperature == e2.temperature
        assert e1.wind_speed == e2.wind_speed
        assert e1.is_storm == e2.is_storm


def test_different_seeds_differ():
    """Different seeds → different weather."""
    w1 = _make_weather(seed=1)
    w2 = _make_weather(seed=2)
    temps1 = [w1.get_weather(float(d)).temperature for d in range(30)]
    temps2 = [w2.get_weather(float(d)).temperature for d in range(30)]
    assert temps1 != temps2


def test_winter_colder_than_summer():
    """Mid-winter (day 190) should be colder than mid-summer (day 0)."""
    w = _make_weather(seed=42)
    # Generate weather at summer (day 0) and winter (day 190)
    # Need fresh generators for fair comparison
    ws = _make_weather(seed=42)
    ww = _make_weather(seed=42)
    # Advance summer generator to day 0
    summer_temps = [ws.get_weather(float(d)).temperature for d in range(10)]
    # Advance winter generator to day 190
    winter_temps = [ww.get_weather(190.0 + d).temperature for d in range(10)]
    assert sum(summer_temps) / len(summer_temps) > sum(winter_temps) / len(winter_temps)


def test_wind_non_negative():
    """Wind speed should never be negative."""
    w = _make_weather(seed=42)
    for day in range(365):
        env = w.get_weather(float(day))
        assert env.wind_speed >= 0.0


def test_storm_has_higher_wind():
    """When a storm occurs, wind should be elevated."""
    w = _make_weather(seed=42)
    storms = []
    calms = []
    for day in range(365):
        for h in range(24):
            env = w.get_weather(day + h / 24.0)
            if env.is_storm:
                storms.append(env.wind_speed)
            else:
                calms.append(env.wind_speed)
    if storms and calms:
        assert sum(storms) / len(storms) > sum(calms) / len(calms)
