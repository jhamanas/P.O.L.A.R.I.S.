"""Antarctic Station Digital Twin — Streamlit app (Phase 0 stub).

Run with: streamlit run app.py
"""

import streamlit as st
from pathlib import Path
from antarctic_twin.engine import SimulationEngine
from antarctic_twin.config import load_station, load_params

st.set_page_config(page_title="Antarctic Station Digital Twin", layout="wide")

# Header with simulated telemetry badge
col_title, col_badge = st.columns([4, 1])
with col_title:
    st.title("🏔️ Antarctic Station Digital Twin")
with col_badge:
    st.markdown(
        '<div style="background:#FF6B35;color:white;padding:8px 12px;'
        'border-radius:4px;text-align:center;margin-top:16px;font-weight:bold;">'
        '⚠️ SIMULATED TELEMETRY</div>',
        unsafe_allow_html=True,
    )

st.caption("SIH26060 — Digital Platform for Remote Management of Indian Antarctic Research Stations")

BASE = Path(__file__).parent

# --- Station selection ---
station_name = st.selectbox("Select Station", ["bharati", "maitri"], format_func=str.title)

# --- Run simulation ---
if st.button("▶ Run 365-day Simulation", type="primary"):
    station_config = load_station(BASE / "stations" / f"{station_name}.yaml")
    params = load_params(BASE / "params.yaml")
    engine = SimulationEngine(station_config, params, seed=42)

    with st.spinner("Running simulation..."):
        result = engine.run(days=365, dt_hours=1.0)

    st.success(f"✅ {result.station_name} — {result.n_steps} steps in 365 days")

    # Extract time series
    days = [s.time_hours / 24.0 for s in result.history]

    # --- Metrics row ---
    final = result.final_state
    fuel_stores = [s for sid, s in final.storage.items() if "fuel" in sid]
    water_stores = [s for sid, s in final.storage.items() if "water" in sid]
    food_stores = [s for sid, s in final.storage.items() if "food" in sid]

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        fuel_pct = (fuel_stores[0].level / 200000 * 100) if fuel_stores else 0
        st.metric("Fuel Remaining", f"{fuel_stores[0].level:,.0f} L", f"{fuel_pct:.0f}%")
    with c2:
        st.metric("Water Level", f"{water_stores[0].level:,.0f} L" if water_stores else "N/A")
    with c3:
        st.metric("Food Remaining", f"{food_stores[0].level:,.0f} kg" if food_stores else "N/A")
    with c4:
        avg_temp = sum(z.temperature for z in final.zones.values()) / max(1, len(final.zones))
        st.metric("Avg Zone Temp", f"{avg_temp:.1f} °C")

    # --- Charts ---
    st.subheader("📊 Consumable Levels Over Time")

    fuel_ids = [sid for sid in result.history[0].storage if "fuel" in sid]
    water_ids = [sid for sid in result.history[0].storage if "water" in sid]
    food_ids = [sid for sid in result.history[0].storage if "food" in sid]

    col1, col2, col3 = st.columns(3)
    with col1:
        st.line_chart(
            {"Day": days, "Fuel (L)": [s.storage[fuel_ids[0]].level for s in result.history]},
            x="Day", y="Fuel (L)",
        )
    with col2:
        st.line_chart(
            {"Day": days, "Water (L)": [s.storage[water_ids[0]].level for s in result.history]},
            x="Day", y="Water (L)",
        )
    with col3:
        st.line_chart(
            {"Day": days, "Food (kg)": [s.storage[food_ids[0]].level for s in result.history]},
            x="Day", y="Food (kg)",
        )

    # --- Zone temperatures ---
    st.subheader("🌡️ Zone Temperatures")
    zone_data = {"Day": days}
    for zid in result.history[0].zones:
        label = zid.split(".")[-1].title()
        zone_data[label] = [s.zones[zid].temperature for s in result.history]
    st.line_chart(zone_data, x="Day")

    # --- Weather ---
    st.subheader("🌬️ Weather Conditions")
    w_col1, w_col2 = st.columns(2)
    with w_col1:
        st.line_chart(
            {"Day": [e.day_of_year for e in result.weather_history],
             "Temperature (°C)": [e.temperature for e in result.weather_history]},
            x="Day", y="Temperature (°C)",
        )
    with w_col2:
        st.line_chart(
            {"Day": [e.day_of_year for e in result.weather_history],
             "Wind (m/s)": [e.wind_speed for e in result.weather_history]},
            x="Day", y="Wind (m/s)",
        )
