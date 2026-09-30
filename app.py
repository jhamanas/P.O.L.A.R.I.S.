"""Antarctic Station Digital Twin â€” Streamlit Dashboard (Phase 3).

Run with:  streamlit run app.py

Features:
  - Overview with margin days, alert feed, and SIMULATED TELEMETRY badge
  - Sidebar scenario controls (temp offset, wind, crew, resupply delay, faults)
  - Forecast fan chart with resupply line
  - Energy-flow view (generation mix, battery SOC)
  - SVG station plan with zones coloured by status
"""

import streamlit as st
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pathlib import Path
from antarctic_twin.engine import SimulationEngine
from antarctic_twin.config import load_station, load_params, param_value
from antarctic_twin.asset_graph import AssetGraph
from antarctic_twin.forecast import run_forecast
from antarctic_twin.alerts import derive_alerts, AlertSeverity, AlertCategory
from antarctic_twin.types import AssetType
from antarctic_twin.scenarios import (
    PRESETS, run_scenario, run_sensitivity, run_backtest,
    Scenario, fork_scenario,
)
from antarctic_twin.database import AuditLogger, Role, User, check_permission, get_provenance
from antarctic_twin.interfaces import YamlDataSource

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Antarctic Station Digital Twin",
    page_icon="logo.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE = Path(__file__).parent

# Phase 5 initialization
data_source = YamlDataSource(BASE)
audit_logger = AuditLogger(BASE / "audit.db")

# ---------------------------------------------------------------------------
# Authentication & Role Simulation
# ---------------------------------------------------------------------------
if "user" not in st.session_state:
    st.session_state.user = User(username="admin", role=Role.ADMIN)

with st.sidebar:
    st.image("logo.png", width=60)
    st.title("User Session")
    
    # Role switcher for demonstration
    user_role = st.selectbox(
        "Current Role (Simulated)", 
        [Role.ADMIN, Role.OPERATOR, Role.SCIENTIST, Role.GUEST],
        index=0
    )
    if user_role != st.session_state.user.role:
        st.session_state.user = User(username=user_role.value.lower(), role=user_role)
        audit_logger.log_action(st.session_state.user, "Login", f"Switched role to {user_role.value}")
        st.rerun()

    st.divider()
    st.title("Scenario Controls")
    st.caption("Adjust parameters to explore what-if scenarios")

    station_name = st.selectbox(
        "Station", data_source.list_stations(), format_func=str.title
    )

    st.divider()
    st.subheader("Environment")
    temp_offset = st.slider(
        "Temperature offset (deg C)", -20.0, 10.0, 0.0, 1.0,
        help="Shift the entire temperature profile up or down"
    )
    wind_mult = st.slider(
        "Wind multiplier", 0.5, 2.0, 1.0, 0.1,
        help="Scale wind speeds (1.0 = normal)"
    )

    st.divider()
    st.subheader("Operations")
    crew_delta = st.slider(
        "Crew change", -10, 10, 0, 1,
        help="Add or remove crew from the winter complement"
    )
    resupply_delay = st.slider(
        "Resupply delay (days)", 0, 90, 0, 5,
        help="Days the resupply ship is delayed"
    )

    st.divider()
    st.subheader("Faults")
    gen_fault = st.selectbox(
        "Inject generator fault",
        ["None", "Generator 1", "Generator 2", "Generator 3"],
        help="Force a fault on a specific generator at day 0"
    )

    st.divider()
    sim_days = st.slider("Simulation days", 30, 365, 365, 5)
    forecast_runs = st.slider("Forecast MC runs", 20, 500, 100, 10,
                              help="More runs = smoother fan chart, slower")

    run_btn = st.button(
        "Run Simulation",
        type="primary",
        use_container_width=True,
        disabled=not check_permission(st.session_state.user, "run_simulation"),
    )
    if not check_permission(st.session_state.user, "run_simulation"):
        st.caption("Your role does not have permission to run simulations.")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
col_title, col_badge = st.columns([5, 1])
with col_title:
    st.title("Antarctic Station Digital Twin")
with col_badge:
    st.markdown(
        '<div style="background:#FF6B35;color:white;padding:8px 12px;'
        'border-radius:4px;text-align:center;margin-top:16px;font-weight:bold;'
        'font-size:0.8em;">'
        'SIMULATED TELEMETRY</div>',
        unsafe_allow_html=True,
    )

st.caption("SIH26060 â€” Digital Platform for Remote Management of Indian Antarctic Research Stations")


# ---------------------------------------------------------------------------
# Run simulation
# ---------------------------------------------------------------------------
if not run_btn and "result" not in st.session_state:
    st.info("Configure scenario in the sidebar and click **Run Simulation** to begin.")
    st.stop()

if run_btn:
    audit_logger.log_action(
        st.session_state.user, 
        "Run Simulation", 
        f"Station: {station_name}, Temp Offset: {temp_offset}, Wind Mult: {wind_mult}"
    )
    
    station_config = data_source.get_station_config(station_name)
    params = data_source.get_global_params()

    # Apply scenario overrides via fork_scenario (deep copy, no mutation)
    custom_scenario = Scenario(
        name="Custom UI Overlay",
        temp_offset=temp_offset,
        wind_mult=wind_mult,
        crew_delta=crew_delta,
        resupply_delay_days=resupply_delay,
    )
    modified_config = fork_scenario(station_config, params, custom_scenario)

    engine = SimulationEngine(modified_config, params, seed=42)

    # Inject generator fault if requested
    if gen_fault != "None":
        gen_num = int(gen_fault.split()[-1])
        gen_id = f"{station_name}.gen{gen_num}"
        if gen_id in engine.initial_state.generators:
            engine.initial_state.generators[gen_id].faulted = True
            engine.initial_state.generators[gen_id].fault_capacity_reduction = 0.5

    with st.spinner("Running simulation..."):
        result = engine.run(days=sim_days)

    # Run forecast from midpoint
    graph = AssetGraph(modified_config)
    mid_day = min(sim_days // 2, 150)
    mid_state = result.history[mid_day * 24]
    resupply_day = param_value(params, "resupply_default_day")

    with st.spinner("Running Monte Carlo forecast..."):
        forecast = run_forecast(
            mid_state, graph, modified_config, params,
            n_runs=forecast_runs,
            horizon_days=min(sim_days - mid_day, 250),
            dt_hours=6.0,
            resupply_day=resupply_day + resupply_delay,
        )

    # Derive alerts
    alerts = derive_alerts(
        result.final_state, graph, params,
        forecast=forecast,
        resupply_day=resupply_day,
        resupply_delay_days=resupply_delay,
    )

    # Store in session
    st.session_state.result = result
    st.session_state.forecast = forecast
    st.session_state.alerts = alerts
    st.session_state.graph = graph
    st.session_state.params = params
    st.session_state.station_config = modified_config
    st.session_state.resupply_day = resupply_day + resupply_delay
    st.session_state.mid_day = mid_day

# Retrieve from session
result = st.session_state.result
forecast = st.session_state.forecast
alerts = st.session_state.alerts
graph = st.session_state.graph
params = st.session_state.params
resupply_day_eff = st.session_state.resupply_day
mid_day = st.session_state.mid_day
final = result.final_state


# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------
tab_overview, tab_forecast, tab_energy, tab_station, tab_live, tab_alerts, tab_scenarios, tab_validation, tab_provenance = st.tabs(
    ["Overview", "Forecast", "Energy", "Station Plan", "Live Telemetry", "Alerts", "Scenarios", "Validation", "Provenance & Audit"]
)


# ===== TAB 1: OVERVIEW =====
with tab_overview:
    st.subheader("Station Status")

    # Margin days
    fuel_fc = next((f for f in forecast.consumables.values() if f.commodity == "fuel"), None)
    water_fc = next((f for f in forecast.consumables.values() if f.commodity == "water"), None)
    food_fc = next((f for f in forecast.consumables.values() if f.commodity == "food"), None)

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        fuel_ids = [sid for sid in final.storage if "fuel" in sid]
        fuel_level = final.storage[fuel_ids[0]].level if fuel_ids else 0
        st.metric("Fuel", f"{fuel_level:,.0f} L",
                  delta=f"Margin: {fuel_fc.margin_p50:+.0f}d" if fuel_fc else None,
                  delta_color="normal" if fuel_fc and fuel_fc.margin_p50 > 0 else "inverse")
    with c2:
        water_ids = [sid for sid in final.storage if "water" in sid]
        water_level = final.storage[water_ids[0]].level if water_ids else 0
        st.metric("Water", f"{water_level:,.0f} L")
    with c3:
        food_ids = [sid for sid in final.storage if "food" in sid]
        food_level = final.storage[food_ids[0]].level if food_ids else 0
        st.metric("Food", f"{food_level:,.0f} kg")
    with c4:
        avg_temp = sum(z.temperature for z in final.zones.values()) / max(1, len(final.zones))
        st.metric("Avg Zone Temp", f"{avg_temp:.1f} C")
    with c5:
        n_faulted = sum(1 for g in final.generators.values() if g.faulted)
        st.metric("Gen Faults", f"{n_faulted}",
                  delta="FAULT" if n_faulted > 0 else "OK",
                  delta_color="inverse" if n_faulted > 0 else "normal")

    # Consumable charts
    st.subheader("Consumable Levels")
    days = [s.time_hours / 24.0 for s in result.history]

    fig_cons = make_subplots(rows=1, cols=3,
                             subplot_titles=["Fuel (L)", "Water (L)", "Food (kg)"])

    if fuel_ids:
        fig_cons.add_trace(
            go.Scatter(x=days,
                       y=[s.storage[fuel_ids[0]].level for s in result.history],
                       name="Fuel", line=dict(color="#EF553B", width=2)),
            row=1, col=1)
    if water_ids:
        fig_cons.add_trace(
            go.Scatter(x=days,
                       y=[s.storage[water_ids[0]].level for s in result.history],
                       name="Water", line=dict(color="#636EFA", width=2)),
            row=1, col=2)
    if food_ids:
        fig_cons.add_trace(
            go.Scatter(x=days,
                       y=[s.storage[food_ids[0]].level for s in result.history],
                       name="Food", line=dict(color="#00CC96", width=2)),
            row=1, col=3)

    fig_cons.update_layout(height=300, showlegend=False,
                           margin=dict(l=40, r=20, t=40, b=30))
    for i in range(1, 4):
        fig_cons.update_xaxes(title_text="Day", row=1, col=i)
    st.plotly_chart(fig_cons, use_container_width=True)

    # Zone temperatures
    st.subheader("Zone Temperatures")
    fig_temp = go.Figure()
    colors = ["#EF553B", "#636EFA", "#00CC96", "#AB63FA", "#FFA15A"]
    for idx, zid in enumerate(result.history[0].zones):
        label = zid.split(".")[-1].title()
        fig_temp.add_trace(go.Scatter(
            x=days,
            y=[s.zones[zid].temperature for s in result.history],
            name=label, line=dict(color=colors[idx % len(colors)], width=2),
        ))
    fig_temp.update_layout(height=300, yaxis_title="Temperature (C)",
                           xaxis_title="Day",
                           margin=dict(l=40, r=20, t=20, b=30))
    st.plotly_chart(fig_temp, use_container_width=True)

    # Alert summary
    st.subheader("Alert Summary")
    n_red = sum(1 for a in alerts if a.severity == AlertSeverity.RED)
    n_amber = sum(1 for a in alerts if a.severity == AlertSeverity.AMBER)
    ac1, ac2, ac3 = st.columns(3)
    with ac1:
        st.metric("RED Alerts", n_red)
    with ac2:
        st.metric("AMBER Alerts", n_amber)
    with ac3:
        st.metric("Total Alerts", len(alerts))

    if alerts:
        for alert in alerts[:5]:
            severity_color = "#FF4444" if alert.severity == AlertSeverity.RED else "#FFB020"
            st.markdown(
                f'<div style="border-left:4px solid {severity_color};padding:8px 12px;'
                f'margin:4px 0;background:#1a1a2e;border-radius:0 4px 4px 0;">'
                f'<b style="color:{severity_color}">[{alert.severity.value}]</b> '
                f'{alert.cause}</div>',
                unsafe_allow_html=True,
            )
        if len(alerts) > 5:
            st.caption(f"...and {len(alerts)-5} more. See Alerts tab for details.")


# ===== TAB 2: FORECAST FAN CHART =====
with tab_forecast:
    st.subheader("Monte Carlo Forecast")
    st.caption(f"Forecast from day {mid_day} | {forecast.n_runs} Monte Carlo runs | "
               f"Resupply target: day {resupply_day_eff:.0f}")

    if forecast.fuel_trajectories is not None and forecast.fuel_trajectories.shape[1] > 0:
        n_days = forecast.fuel_trajectories.shape[1]
        forecast_days = np.arange(mid_day, mid_day + n_days)

        fig_fan = go.Figure()

        # P10-P90 band
        p10 = np.percentile(forecast.fuel_trajectories, 90, axis=0)
        p25 = np.percentile(forecast.fuel_trajectories, 75, axis=0)
        p50 = np.percentile(forecast.fuel_trajectories, 50, axis=0)
        p75 = np.percentile(forecast.fuel_trajectories, 25, axis=0)
        p90 = np.percentile(forecast.fuel_trajectories, 10, axis=0)

        # P10-P90 band (light)
        fig_fan.add_trace(go.Scatter(
            x=np.concatenate([forecast_days, forecast_days[::-1]]),
            y=np.concatenate([p10, p90[::-1]]),
            fill="toself", fillcolor="rgba(99,110,250,0.15)",
            line=dict(color="rgba(0,0,0,0)"),
            name="P10-P90 range", showlegend=True,
        ))

        # P25-P75 band (darker)
        fig_fan.add_trace(go.Scatter(
            x=np.concatenate([forecast_days, forecast_days[::-1]]),
            y=np.concatenate([p25, p75[::-1]]),
            fill="toself", fillcolor="rgba(99,110,250,0.3)",
            line=dict(color="rgba(0,0,0,0)"),
            name="P25-P75 range", showlegend=True,
        ))

        # P50 line
        fig_fan.add_trace(go.Scatter(
            x=forecast_days, y=p50,
            line=dict(color="#636EFA", width=3),
            name="P50 (median)",
        ))

        # Resupply line
        fig_fan.add_vline(x=resupply_day_eff, line_dash="dash",
                          line_color="#00CC96", annotation_text="Resupply",
                          annotation_position="top right")

        # Zero line
        fig_fan.add_hline(y=0, line_dash="dot", line_color="#EF553B",
                          annotation_text="Exhaustion")

        fig_fan.update_layout(
            height=450, title="Fuel Level Forecast (Fan Chart)",
            xaxis_title="Day of Year", yaxis_title="Fuel (L)",
            margin=dict(l=50, r=20, t=60, b=40),
        )
        st.plotly_chart(fig_fan, use_container_width=True)

        # Margin days display
        if fuel_fc:
            mc1, mc2, mc3, mc4 = st.columns(4)
            with mc1:
                color = "normal" if fuel_fc.margin_p50 > 0 else "inverse"
                st.metric("P50 Margin", f"{fuel_fc.margin_p50:+.0f} days",
                          delta_color=color)
            with mc2:
                st.metric("P90 Margin (worst)", f"{fuel_fc.margin_p90:+.0f} days")
            with mc3:
                st.metric("P10 Margin (best)", f"{fuel_fc.margin_p10:+.0f} days")
            with mc4:
                st.metric("Runs exhausting", f"{fuel_fc.fraction_exhausting_before_resupply*100:.0f}%")

    else:
        st.warning("No fuel trajectory data available.")

    # Water and food forecasts (compact)
    st.divider()
    wc1, wc2 = st.columns(2)

    with wc1:
        st.markdown("**Water Forecast**")
        if water_fc:
            st.write(f"P50 margin: **{water_fc.margin_p50:+.0f} days**")
            st.write(f"Exhaustion risk: **{water_fc.fraction_exhausting_before_resupply*100:.0f}%**")
        else:
            st.write("No water forecast.")

    with wc2:
        st.markdown("**Food Forecast**")
        if food_fc:
            st.write(f"P50 margin: **{food_fc.margin_p50:+.0f} days**")
            st.write(f"Exhaustion risk: **{food_fc.fraction_exhausting_before_resupply*100:.0f}%**")
        else:
            st.write("No food forecast.")


# ===== TAB 3: ENERGY FLOW =====
with tab_energy:
    st.subheader("Energy Flow")

    # Generation mix over time
    fig_energy = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        subplot_titles=["Generation Mix (kW)", "Battery SOC (kWh)", "Heating Demand vs Supply (kW)"]
    )

    # Generator power
    fig_energy.add_trace(go.Scatter(
        x=days, y=[s.total_generation_kw for s in result.history],
        name="Generators", fill="tozeroy",
        fillcolor="rgba(239,85,59,0.3)", line=dict(color="#EF553B", width=1),
    ), row=1, col=1)

    # Renewable power
    fig_energy.add_trace(go.Scatter(
        x=days, y=[s.renewable_generation_kw for s in result.history],
        name="Renewables", fill="tozeroy",
        fillcolor="rgba(0,204,150,0.3)", line=dict(color="#00CC96", width=1),
    ), row=1, col=1)

    # Demand line
    fig_energy.add_trace(go.Scatter(
        x=days, y=[s.total_electrical_load_kw for s in result.history],
        name="Demand", line=dict(color="#FFA15A", width=2, dash="dot"),
    ), row=1, col=1)

    # Battery SOC
    fig_energy.add_trace(go.Scatter(
        x=days, y=[s.battery.soc_kwh for s in result.history],
        name="Battery SOC", line=dict(color="#AB63FA", width=2),
        fill="tozeroy", fillcolor="rgba(171,99,250,0.2)",
    ), row=2, col=1)

    # Heating
    fig_energy.add_trace(go.Scatter(
        x=days, y=[s.total_heating_demand_kw for s in result.history],
        name="Heating Demand", line=dict(color="#EF553B", width=1),
    ), row=3, col=1)
    fig_energy.add_trace(go.Scatter(
        x=days, y=[s.waste_heat_kw for s in result.history],
        name="Waste Heat", line=dict(color="#00CC96", width=1),
        fill="tozeroy", fillcolor="rgba(0,204,150,0.2)",
    ), row=3, col=1)

    fig_energy.update_layout(height=650, margin=dict(l=50, r=20, t=40, b=30))
    fig_energy.update_xaxes(title_text="Day", row=3, col=1)
    st.plotly_chart(fig_energy, use_container_width=True)

    # Generator status table
    st.subheader("Generator Status")
    gen_data = []
    for gid, gs in final.generators.items():
        label = gid.split(".")[-1].upper()
        gen_data.append({
            "Generator": label,
            "Running": "Yes" if gs.running else "Standby",
            "Load": f"{gs.load_fraction*100:.0f}%",
            "Hours": f"{gs.running_hours:,.0f}",
            "Fuel Used (L)": f"{gs.fuel_consumed_l:,.0f}",
            "Condition": f"{gs.condition:.0%}",
            "Wear & Tear": f"{(1 - gs.condition):.0%}",
            "Status": "FAULTED" if gs.faulted else "OK",
        })
    st.table(gen_data)


# ===== TAB 4: STATION PLAN (3D Digital Twin) =====
with tab_station:
    import pydeck as pdk
    import pandas as pd

    st.subheader("3D Spatial Model")
    st.caption(
        "Station zones extruded by heating demand and colored by thermal stress. "
        "Drag to rotate, scroll to zoom."
    )

    # --- Build 3D data for pydeck ---
    lat, lon = -69.408, 76.193  # Bharati station coordinates
    zones = list(final.zones.items())
    zone_assets = {a.id: a for a in graph.get_by_type(AssetType.ZONE)}

    building_data = []
    for i, (zid, zs) in enumerate(zones):
        z_asset = zone_assets.get(zid)
        label = zid.split(".")[-1].title() if not z_asset else z_asset.label
        target = z_asset.params.get("target_temp", 20.0) if z_asset else 20.0
        temp = zs.temperature

        delta = target - temp
        if delta > 10:
            color = [239, 85, 59, 200]
        elif delta > 5:
            color = [255, 176, 32, 200]
        elif delta > 2:
            color = [255, 161, 90, 200]
        else:
            color = [0, 204, 150, 200]

        building_data.append({
            "name": label,
            "coordinates": [lon + (i * 0.0015) - 0.001, lat],
            "elevation": max(zs.heating_demand_kw * 1.5, 10),
            "color": color,
            "temp": f"{temp:.1f}",
            "target": f"{target:.0f}",
            "demand": f"{zs.heating_demand_kw:.0f}",
        })

    # Generator columns (offset row below zones)
    gens_3d = list(final.generators.items())
    for i, (gid, gs) in enumerate(gens_3d):
        label = gid.split(".")[-1].upper()
        if gs.faulted:
            color = [239, 85, 59, 220]
        elif gs.running:
            color = [0, 204, 150, 220]
        else:
            color = [80, 80, 80, 180]

        building_data.append({
            "name": f"{label} ({'FAULT' if gs.faulted else f'{gs.load_fraction*100:.0f}%'})",
            "coordinates": [lon + (i * 0.0012) - 0.0005, lat - 0.0008],
            "elevation": max(gs.load_fraction * 80, 8),
            "color": color,
            "temp": f"Cond: {gs.condition:.0%}",
            "target": f"Wear: {(1-gs.condition):.0%}",
            "demand": f"{gs.running_hours:.0f}h run",
        })

    df_3d = pd.DataFrame(building_data)

    zone_layer = pdk.Layer(
        "ColumnLayer",
        data=df_3d,
        get_position="coordinates",
        get_elevation="elevation",
        elevation_scale=1,
        radius=25,
        get_fill_color="color",
        pickable=True,
        auto_highlight=True,
    )

    view_state = pdk.ViewState(
        latitude=lat - 0.0003,
        longitude=lon + 0.001,
        zoom=16,
        pitch=55,
        bearing=30,
    )

    st.pydeck_chart(pdk.Deck(
        layers=[zone_layer],
        initial_view_state=view_state,
        tooltip={"text": "{name}\nTemp: {temp} C (Target: {target} C)\nDemand: {demand}"},
    ))

    # --- 2D SVG detail view (collapsible) ---
    with st.expander("2D Station Layout (detailed)", expanded=False):
        def zone_color(temp: float, target: float) -> str:
            delta = target - temp
            if delta > 10:
                return "#EF553B"  # red - severely underheat
            elif delta > 5:
                return "#FFB020"  # amber
            elif delta > 2:
                return "#FFA15A"  # light amber
            else:
                return "#00CC96"  # green - on target

        zones = list(final.zones.items())
        zone_assets = {a.id: a for a in graph.get_by_type(AssetType.ZONE)}

        # Build SVG
        svg_width = 800
        svg_height = 400
        zone_width = 220
        zone_height = 140
        gap = 20
        start_x = (svg_width - (zone_width * len(zones) + gap * (len(zones) - 1))) // 2
        start_y = 80

        svg_parts = [
            f'<svg width="{svg_width}" height="{svg_height}" '
            f'xmlns="http://www.w3.org/2000/svg" '
            f'style="background:#0d1117;border-radius:8px;">',
            # Station label
            f'<text x="{svg_width//2}" y="40" text-anchor="middle" '
            f'fill="white" font-size="18" font-weight="bold">'
            f'{result.station_name} Station Plan</text>',
            f'<text x="{svg_width//2}" y="60" text-anchor="middle" '
            f'fill="#888" font-size="12">Zones coloured by thermal status</text>',
        ]

        for i, (zid, zs) in enumerate(zones):
            x = start_x + i * (zone_width + gap)
            y = start_y
            target = zone_assets[zid].params.get("target_temp", 20.0) if zid in zone_assets else 20.0
            color = zone_color(zs.temperature, target)
            label = zid.split(".")[-1].title()

            svg_parts.extend([
                # Zone rectangle
                f'<rect x="{x}" y="{y}" width="{zone_width}" height="{zone_height}" '
                f'rx="8" fill="{color}" opacity="0.85"/>',
                # Zone border
                f'<rect x="{x}" y="{y}" width="{zone_width}" height="{zone_height}" '
                f'rx="8" fill="none" stroke="white" stroke-width="1" opacity="0.3"/>',
                # Zone name
                f'<text x="{x + zone_width//2}" y="{y + 30}" text-anchor="middle" '
                f'fill="white" font-size="16" font-weight="bold">{label}</text>',
                # Temperature
                f'<text x="{x + zone_width//2}" y="{y + 60}" text-anchor="middle" '
                f'fill="white" font-size="28" font-weight="bold">{zs.temperature:.1f} C</text>',
                # Target
                f'<text x="{x + zone_width//2}" y="{y + 85}" text-anchor="middle" '
                f'fill="rgba(255,255,255,0.7)" font-size="12">Target: {target:.0f} C</text>',
                # Heating
                f'<text x="{x + zone_width//2}" y="{y + 105}" text-anchor="middle" '
                f'fill="rgba(255,255,255,0.6)" font-size="11">'
                f'Heating: {zs.heating_kw:.1f} kW</text>',
                # Status indicator
                f'<circle cx="{x + zone_width - 15}" cy="{y + 15}" r="6" fill="{color}"/>',
            ])

        # Generator row
        gen_y = start_y + zone_height + 40
        gens = list(final.generators.items())
        gen_box_w = 120
        gen_start_x = (svg_width - (gen_box_w * len(gens) + gap * (len(gens) - 1))) // 2

        svg_parts.append(
            f'<text x="{svg_width//2}" y="{gen_y}" text-anchor="middle" '
            f'fill="#888" font-size="12">Power Generation</text>'
        )

        for i, (gid, gs) in enumerate(gens):
            x = gen_start_x + i * (gen_box_w + gap)
            y = gen_y + 10
            label = gid.split(".")[-1].upper()
            color = "#EF553B" if gs.faulted else "#00CC96" if gs.running else "#444"

            svg_parts.extend([
                f'<rect x="{x}" y="{y}" width="{gen_box_w}" height="60" rx="6" '
                f'fill="{color}" opacity="0.7"/>',
                f'<text x="{x + gen_box_w//2}" y="{y + 22}" text-anchor="middle" '
                f'fill="white" font-size="13" font-weight="bold">{label}</text>',
                f'<text x="{x + gen_box_w//2}" y="{y + 40}" text-anchor="middle" '
                f'fill="white" font-size="11">'
                f'{"FAULT" if gs.faulted else f"{gs.load_fraction*100:.0f}%"}</text>',
                f'<text x="{x + gen_box_w//2}" y="{y + 54}" text-anchor="middle" '
                f'fill="rgba(255,255,255,0.6)" font-size="10">'
                f'Cond: {gs.condition:.0%}</text>',
            ])

        svg_parts.append("</svg>")
        svg = "\n".join(svg_parts)

        st.markdown(svg, unsafe_allow_html=True)

        # Legend
        st.markdown("""
        **Legend:**
        - :green[Green] = Within 2 deg C of target | :orange[Amber] = 2-10 deg C below target | :red[Red] = >10 deg C below target
        - Generator: :green[Green] = Running | :red[Red] = Faulted | Grey = Standby
        """)

# ===== TAB 5: LIVE TELEMETRY =====
with tab_live:
    import requests as _requests

    st.subheader("Live Station Telemetry")

    TELEMETRY_URL = "http://localhost:8765/latest"

    # Connection status and auto-refresh
    col_status, col_refresh = st.columns([3, 1])
    with col_refresh:
        auto_refresh = st.checkbox("Auto-refresh (5s)", value=False, key="auto_refresh_live")

    if auto_refresh:
        # Use st.empty + time-based rerun for auto-refresh
        import time as _time
        if "last_refresh" not in st.session_state:
            st.session_state.last_refresh = 0
        now = _time.time()
        if now - st.session_state.last_refresh > 5:
            st.session_state.last_refresh = now
            _time.sleep(0.1)
            st.rerun()

    # Fetch live data
    live_data = None
    try:
        resp = _requests.get(TELEMETRY_URL, timeout=2)
        resp.raise_for_status()
        live_data = resp.json()
        with col_status:
            st.success(
                f"Connected to telemetry server | "
                f"Station: **{live_data.get('station', '?').title()}** | "
                f"Sim Day: **{live_data.get('sim_day', '?')}** | "
                f"Day of Year: **{live_data.get('day_of_year', '?')}**"
            )
    except Exception:
        with col_status:
            st.error(
                "Cannot reach telemetry server. "
                "Start it with: `python telemetry_server.py`"
            )
        st.info(
            "The telemetry server simulates a real Antarctic station broadcasting "
            "live sensor data over HTTP and WebSockets. This tab proves the platform's "
            "architecture is ready to accept real hardware data from MoES."
        )
        st.code("python telemetry_server.py", language="bash")

    if live_data:
        # --- Weather gauges ---
        st.markdown("---")
        wx = live_data.get("weather", {})
        gen_list = live_data.get("generators", [])

        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Temperature", f"{wx.get('temperature_c', '?')} C")
        w2.metric("Wind Speed", f"{wx.get('wind_speed_ms', '?')} m/s")
        w3.metric("Heating Demand", f"{live_data.get('heating_demand_kw', '?')} kW")
        w4.metric("Total Generation", f"{live_data.get('total_gen_kw', '?')} kW")

        # --- Generator cards ---
        st.markdown("---")
        st.subheader("Generator Status")
        gen_cols = st.columns(len(gen_list)) if gen_list else []
        for col, g in zip(gen_cols, gen_list):
            with col:
                status = "RUNNING" if g["running"] else "OFFLINE"
                color = "green" if g["running"] else "red"
                st.markdown(f"**{g.get('label', g['id'])}**")
                st.markdown(f"Status: :{color}[{status}]")
                st.metric("Load", f"{g['load_pct']}%")
                st.metric("Condition", f"{g['condition_pct']}%")
                st.metric("Fuel Rate", f"{g['fuel_rate_lph']} L/h")

        # --- Raw JSON (for judges to inspect the data contract) ---
        with st.expander("Raw Telemetry Payload (JSON)", expanded=False):
            st.json(live_data)

        st.caption(
            "This data is served by `telemetry_server.py` over REST (GET /latest) "
            "and WebSockets (ws://localhost:8765/ws). When MoES provides real sensor "
            "hardware, this endpoint is swapped to the physical station with zero "
            "dashboard code changes."
        )


# ===== TAB 6: ALERTS =====
with tab_alerts:
    st.subheader(f"Active Alerts ({len(alerts)})")

    if not alerts:
        st.success("No active alerts. All systems nominal.")
    else:
        for alert in alerts:
            if alert.severity == AlertSeverity.RED:
                icon = "error"
                border_color = "#FF4444"
            elif alert.severity == AlertSeverity.AMBER:
                icon = "warning"
                border_color = "#FFB020"
            else:
                icon = "info"
                border_color = "#00CC96"

            with st.expander(
                f"{'[ACK] ' if alert.acknowledged else ''}[{alert.severity.value}] {alert.cause}",
                expanded=(alert.severity == AlertSeverity.RED and not alert.acknowledged),
            ):
                st.markdown(f"**Category:** {alert.category.value.title()}")
                st.markdown(f"**Cause:** {alert.cause}")
                st.markdown(f"**Evidence:** {alert.evidence}")
                st.markdown(f"**Consequence:** {alert.consequence}")
                st.markdown(f"**Recommended Action:** {alert.recommended_action}")
                if alert.extra_quantity:
                    st.markdown(f"**Extra Quantity Needed:** "
                                f"{alert.extra_quantity:,.0f} {alert.extra_quantity_unit}")

                # --- Remote Management Workflow ---
                if not alert.acknowledged:
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("Acknowledge & Assign", key=f"ack_{alert.id}"):
                            alert.acknowledge()
                            audit_logger.log_action(
                                st.session_state.user,
                                "Acknowledge Alert",
                                f"Alert {alert.id} acknowledged and assigned to Station Engineer."
                            )
                            st.rerun()
                    with col2:
                        if st.button("Dismiss (False Positive)", key=f"ign_{alert.id}"):
                            alert.acknowledge()
                            audit_logger.log_action(
                                st.session_state.user,
                                "Dismiss Alert",
                                f"Alert {alert.id} dismissed as false positive."
                            )
                            st.rerun()
                else:
                    st.success("Alert acknowledged and assigned to crew.")


# ---------------------------------------------------------------------------
# Weather panel (collapsible)
# ---------------------------------------------------------------------------
with st.expander("Weather Conditions", expanded=False):
    fig_wx = make_subplots(rows=1, cols=2,
                           subplot_titles=["Temperature (C)", "Wind Speed (m/s)"])
    wx_days = [e.day_of_year for e in result.weather_history]

    fig_wx.add_trace(go.Scatter(
        x=wx_days, y=[e.temperature for e in result.weather_history],
        line=dict(color="#636EFA", width=1), name="Temperature",
    ), row=1, col=1)

    fig_wx.add_trace(go.Scatter(
        x=wx_days, y=[e.wind_speed for e in result.weather_history],
        line=dict(color="#EF553B", width=1), name="Wind",
    ), row=1, col=2)

    fig_wx.update_layout(height=250, showlegend=False,
                         margin=dict(l=40, r=20, t=40, b=30))
    st.plotly_chart(fig_wx, use_container_width=True)


# ===== TAB 6: SCENARIOS =====
with tab_scenarios:
    st.subheader("Scenario Presets")
    st.caption("Run a preset scenario against the baseline and compare results.")

    selected_preset = st.selectbox(
        "Select scenario preset",
        list(PRESETS.keys()),
        format_func=lambda k: PRESETS[k].name,
    )
    preset = PRESETS[selected_preset]

    # Show scenario description and diff
    st.markdown(f"**Description:** {preset.description}")

    diff = preset.diff_from_baseline()
    if diff:
        st.markdown("**Changes from baseline:**")
        for k, v in diff.items():
            st.markdown(f"- **{k}:** {v}")

    if st.button("Run Scenario Comparison", type="primary"):
        station_file = BASE / "stations" / f"{station_name}.yaml"
        params_file = BASE / "params.yaml"

        with st.spinner(f"Running '{preset.name}' scenario..."):
            sc_result = run_scenario(
                station_file, params_file, preset,
                days=sim_days, forecast_runs=min(forecast_runs, 50),
            )

        st.session_state.scenario_comparison = sc_result
        st.success("Scenario comparison complete!")

    if "scenario_comparison" in st.session_state:
        sc = st.session_state.scenario_comparison

        # Diff summary table
        st.subheader("Baseline vs Scenario Diff")
        diff_data = sc.diff_summary()
        for metric, values in diff_data.items():
            st.markdown(f"- **{metric}:** {values}")

        # Side-by-side fuel comparison chart
        st.subheader("Fuel Level Comparison")
        b_days = [s.time_hours / 24.0 for s in sc.baseline.history]
        s_days = [s.time_hours / 24.0 for s in sc.scenario_result.history]

        b_fuel_id = [sid for sid in sc.baseline.history[0].storage if "fuel" in sid]
        s_fuel_id = [sid for sid in sc.scenario_result.history[0].storage if "fuel" in sid]

        fig_cmp = go.Figure()
        if b_fuel_id:
            fig_cmp.add_trace(go.Scatter(
                x=b_days,
                y=[s.storage[b_fuel_id[0]].level for s in sc.baseline.history],
                name="Baseline", line=dict(color="#636EFA", width=2),
            ))
        if s_fuel_id:
            fig_cmp.add_trace(go.Scatter(
                x=s_days,
                y=[s.storage[s_fuel_id[0]].level for s in sc.scenario_result.history],
                name=sc.scenario.name, line=dict(color="#EF553B", width=2, dash="dash"),
            ))
        fig_cmp.update_layout(
            height=350, xaxis_title="Day", yaxis_title="Fuel (L)",
            margin=dict(l=50, r=20, t=20, b=30),
        )
        st.plotly_chart(fig_cmp, use_container_width=True)

        # Alert comparison
        st.subheader("Alert Comparison")
        ac1, ac2 = st.columns(2)
        with ac1:
            st.markdown("**Baseline Alerts**")
            for a in sc.baseline_alerts[:5]:
                color = "#FF4444" if a.severity == AlertSeverity.RED else "#FFB020"
                st.markdown(
                    f'<span style="color:{color}">[{a.severity.value}]</span> {a.cause}',
                    unsafe_allow_html=True,
                )
            if not sc.baseline_alerts:
                st.write("No alerts.")

        with ac2:
            st.markdown(f"**{sc.scenario.name} Alerts**")
            for a in sc.scenario_alerts[:5]:
                color = "#FF4444" if a.severity == AlertSeverity.RED else "#FFB020"
                st.markdown(
                    f'<span style="color:{color}">[{a.severity.value}]</span> {a.cause}',
                    unsafe_allow_html=True,
                )
            if not sc.scenario_alerts:
                st.write("No alerts.")


# ===== TAB 7: VALIDATION =====
with tab_validation:
    val_tab1, val_tab2 = st.tabs(["Sensitivity Analysis", "Calibration Backtest"])

    with val_tab1:
        st.subheader("Sensitivity Tornado Chart")
        st.caption("Each parameter varied +/-20%. Impact measured as change in fuel exhaustion day.")

        if st.button("Run Sensitivity Analysis", key="sensitivity_btn"):
            station_file = BASE / "stations" / f"{station_name}.yaml"
            params_file = BASE / "params.yaml"

            with st.spinner("Running sensitivity analysis (12 simulations)..."):
                sensitivity = run_sensitivity(station_file, params_file, days=sim_days)

            st.session_state.sensitivity = sensitivity

        if "sensitivity" in st.session_state:
            sensitivity = st.session_state.sensitivity

            # Tornado chart
            fig_tornado = go.Figure()

            labels = [s.parameter for s in sensitivity]
            baseline_val = sensitivity[0].baseline_value if sensitivity else 365
            low_deltas = [s.low_value - baseline_val for s in sensitivity]
            high_deltas = [s.high_value - baseline_val for s in sensitivity]

            fig_tornado.add_trace(go.Bar(
                y=labels, x=low_deltas, orientation="h",
                name=sensitivity[0].low_label if sensitivity else "-20%",
                marker_color="#636EFA",
            ))
            fig_tornado.add_trace(go.Bar(
                y=labels, x=high_deltas, orientation="h",
                name=sensitivity[0].high_label if sensitivity else "+20%",
                marker_color="#EF553B",
            ))

            fig_tornado.update_layout(
                height=400,
                title=f"Fuel Exhaustion Day Sensitivity (baseline: day {baseline_val:.0f})",
                xaxis_title="Change in exhaustion day (days)",
                barmode="overlay",
                margin=dict(l=150, r=20, t=60, b=30),
            )
            st.plotly_chart(fig_tornado, use_container_width=True)

            # Data table
            st.subheader("Sensitivity Data")
            table_data = []
            for s in sensitivity:
                table_data.append({
                    "Parameter": s.parameter,
                    f"{s.low_label}": f"Day {s.low_value:.0f}",
                    "Baseline": f"Day {s.baseline_value:.0f}",
                    f"{s.high_label}": f"Day {s.high_value:.0f}",
                    "Swing": f"{abs(s.high_value - s.low_value):.0f} days",
                })
            st.table(table_data)

    with val_tab2:
        st.subheader("Calibration Backtest")
        st.caption("Checks that model outputs fall within defensible physical bounds.")

        if st.button("Run Backtest", key="backtest_btn"):
            station_file = BASE / "stations" / f"{station_name}.yaml"
            params_file = BASE / "params.yaml"

            with st.spinner("Running calibration backtest..."):
                checks = run_backtest(station_file, params_file)

            st.session_state.backtest = checks

        if "backtest" in st.session_state:
            checks = st.session_state.backtest
            n_pass = sum(1 for c in checks if c.passed)
            n_total = len(checks)

            if n_pass == n_total:
                st.success(f"All {n_total} checks passed!")
            else:
                st.error(f"{n_total - n_pass} of {n_total} checks failed.")

            for check in checks:
                icon = "white_check_mark" if check.passed else "x"
                with st.expander(
                    f":{icon}: {check.name}",
                    expanded=not check.passed,
                ):
                    st.markdown(f"**Description:** {check.description}")
                    st.markdown(f"**Expected:** {check.expected_range}")
                    st.markdown(f"**Actual:** {check.actual_value:,.2f} {check.unit}")
                    st.markdown(f"**Result:** {'PASS' if check.passed else 'FAIL'}")


# ===== TAB 8: PROVENANCE & AUDIT =====
with tab_provenance:
    prov_tab1, prov_tab2, prov_tab3 = st.tabs(
        ["Parameter Provenance", "Audit Log", "System Info"]
    )

    # --- Parameter Provenance ---
    with prov_tab1:
        st.subheader("Parameter Provenance")
        st.caption("Every parameter in the model is traced to a published source or marked as an assumption.")

        provenance = get_provenance(params)
        if provenance:
            st.dataframe(
                provenance,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Parameter": st.column_config.TextColumn("Parameter", width="medium"),
                    "Value": st.column_config.TextColumn("Value", width="small"),
                    "Unit": st.column_config.TextColumn("Unit", width="small"),
                    "Source": st.column_config.TextColumn("Source / Citation", width="large"),
                },
            )

            # Stats
            n_sourced = sum(1 for p in provenance if "assumption" not in p["Source"].lower())
            n_total = len(provenance)
            st.info(
                f"**{n_sourced}** of **{n_total}** parameters have published sources. "
                f"**{n_total - n_sourced}** are marked as assumptions."
            )
        else:
            st.warning("No parameter data available. Run a simulation first.")

    # --- Audit Log ---
    with prov_tab2:
        st.subheader("Audit Log")
        st.caption("Tracks who did what and when, for accountability and reproducibility.")

        if check_permission(st.session_state.user, "view_audit"):
            logs = audit_logger.get_logs(limit=200)
            total = audit_logger.count()

            st.metric("Total audit entries", total)

            if logs:
                log_data = [
                    {
                        "Timestamp": entry.timestamp,
                        "User": entry.username,
                        "Role": entry.role,
                        "Action": entry.action,
                        "Details": entry.details,
                    }
                    for entry in logs
                ]
                st.dataframe(log_data, use_container_width=True, hide_index=True)
            else:
                st.info("No audit entries yet. Run a simulation to generate entries.")

            # Clear button (Admin only)
            if check_permission(st.session_state.user, "clear_audit"):
                if st.button("Clear Audit Log", type="secondary"):
                    audit_logger.clear()
                    audit_logger.log_action(
                        st.session_state.user, "Clear Audit Log",
                        "All previous audit entries deleted",
                    )
                    st.rerun()
        else:
            st.warning("Your role does not have permission to view the audit log.")

    # --- System Info ---
    with prov_tab3:
        st.subheader("System Information")

        st.markdown("**Platform:** Antarctic Station Digital Twin")
        st.markdown("**Problem Statement:** SIH26060 - Digital Platform for Remote Management of Indian Antarctic Research Stations")
        st.markdown("**Data Sources:** YAML configuration files (local)")

        st.divider()
        st.markdown("**Available Stations:**")
        for s in data_source.list_stations():
            st.markdown(f"- {s.title()}")

        st.divider()
        st.markdown("**Role Permissions:**")
        from antarctic_twin.database import ROLE_PERMISSIONS
        for role, perms in ROLE_PERMISSIONS.items():
            st.markdown(f"- **{role.value}:** {', '.join(sorted(perms))}")

        st.divider()
        st.markdown("**Current User:**")
        user = st.session_state.user
        st.markdown(f"- Username: `{user.username}`")
        st.markdown(f"- Role: `{user.role.value}`")
