import asyncio
import json
import time
import random
import uvicorn
from fastapi import FastAPI, WebSocket
from antarctic_twin.config import load_station
from pathlib import Path

app = FastAPI(title="Antarctic Station Telemetry Mock")
BASE = Path(__file__).resolve().parent

# Load initial state to mock realistic values
try:
    station_cfg = load_station(BASE / "stations" / "bharati.yaml")
    generators = station_cfg.get("generators", [])
except Exception:
    generators = []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Dashboard connected to telemetry stream.")
    
    sim_time = 0
    try:
        while True:
            # Generate synthetic telemetry payload
            payload = {
                "timestamp_utc": time.time(),
                "sim_hours": sim_time,
                "weather": {
                    "temperature": random.uniform(-25.0, 5.0),
                    "wind_speed": random.uniform(2.0, 25.0)
                },
                "generators": []
            }
            
            for i, gen in enumerate(generators):
                running = random.choice([True, True, True, False])
                payload["generators"].append({
                    "id": gen.get("id", f"gen{i}"),
                    "running": running,
                    "load": random.uniform(0.3, 0.9) if running else 0.0,
                    "condition": max(0.0, 1.0 - (sim_time * 0.0001) * (1.5 if i == 0 else 1.0))
                })
            
            await websocket.send_text(json.dumps(payload))
            sim_time += 1
            await asyncio.sleep(2)  # Emit every 2 seconds
            
    except Exception as e:
        print(f"Connection closed: {e}")

if __name__ == "__main__":
    print("Starting Antarctic Telemetry Mock Server on ws://localhost:8000/ws")
    uvicorn.run(app, host="0.0.0.0", port=8000)
