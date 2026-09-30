"""Antarctic Station Telemetry Mock Server.

Simulates a real Antarctic research station broadcasting live sensor data.
Run with:  python telemetry_server.py

Endpoints:
  - WebSocket  ws://localhost:8765/ws   (real-time stream, 2s interval)
  - REST GET   http://localhost:8765/latest  (latest telemetry snapshot)
"""

import asyncio
import json
import math
import random
import time
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from antarctic_twin.config import load_params, load_station

app = FastAPI(title="Antarctic Station Telemetry Mock")

# Allow Streamlit to call REST endpoints
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Load station config to seed realistic ranges
# ---------------------------------------------------------------------------
try:
    station_cfg = load_station(BASE / "stations" / "bharati.yaml")
    generators = station_cfg.get("generators", [])
    params = load_params(BASE / "params.yaml")
    winter_temp = station_cfg["weather"]["winter_temp_avg"]["value"]  # -20
    summer_temp = station_cfg["weather"]["summer_temp_avg"]["value"]  # 0
    avg_wind = station_cfg["weather"]["avg_wind"]["value"]
except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
    generators = []
    params = {}
    winter_temp, summer_temp, avg_wind = -20.0, 0.0, 7.0

# ---------------------------------------------------------------------------
# Shared mutable state (latest telemetry frame)
# ---------------------------------------------------------------------------
_latest_frame: dict = {}
_sim_time: int = 0


def _build_frame(sim_time: int) -> dict:
    """Generate a single telemetry frame that looks like real sensor data."""
    # Day-of-year drives a realistic seasonal temperature cycle
    day_of_year = (sim_time // 24) % 365
    # Sinusoidal season: coldest at day ~182 (July), warmest at day ~0 (Jan)
    season_factor = -math.cos(2 * math.pi * day_of_year / 365)
    base_temp = (
        (summer_temp + winter_temp) / 2 +
        (summer_temp - winter_temp) / 2 * season_factor
    )
    temp = base_temp + random.gauss(0, 2.5)

    wind = max(0, avg_wind + random.gauss(0, 3.0))

    gen_data = []
    for i, gen in enumerate(generators):
        # Generators degrade slowly over time
        condition = max(0.0, 1.0 - (sim_time * 0.00005) * (1.3 if i == 0 else 1.0))
        running = condition > 0.05 and random.random() < 0.92
        load = random.uniform(0.35, 0.95) if running else 0.0
        gen_data.append({
            "id": gen.get("id", f"gen{i}"),
            "label": gen.get("label", f"Generator {i+1}"),
            "running": running,
            "load_pct": round(load * 100, 1),
            "condition_pct": round(condition * 100, 1),
            "fuel_rate_lph": round(load * 25, 1) if running else 0.0,
        })

    # Heating demand correlates with how cold it is
    heating_kw = max(0, (-temp - 5) * 3.5 + random.gauss(0, 5))

    return {
        "station": "bharati",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sim_hour": sim_time,
        "sim_day": sim_time // 24,
        "day_of_year": day_of_year,
        "weather": {
            "temperature_c": round(temp, 1),
            "wind_speed_ms": round(wind, 1),
        },
        "generators": gen_data,
        "heating_demand_kw": round(heating_kw, 1),
        "total_gen_kw": round(sum(g["load_pct"] / 100 * 125 for g in gen_data), 1),
    }


# ---------------------------------------------------------------------------
# REST endpoint: GET /latest
# ---------------------------------------------------------------------------
@app.get("/latest")
async def get_latest():
    """Return the most recent telemetry frame as JSON."""
    return _latest_frame if _latest_frame else _build_frame(0)


# ---------------------------------------------------------------------------
# WebSocket endpoint: ws://host:port/ws
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global _latest_frame, _sim_time
    await websocket.accept()
    print("[telemetry] Dashboard connected via WebSocket.")

    try:
        while True:
            frame = _build_frame(_sim_time)
            _latest_frame = frame
            _sim_time += 1
            await websocket.send_text(json.dumps(frame))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        print("[telemetry] Dashboard disconnected.")
    except (ConnectionError, RuntimeError) as exc:
        print(f"[telemetry] Connection closed: {exc}")


# ---------------------------------------------------------------------------
# Background ticker (keeps _latest_frame fresh even without WS clients)
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def start_ticker():
    async def _tick():
        global _latest_frame, _sim_time
        while True:
            _latest_frame = _build_frame(_sim_time)
            _sim_time += 1
            await asyncio.sleep(2)
    asyncio.create_task(_tick())


if __name__ == "__main__":
    print("Starting Antarctic Telemetry Mock Server")
    print("  REST:      http://localhost:8765/latest")
    print("  WebSocket: ws://localhost:8765/ws")
    uvicorn.run(app, host="0.0.0.0", port=8765)
