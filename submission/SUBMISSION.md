# SIH 2026 Final Submission

## Problem Statement

**SIH26060 - Digital Platform for efficient remote management of Indian Antarctic Research Stations**

## Solution

We developed an Antarctic Station Digital Twin for the Bharati and Maitri
research stations.

The platform integrates:

- Infrastructure and station assets
- Environmental conditions
- Thermal behavior
- Energy generation and storage
- Fuel, water and food consumption
- Resupply planning
- Predictive maintenance
- Probabilistic forecasting
- Scenario analysis
- Alerts and operator workflows
- Role-based access and audit logging

## Digital Twin Causal Workflow

Environmental conditions
        ↓
Thermal demand
        ↓
Energy demand and dispatch
        ↓
Generator loading
        ↓
Fuel consumption
        ↓
Consumable depletion
        ↓
Monte Carlo forecast
        ↓
Resupply risk
        ↓
Actionable alert and recommendation

## Technology Stack

- Python 3.12
- Streamlit
- NumPy
- Plotly
- PyDeck
- FastAPI
- WebSockets
- SQLite
- YAML
- Pytest

## Prototype Data

The current prototype uses synthetic telemetry and engineering assumptions
where authorized operational station data is unavailable.

The telemetry/data-source architecture is designed so that an authorized real
station feed can replace the synthetic source without redesigning the
simulation and dashboard layers.

## Prototype Capabilities

- Bharati station model
- Maitri station model
- Thermal RC simulation
- Renewable and generator dispatch
- Battery storage
- Consumable tracking
- Monte Carlo forecasting
- Scenario simulation
- Predictive maintenance
- Alert generation
- Telemetry interface
- RBAC and audit logging
- Parameter provenance
- Sensitivity analysis
- Physical plausibility and sanity checks
