"""Alert engine: derives actionable alerts from station state and forecasts.

Every alert has:
  - id: unique identifier
  - severity: RED / AMBER / GREEN
  - category: consumable | equipment | thermal | power | logistics | weather
  - cause: what triggered the alert
  - evidence: data supporting the alert
  - consequence: what happens if no action is taken
  - recommended_action: what to do
  - extra_quantity: for consumable alerts, how much extra to order
  - acknowledged: whether a human has acknowledged it
  - timestamp_hours: simulation time when alert was raised

Alerts are derived from:
  1. Current state (instant alerts: faults, temperature, power)
  2. Forecast (predictive alerts: exhaustion before resupply)
  3. Configuration changes (resupply shift → logistics alert)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .state import StationState
from .asset_graph import AssetGraph
from .config import param_value
from .types import AssetType
from .forecast import ForecastResult, ConsumableForecast


class AlertSeverity(str, Enum):
    RED = "RED"
    AMBER = "AMBER"
    GREEN = "GREEN"


class AlertCategory(str, Enum):
    CONSUMABLE = "consumable"
    EQUIPMENT = "equipment"
    THERMAL = "thermal"
    POWER = "power"
    LOGISTICS = "logistics"
    WEATHER = "weather"


@dataclass
class Alert:
    """A single actionable alert."""
    id: str
    severity: AlertSeverity
    category: AlertCategory
    cause: str
    evidence: str
    consequence: str
    recommended_action: str
    extra_quantity: float | None = None  # units depend on commodity
    extra_quantity_unit: str | None = None
    acknowledged: bool = False
    timestamp_hours: float = 0.0

    def acknowledge(self) -> None:
        """Mark this alert as acknowledged by a human operator."""
        self.acknowledged = True


def derive_alerts(
    state: StationState,
    graph: AssetGraph,
    params: dict[str, Any],
    forecast: ForecastResult | None = None,
    resupply_day: float | None = None,
    resupply_delay_days: float = 0.0,
) -> list[Alert]:
    """Derive all alerts from current state and optional forecast.

    Args:
        state: Current station state.
        graph: Asset graph.
        params: Global parameters.
        forecast: Optional forecast result for predictive alerts.
        resupply_day: Nominal resupply day (overridden if shifted).
        resupply_delay_days: Days the resupply is delayed.

    Returns:
        List of Alert objects, sorted by severity (RED first).
    """
    alerts: list[Alert] = []
    t = state.time_hours

    if resupply_day is None:
        resupply_day = param_value(params, "resupply_default_day")
    effective_resupply = resupply_day + resupply_delay_days

    # ================================================================
    # 1. CONSUMABLE ALERTS (from forecast)
    # ================================================================
    if forecast:
        for sid, fc in forecast.consumables.items():
            # Compute extra quantity needed to reach resupply
            # extra = depletion_rate × |margin| (if margin is negative)

            if fc.fraction_exhausting_before_resupply > 0.5:
                # P50 exhaustion before resupply → RED
                days_short = abs(fc.margin_p50) if fc.margin_p50 < 0 else 0
                extra = fc.depletion_rate_per_day * (days_short + 30)  # 30-day buffer
                alerts.append(Alert(
                    id=f"consumable.exhaust.{fc.commodity}.{sid}",
                    severity=AlertSeverity.RED,
                    category=AlertCategory.CONSUMABLE,
                    cause=f"{fc.commodity.title()} projected to exhaust before resupply "
                          f"(P50: day {fc.p50_days:.0f}, resupply: day {effective_resupply:.0f})",
                    evidence=f"Current level: {fc.current_level:,.0f} {fc.unit}. "
                             f"Depletion rate: {fc.depletion_rate_per_day:,.0f} {fc.unit}/day. "
                             f"{fc.fraction_exhausting_before_resupply*100:.0f}% of scenarios exhaust before resupply.",
                    consequence=f"Station will run out of {fc.commodity} approximately "
                                f"{abs(fc.margin_p50):.0f} days before resupply arrives.",
                    recommended_action=f"Request emergency {fc.commodity} delivery of "
                                       f"{extra:,.0f} {fc.unit} or reduce consumption.",
                    extra_quantity=extra,
                    extra_quantity_unit=fc.unit,
                    timestamp_hours=t,
                ))
            elif fc.fraction_exhausting_before_resupply > 0.1:
                # P10 exhaustion before resupply → AMBER
                extra = fc.depletion_rate_per_day * 30  # 30-day buffer
                alerts.append(Alert(
                    id=f"consumable.risk.{fc.commodity}.{sid}",
                    severity=AlertSeverity.AMBER,
                    category=AlertCategory.CONSUMABLE,
                    cause=f"{fc.commodity.title()} may exhaust before resupply in adverse conditions "
                          f"(P90: day {fc.p90_days:.0f})",
                    evidence=f"Current level: {fc.current_level:,.0f} {fc.unit}. "
                             f"{fc.fraction_exhausting_before_resupply*100:.0f}% of scenarios exhaust before resupply.",
                    consequence=f"Risk of {fc.commodity} shortage if conditions worsen.",
                    recommended_action=f"Monitor closely. Consider pre-positioning "
                                       f"{extra:,.0f} {fc.unit} of extra {fc.commodity}.",
                    extra_quantity=extra,
                    extra_quantity_unit=fc.unit,
                    timestamp_hours=t,
                ))

    # ================================================================
    # 2. EQUIPMENT ALERTS (condition + faults)
    # ================================================================
    for gen_asset in graph.get_by_type(AssetType.GENERATOR):
        gs = state.generators.get(gen_asset.id)
        if gs is None:
            continue

        if gs.faulted:
            alerts.append(Alert(
                id=f"equipment.fault.{gen_asset.id}",
                severity=AlertSeverity.RED,
                category=AlertCategory.EQUIPMENT,
                cause=f"{gen_asset.label} has an active fault",
                evidence=f"Condition score: {gs.condition:.2f}. "
                         f"Running hours: {gs.running_hours:,.0f}h. "
                         f"Operating at {(1-gs.fault_capacity_reduction)*100:.0f}% capacity.",
                consequence="Reduced power generation capacity. Risk of complete failure "
                            "if not repaired. Other generators must carry additional load.",
                recommended_action="Schedule immediate maintenance. Reduce non-essential "
                                   "electrical load. Ensure backup generator is on standby.",
                timestamp_hours=t,
            ))
        elif gs.condition < 0.3:
            alerts.append(Alert(
                id=f"equipment.condition.{gen_asset.id}",
                severity=AlertSeverity.AMBER,
                category=AlertCategory.EQUIPMENT,
                cause=f"{gen_asset.label} condition degraded to {gs.condition:.0%}",
                evidence=f"Running hours: {gs.running_hours:,.0f}h. "
                         f"Condition score: {gs.condition:.2f}. "
                         f"Fault probability increasing.",
                consequence="Elevated risk of generator fault. May require unplanned "
                            "maintenance or replacement parts.",
                recommended_action="Schedule preventive maintenance during next calm period. "
                                   "Verify spare parts availability.",
                timestamp_hours=t,
            ))

    # ================================================================
    # 3. THERMAL ALERTS
    # ================================================================
    for zone_asset in graph.get_by_type(AssetType.ZONE):
        zs = state.zones.get(zone_asset.id)
        if zs is None:
            continue
        target = zone_asset.params.get("target_temp", 20.0)
        delta = target - zs.temperature

        if delta > 10.0:
            alerts.append(Alert(
                id=f"thermal.underheat.{zone_asset.id}",
                severity=AlertSeverity.RED,
                category=AlertCategory.THERMAL,
                cause=f"{zone_asset.label} is {delta:.1f} deg C below target",
                evidence=f"Current: {zs.temperature:.1f} deg C, target: {target:.0f} deg C. "
                         f"Heating applied: {zs.heating_kw:.1f} kW, "
                         f"demand: {zs.heating_demand_kw:.1f} kW.",
                consequence="Risk of equipment damage, frozen pipes, and crew health impact.",
                recommended_action="Check heating system. Reduce ventilation. "
                                   "Move crew to warmer zones if needed.",
                timestamp_hours=t,
            ))
        elif delta > 5.0:
            alerts.append(Alert(
                id=f"thermal.cool.{zone_asset.id}",
                severity=AlertSeverity.AMBER,
                category=AlertCategory.THERMAL,
                cause=f"{zone_asset.label} is {delta:.1f} deg C below target",
                evidence=f"Current: {zs.temperature:.1f} deg C, target: {target:.0f} deg C.",
                consequence="Crew comfort reduced. Increased heating energy consumption.",
                recommended_action="Monitor. Increase heating allocation if possible.",
                timestamp_hours=t,
            ))

    # ================================================================
    # 4. POWER ALERTS
    # ================================================================
    if state.unmet_demand_kw > 1.0:
        alerts.append(Alert(
            id="power.shortage",
            severity=AlertSeverity.RED,
            category=AlertCategory.POWER,
            cause=f"Unmet electrical demand: {state.unmet_demand_kw:.1f} kW",
            evidence=f"Total demand: {state.total_electrical_load_kw:.1f} kW. "
                     f"Generation: {state.total_generation_kw:.1f} kW. "
                     f"Renewables: {state.renewable_generation_kw:.1f} kW.",
            consequence="Load shedding active. Snow-melt, heating, or science equipment "
                        "may be curtailed.",
            recommended_action="Bring additional generator online. Reduce non-essential loads.",
            timestamp_hours=t,
        ))

    # ================================================================
    # 5. LOGISTICS ALERTS (resupply shift)
    # ================================================================
    if resupply_delay_days > 0:
        alerts.append(Alert(
            id="logistics.resupply_delay",
            severity=AlertSeverity.RED if resupply_delay_days > 30 else AlertSeverity.AMBER,
            category=AlertCategory.LOGISTICS,
            cause=f"Resupply delayed by {resupply_delay_days:.0f} days "
                  f"(new arrival: day {effective_resupply:.0f})",
            evidence=f"Original resupply: day {resupply_day:.0f}. "
                     f"Delayed to: day {effective_resupply:.0f}.",
            consequence="All consumable margins reduced. May require rationing or "
                        "emergency resupply.",
            recommended_action="Re-run forecasts with new resupply date. "
                               "Implement conservation measures immediately.",
            timestamp_hours=t,
        ))

    # ================================================================
    # 6. WEATHER ALERTS
    # ================================================================
    # These are derived from current environment, passed through state
    # In practice, check if a storm is active or if temp is extreme
    # (The step function doesn't store env in state, so we check zone temps as proxy)
    avg_zone_temp = sum(z.temperature for z in state.zones.values()) / max(1, len(state.zones))
    if avg_zone_temp < 5.0:
        # Extremely cold conditions are causing underheat across all zones
        alerts.append(Alert(
            id="weather.extreme_cold",
            severity=AlertSeverity.AMBER,
            category=AlertCategory.WEATHER,
            cause="Extreme cold conditions detected across station",
            evidence=f"Average zone temperature: {avg_zone_temp:.1f} deg C. "
                     f"Heating demand: {state.total_heating_demand_kw:.1f} kW.",
            consequence="Increased fuel consumption. Risk of equipment freezing.",
            recommended_action="Seal all external openings. Minimize outdoor operations. "
                               "Monitor fuel levels closely.",
            timestamp_hours=t,
        ))

    # Sort by severity: RED first, then AMBER, then GREEN
    severity_order = {AlertSeverity.RED: 0, AlertSeverity.AMBER: 1, AlertSeverity.GREEN: 2}
    alerts.sort(key=lambda a: severity_order.get(a.severity, 3))

    return alerts
