"""Phase 2 tests: forecast, alerts, resupply coupling, cold-snap scenario."""

from pathlib import Path
import numpy as np

from antarctic_twin.config import load_params, load_station
from antarctic_twin.asset_graph import AssetGraph
from antarctic_twin.engine import SimulationEngine
from antarctic_twin.forecast import run_forecast
from antarctic_twin.alerts import derive_alerts, AlertSeverity, AlertCategory
from antarctic_twin.state import initialize_state
from antarctic_twin.types import AssetType


from tests.conftest import REPO_ROOT as BASE


def _setup(station="bharati", seed=42, days=200):
    cfg = load_station(BASE / "stations" / f"{station}.yaml")
    params = load_params(BASE / "params.yaml")
    engine = SimulationEngine(cfg, params, seed=seed)
    result = engine.run(days=days)
    graph = AssetGraph(cfg)
    return result, graph, cfg, params


# ================================================================
# Forecast tests
# ================================================================

def test_forecast_runs():
    """Forecast should complete and produce results for all consumables."""
    result, graph, cfg, params = _setup(days=100)
    state = result.history[100 * 24]  # state at day 100

    fc = run_forecast(state, graph, cfg, params,
                      n_runs=20, horizon_days=90, dt_hours=6.0)

    assert fc.n_runs == 20
    assert fc.forecast_horizon_days == 90
    # Should have forecasts for fuel, water, food
    commodities = {f.commodity for f in fc.consumables.values()}
    assert "fuel" in commodities
    assert "water" in commodities
    assert "food" in commodities


def test_forecast_percentiles_ordered():
    """P90 (pessimistic) should exhaust earlier than P10 (optimistic)."""
    result, graph, cfg, params = _setup(days=100)
    state = result.history[100 * 24]

    fc = run_forecast(state, graph, cfg, params,
                      n_runs=50, horizon_days=120, dt_hours=6.0)

    for sid, cf in fc.consumables.items():
        # P90 <= P50 <= P10 (P90 exhausts first = pessimistic)
        assert cf.p90_days <= cf.p50_days + 0.01, f"{sid}: P90 > P50"
        assert cf.p50_days <= cf.p10_days + 0.01, f"{sid}: P50 > P10"


def test_forecast_trajectories_stored():
    """Fan chart trajectories should be stored when requested."""
    result, graph, cfg, params = _setup(days=30)
    state = result.history[30 * 24]

    fc = run_forecast(state, graph, cfg, params,
                      n_runs=10, horizon_days=60, dt_hours=6.0,
                      store_trajectories=True)

    assert fc.fuel_trajectories is not None
    assert fc.fuel_trajectories.shape == (10, 60)
    # Fuel should generally decrease over time
    assert fc.fuel_trajectories[0, 0] >= fc.fuel_trajectories[0, -1]


def test_forecast_margin_days():
    """margin_days should equal exhaustion_day - resupply_day."""
    result, graph, cfg, params = _setup(days=100)
    state = result.history[100 * 24]

    fc = run_forecast(state, graph, cfg, params,
                      n_runs=20, horizon_days=120, dt_hours=6.0,
                      resupply_day=350.0)

    for sid, cf in fc.consumables.items():
        assert abs(cf.margin_p50 - (cf.p50_days - 350.0)) < 0.01


# ================================================================
# Alert tests
# ================================================================

def test_alerts_from_degraded_state():
    """Should produce equipment alerts when generators are degraded."""
    result, graph, cfg, params = _setup(days=300)
    state = result.final_state

    # After 300 days, gen1 should have degraded condition
    alerts = derive_alerts(state, graph, params)

    # Should have at least one equipment alert
    equip_alerts = [a for a in alerts if a.category == AlertCategory.EQUIPMENT]
    assert len(equip_alerts) > 0, "No equipment alerts after 300 days of operation"


def test_alert_has_required_fields():
    """Every alert should have all required fields populated."""
    result, graph, cfg, params = _setup(days=200)
    state = result.final_state

    alerts = derive_alerts(state, graph, params)

    for alert in alerts:
        assert alert.id, "Alert missing ID"
        assert alert.severity in AlertSeverity
        assert alert.category in AlertCategory
        assert len(alert.cause) > 10, f"Alert {alert.id}: cause too short"
        assert len(alert.evidence) > 10, f"Alert {alert.id}: evidence too short"
        assert len(alert.consequence) > 10, f"Alert {alert.id}: consequence too short"
        assert len(alert.recommended_action) > 10, f"Alert {alert.id}: action too short"


def test_alerts_sorted_by_severity():
    """Alerts should be sorted RED first, then AMBER, then GREEN."""
    result, graph, cfg, params = _setup(days=300)
    state = result.final_state

    alerts = derive_alerts(state, graph, params)
    if len(alerts) >= 2:
        severity_order = {AlertSeverity.RED: 0, AlertSeverity.AMBER: 1, AlertSeverity.GREEN: 2}
        for i in range(len(alerts) - 1):
            assert severity_order[alerts[i].severity] <= severity_order[alerts[i+1].severity]


def test_resupply_delay_raises_logistics_alert():
    """Delaying resupply should produce a logistics alert."""
    result, graph, cfg, params = _setup(days=100)
    state = result.history[100 * 24]

    alerts = derive_alerts(state, graph, params, resupply_delay_days=45)

    logistics = [a for a in alerts if a.category == AlertCategory.LOGISTICS]
    assert len(logistics) > 0, "No logistics alert for 45-day resupply delay"
    assert logistics[0].severity in (AlertSeverity.RED, AlertSeverity.AMBER)
    assert "45" in logistics[0].cause  # should mention the delay


def test_alert_acknowledgement():
    """Alerts should be acknowledgeable."""
    result, graph, cfg, params = _setup(days=200)
    state = result.final_state

    alerts = derive_alerts(state, graph, params)
    if alerts:
        alert = alerts[0]
        assert not alert.acknowledged
        alert.acknowledge()
        assert alert.acknowledged


# ================================================================
# Cold-snap scenario (EXIT CRITERION)
# ================================================================

def test_cold_snap_produces_red_alert_with_fuel_quantity():
    """EXIT CRITERION: Cold-snap scenario produces a RED alert with
    a recommended extra fuel quantity.

    Approach: run the simulation to mid-winter (day 150), then run a
    forecast from a cold state with reduced fuel. The forecast should
    show fuel exhaustion before resupply, producing a RED consumable
    alert with extra_quantity.
    """
    cfg = load_station(BASE / "stations" / "bharati.yaml")
    params = load_params(BASE / "params.yaml")

    # Create cold-snap config
    cold_cfg = dict(cfg)
    cold_weather = dict(cfg["weather"])
    cold_weather["winter_temp_avg"] = {"value": -35.0, "unit": "C", "source": "cold snap"}
    cold_weather["summer_temp_avg"] = {"value": -10.0, "unit": "C", "source": "cold snap"}
    cold_cfg["weather"] = cold_weather

    # Run cold scenario to day 200
    engine = SimulationEngine(cold_cfg, params, seed=42)
    result = engine.run(days=200)
    state = result.final_state
    graph = AssetGraph(cold_cfg)

    # Run forecast from day 200
    fc = run_forecast(state, graph, cold_cfg, params,
                      n_runs=50, horizon_days=180, dt_hours=6.0,
                      resupply_day=350.0)

    # Derive alerts including forecast
    alerts = derive_alerts(state, graph, params, forecast=fc,
                          resupply_day=350.0)

    # Should have RED consumable alert for fuel
    red_fuel_alerts = [
        a for a in alerts
        if a.severity == AlertSeverity.RED
        and a.category == AlertCategory.CONSUMABLE
        and "fuel" in a.id.lower()
    ]

    assert len(red_fuel_alerts) > 0, (
        f"No RED fuel alert from cold-snap scenario. "
        f"Alerts: {[(a.id, a.severity) for a in alerts]}"
    )

    # Should have a recommended extra fuel quantity
    fuel_alert = red_fuel_alerts[0]
    assert fuel_alert.extra_quantity is not None, "RED fuel alert has no extra_quantity"
    assert fuel_alert.extra_quantity > 0, "extra_quantity should be positive"
    assert fuel_alert.extra_quantity_unit == "L", "extra_quantity should be in litres"

    print(f"\nCOLD-SNAP ALERT:")
    print(f"  Severity: {fuel_alert.severity}")
    print(f"  Cause: {fuel_alert.cause}")
    print(f"  Evidence: {fuel_alert.evidence}")
    print(f"  Consequence: {fuel_alert.consequence}")
    print(f"  Action: {fuel_alert.recommended_action}")
    print(f"  Extra fuel needed: {fuel_alert.extra_quantity:,.0f} L")
