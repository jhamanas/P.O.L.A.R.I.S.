"""Phase 4 tests: scenarios, presets, sensitivity, backtest."""

from pathlib import Path
from antarctic_twin.scenarios import (
    ScenarioSpec, PRESETS, run_scenario,
    run_sensitivity, run_backtest,
    apply_scenario,
)
from antarctic_twin.config import load_station, load_params
from antarctic_twin.alerts import AlertSeverity


from tests.conftest import REPO_ROOT as BASE
STATION = BASE / "stations" / "bharati.yaml"
PARAMS = BASE / "params.yaml"


# ================================================================
# Scenario spec
# ================================================================

def test_scenario_spec_diff():
    """ScenarioSpec should report non-default values as diff."""
    spec = ScenarioSpec(
        name="Test",
        temp_offset=-10.0,
        faults=["gen1"],
    )
    diff = spec.diff_from_baseline()
    assert "Temperature" in diff
    assert "Faults" in diff
    assert "Wind" not in diff  # default, should not appear
    assert "Crew" not in diff


def test_six_presets_exist():
    """All six preset scenarios should be defined."""
    expected = {
        "cold_snap", "prolonged_blizzard", "delayed_resupply",
        "generator_failure", "crew_surge", "combined_winter_isolation",
    }
    assert set(PRESETS.keys()) == expected


def test_presets_have_descriptions():
    """Every preset should have a non-empty description."""
    for key, spec in PRESETS.items():
        assert len(spec.description) > 20, f"Preset '{key}' has no description"


def test_apply_scenario_modifies_config():
    """apply_scenario should modify the config according to the spec."""
    cfg = load_station(STATION)
    spec = ScenarioSpec(name="Test", temp_offset=-10.0, crew_delta=5)
    modified = apply_scenario(cfg, {}, spec)

    # Crew should be increased
    assert modified["crew"]["winter"] == cfg["crew"]["winter"] + 5

    # Temperature should be shifted
    orig_winter = cfg["weather"]["winter_temp_avg"]["value"]
    new_winter = modified["weather"]["winter_temp_avg"]["value"]
    assert abs(new_winter - (orig_winter - 10.0)) < 0.01


# ================================================================
# Scenario runner
# ================================================================

def test_cold_snap_scenario_runs():
    """Cold snap scenario should run and produce worse fuel than baseline."""
    result = run_scenario(STATION, PARAMS, PRESETS["cold_snap"],
                          days=200, forecast_runs=20)

    # Scenario should use more fuel than baseline
    diff = result.diff_summary()
    assert "Fuel remaining (L)" in diff

    # Scenario should produce alerts
    assert len(result.scenario_alerts) >= len(result.baseline_alerts)


def test_generator_failure_scenario():
    """Generator failure should produce equipment alerts."""
    result = run_scenario(STATION, PARAMS, PRESETS["generator_failure"],
                          days=100, forecast_runs=20)

    equip_alerts = [a for a in result.scenario_alerts
                    if a.category.value == "equipment"]
    assert len(equip_alerts) > 0, "No equipment alert for generator failure"


def test_combined_scenario_worst_case():
    """Combined winter isolation should be worse than any single scenario."""
    combined = run_scenario(STATION, PARAMS, PRESETS["combined_winter_isolation"],
                            days=200, forecast_runs=20)
    cold = run_scenario(STATION, PARAMS, PRESETS["cold_snap"],
                        days=200, forecast_runs=20)

    # Combined should have equal or more RED alerts
    combined_reds = sum(1 for a in combined.scenario_alerts
                        if a.severity == AlertSeverity.RED)
    cold_reds = sum(1 for a in cold.scenario_alerts
                    if a.severity == AlertSeverity.RED)
    assert combined_reds >= cold_reds


# ================================================================
# Sensitivity analysis
# ================================================================

def test_sensitivity_runs():
    """Sensitivity analysis should produce results for all parameters."""
    results = run_sensitivity(STATION, PARAMS, days=200)
    assert len(results) >= 4  # at least 4 parameters
    for r in results:
        assert r.parameter
        assert r.unit


def test_sensitivity_ordered_by_impact():
    """Sensitivity results should be sorted by impact (largest first)."""
    results = run_sensitivity(STATION, PARAMS, days=200)
    impacts = [abs(r.high_value - r.low_value) for r in results]
    for i in range(len(impacts) - 1):
        assert impacts[i] >= impacts[i + 1] - 0.01


# ================================================================
# Calibration backtest
# ================================================================

def test_backtest_all_checks_pass():
    """All calibration backtest checks should pass for Bharati baseline."""
    checks = run_backtest(STATION, PARAMS)
    assert len(checks) >= 6

    for check in checks:
        assert check.passed, (
            f"Backtest FAILED: {check.name}\n"
            f"  Expected: {check.expected_range}\n"
            f"  Actual: {check.actual_value:.2f} {check.unit}"
        )


def test_backtest_maitri_passes():
    """Backtest should also pass for Maitri (second station by config)."""
    maitri = BASE / "stations" / "maitri.yaml"
    checks = run_backtest(maitri, PARAMS)
    for check in checks:
        assert check.passed, (
            f"Maitri backtest FAILED: {check.name}\n"
            f"  Expected: {check.expected_range}\n"
            f"  Actual: {check.actual_value:.2f} {check.unit}"
        )
