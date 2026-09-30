# Antarctic Station Digital Twin

**SIH26060 - Digital Platform for Efficient Remote Management of Indian Antarctic Research Stations**

A physics-based digital twin that simulates the energy, thermal, and consumable systems of Indian Antarctic stations (Bharati and Maitri). It forecasts resource exhaustion, generates actionable alerts, and supports what-if scenario analysis — all through an interactive Streamlit dashboard.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the dashboard
streamlit run app.py

# 3. Run the test suite
python -m pytest tests/ -v
```


---

## Features

| Feature | Description |
|---|---|
| **Lumped-RC Thermal Model** | Implicit Euler integration for zone temperatures with priority-based heating (living > lab > workshop) |
| **3D Spatial Model** | Live interactive 3D map (pydeck) rendering station zones extruded by heating demand and colored by thermal stress |
| **Real-Time Telemetry** | Standalone WebSocket/REST mock server streaming synthetic live sensor data, ready for real hardware integration |
| **Energy Dispatch** | Merit-order generator dispatch, wind turbine (cubic power curve), solar array, battery storage with DOD limits |
| **Predictive Maintenance** | Equipment wear-and-tear increases dynamically under high load (MTBF scaling), triggering pre-emptive warnings |
| **Consumable Tracking** | Fuel (load-dependent burn curve), water (snow-melt coupling), food depletion |
| **Monte Carlo Forecast** | 500 runs with different weather seeds producing P10/P50/P90 exhaustion dates and fan charts |
| **Actionable Workflows** | Alerts feature interactive "Acknowledge" and "Dismiss" dispatch buttons with automatic audit logging |
| **Scenario Presets** | Cold Snap, Prolonged Blizzard, Delayed Resupply, Generator Failure, Crew Surge, Combined Winter Isolation |
| **Sensitivity Analysis** | Tornado chart varying 6 parameters ±20% to identify dominant risk factors |
| **Calibration Backtest** | 8 physical plausibility checks run against both stations |
| **RBAC & Audit Log** | 4 roles (Admin/Operator/Scientist/Guest), SQLite audit trail, parameter provenance page |
| **Second Station by Config** | Maitri works purely from YAML — no code changes. Drop a new YAML to add a third station. |

---

## Architecture

```
app.py                          Streamlit dashboard (9 tabs)
telemetry_server.py             FastAPI server mocking live MoES sensor feeds (WebSocket + REST)
antarctic_twin/
  types.py                      Shared enums and dataclasses
  config.py                     YAML loading and validation
  weather.py                    Synthetic weather generator (AR(1) + storms)
  asset_graph.py                Asset graph with stable IDs
  state.py                      Station state and step() contract
  energy.py                     Renewables, battery, generator dispatch, dynamic wear model
  engine.py                     Simulation engine (wires weather + step loop)
  forecast.py                   Monte Carlo forecaster
  alerts.py                     Alert engine (6 categories)
  scenarios.py                  Scenario spec, 6 presets, sensitivity, backtest
  database.py                   RBAC, SQLite audit log, provenance
  interfaces.py                 DataSource abstraction (YAML / future API)
stations/
  bharati.yaml                  Bharati station configuration
  maitri.yaml                   Maitri station configuration
params.yaml                     Global parameters with source citations
tests/                          73 tests across 8 test files
```

---

## Dashboard Tabs

1. **Overview** — Consumable levels, zone temperatures, margin days, alert summary
2. **Forecast** — Monte Carlo fan chart with P10/P50/P90 bands and resupply deadline
3. **Energy** — Generation mix, battery SOC, heating demand vs waste heat, generator status
4. **Station Plan** — 3D spatial map (pydeck) with extruded zones, plus a collapsible 2D layout
5. **Live Telemetry** — Real-time sensor feed polled from `telemetry_server.py`
6. **Alerts** — Actionable alert cards (Acknowledge / Dismiss workflows)
7. **Scenarios** — Preset selector with baseline vs scenario diff and fuel comparison
8. **Validation** — Sensitivity tornado chart and calibration backtest
9. **Provenance & Audit** — Parameter source citations, audit log, system info

---

## Scenario Controls (Sidebar)

| Control | Range | Effect |
|---|---|---|
| Temperature offset | -20 to +10 °C | Shifts weather profile |
| Wind multiplier | 0.5x to 2.0x | Scales wind speeds |
| Crew change | -10 to +10 | Adjusts food/water/electrical consumption |
| Resupply delay | 0 to 90 days | Shifts resupply deadline |
| Generator fault | Gen 1/2/3 | Injects fault at day 0 |
| Role selector | Admin/Operator/Scientist/Guest | Controls permissions |

---

## Test Suite

```
70 tests, 8 files, ~60 seconds

tests/test_config.py      5 tests   Config loading and validation
tests/test_weather.py      5 tests   Weather generator determinism and bounds
tests/test_step.py         7 tests   Step contract, immutability, no negative stocks
tests/test_replay.py       4 tests   Deterministic replay, performance
tests/test_phase1.py      13 tests   Energy balance, wind/solar/battery, faults, priority heating
tests/test_phase2.py      10 tests   Forecast percentiles, alerts, cold-snap exit criterion
tests/test_phase4.py      11 tests   Scenarios, sensitivity ordering, backtest (both stations)
tests/test_phase5.py      15 tests   RBAC permissions, audit log CRUD, DataSource, provenance
```

---

## Key Physics

- **Thermal:** `T_new = (C·T_old + dt·(UA·T_amb + Q_heat)) / (C + dt·UA)` (implicit Euler, stable)
- **Fuel:** Load-dependent interpolation between half-load and full-load consumption rates
- **Electrical:** `base_kw + snowmelt_kw + heating_supplement_kw`, waste heat from generators covers ~34 kW of heating
- **Wind:** Cubic power curve with cut-in (3 m/s), rated (12 m/s), cut-out (25 m/s)
- **Condition:** Linear degradation: `max(0, 1 - hours / (2 × MTBF))`
- **Faults:** Poisson process with rate increasing as condition degrades
- **Crew:** Auto-switches between summer (23) and winter (15) crew based on day-of-year

### Forecast Convention

We use **reserves-estimation convention** for percentiles:
- **P10** = optimistic (90th percentile of exhaustion day) — fuel lasts *longer*
- **P50** = median
- **P90** = pessimistic (10th percentile of exhaustion day) — fuel runs out *earlier*

`margin_days = P50_exhaustion − resupply_day`. Positive = safe, negative = runs out before resupply.

---

## Parameter Sources

Every parameter in `params.yaml` carries a `source` field citing either:
- Published data (NCAOR logistics, manufacturer specs, Antarctic building standards)
- Explicit assumptions (marked `"assumption – ..."` with rationale)

View them in the **Provenance & Audit** tab or by reading `params.yaml` directly.

---

## License

This project was built for the Smart India Hackathon 2026 (SIH26060).
