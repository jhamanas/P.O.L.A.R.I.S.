"""Tests for deterministic replay: same seed → identical results."""

from antarctic_twin.config import load_params, load_station
from antarctic_twin.engine import SimulationEngine
from tests.conftest import REPO_ROOT as BASE


def _run(station: str, seed: int, days: int = 30):
    cfg = load_station(BASE / "stations" / f"{station}.yaml")
    params = load_params(BASE / "params.yaml")
    engine = SimulationEngine(cfg, params, seed=seed)
    return engine.run(days=days)


def test_replay_bharati():
    """Two runs with the same seed produce identical final state."""
    r1 = _run("bharati", seed=123, days=30)
    r2 = _run("bharati", seed=123, days=30)

    assert r1.final_state.time_hours == r2.final_state.time_hours
    # Check fuel levels match exactly
    for sid in r1.final_state.storage:
        assert r1.final_state.storage[sid].level == r2.final_state.storage[sid].level
    # Check zone temps match exactly
    for zid in r1.final_state.zones:
        assert r1.final_state.zones[zid].temperature == r2.final_state.zones[zid].temperature


def test_replay_maitri():
    """Maitri also replays identically from config."""
    r1 = _run("maitri", seed=456, days=30)
    r2 = _run("maitri", seed=456, days=30)

    assert r1.final_state.time_hours == r2.final_state.time_hours
    for sid in r1.final_state.storage:
        assert r1.final_state.storage[sid].level == r2.final_state.storage[sid].level


def test_different_seeds_differ():
    """Different seeds should produce different results."""
    r1 = _run("bharati", seed=1, days=30)
    r2 = _run("bharati", seed=2, days=30)

    fuel_ids = [s for s in r1.final_state.storage if "fuel" in s]
    assert len(fuel_ids) > 0
    # Fuel levels should differ (different weather → different heating → different fuel)
    assert r1.final_state.storage[fuel_ids[0]].level != r2.final_state.storage[fuel_ids[0]].level


def test_full_year_runs_quickly():
    """A 365-day simulation should complete (not hang or crash)."""
    import time
    start = time.time()
    result = _run("bharati", seed=42, days=365)
    elapsed = time.time() - start

    assert result.n_steps == 365 * 24
    assert elapsed < 30.0, f"Full year took {elapsed:.1f}s, should be under 30s"
    print(f"Full year: {elapsed:.2f}s, {result.n_steps} steps")
