"""Phase 1 calibration: full year, both stations, cascade check."""
from antarctic_twin.engine import run_station, SimulationEngine
from antarctic_twin.config import load_station, load_params
from pathlib import Path
import time

BASE = Path("d:/PS2")

print("=" * 70)
print("PHASE 1 — FULL SYSTEM CALIBRATION")
print("=" * 70)

for station in ['bharati', 'maitri']:
    cfg = load_station(BASE / "stations" / f"{station}.yaml")
    params = load_params(BASE / "params.yaml")

    t0 = time.time()
    engine = SimulationEngine(cfg, params, seed=42)
    result = engine.run(days=365)
    elapsed = time.time() - t0
    f = result.final_state

    print(f"\n{'-'*60}")
    print(f"  {result.station_name} ({elapsed:.2f}s, {result.n_steps} steps)")
    print(f"{'-'*60}")

    # Consumables
    for sid, ss in f.storage.items():
        if "battery" in sid:
            print(f"  Battery SOC: {f.battery.soc_kwh:.1f} kWh")
            continue
        print(f"  {sid}: {ss.level:,.0f}", end="")
        # Check exhaustion
        for s in result.history:
            if s.storage[sid].level <= 0:
                print(f" [EXHAUSTED day {s.time_hours/24:.0f}]", end="")
                break
        else:
            print(f" [SURVIVES]", end="")
        print()

    # Fuel burn rate
    fuel_id = [sid for sid in result.history[0].storage if "fuel" in sid][0]
    day30_fuel = result.history[30*24].storage[fuel_id].level
    day0_fuel = result.history[0].storage[fuel_id].level
    daily_rate = (day0_fuel - day30_fuel) / 30
    print(f"  Fuel burn: {daily_rate:.0f} L/day ({daily_rate*365/1000:.0f} kL/year)")

    # Zone temps at mid-winter
    winter_state = result.history[190*24]
    print(f"  Mid-winter zone temps:")
    for zid, zs in winter_state.zones.items():
        label = zid.split('.')[-1]
        print(f"    {label}: {zs.temperature:.1f}°C (demand {zs.heating_demand_kw:.1f}kW, applied {zs.heating_kw:.1f}kW)")

    # Generator condition and faults
    print(f"  Generator status at day 365:")
    for gid, gs in f.generators.items():
        label = gid.split('.')[-1]
        status = "FAULTED" if gs.faulted else "OK"
        print(f"    {label}: {gs.running_hours:.0f}h, cond={gs.condition:.3f}, {status}, fuel={gs.fuel_consumed_l:,.0f}L")

    # Renewable generation
    total_renew = sum(s.renewable_generation_kw for s in result.history[1:])
    total_gen = sum(s.total_generation_kw for s in result.history[1:])
    renew_pct = total_renew / (total_gen + total_renew + 0.01) * 100
    print(f"  Renewable contribution: {renew_pct:.1f}% of total")

    # Battery utilization
    bat_charge = sum(1 for s in result.history[1:] if s.battery_power_kw < 0)
    bat_discharge = sum(1 for s in result.history[1:] if s.battery_power_kw > 0)
    print(f"  Battery: charged {bat_charge}h, discharged {bat_discharge}h")

# --- CASCADE TEST ---
print(f"\n{'='*70}")
print("CASCADE TEST: Drop temperature -> fuel exhaustion moves earlier")
print(f"{'='*70}")

cfg = load_station(BASE / "stations" / "bharati.yaml")
params = load_params(BASE / "params.yaml")

# Baseline
r_base = SimulationEngine(cfg, params, seed=42).run(days=365)
fuel_id = [sid for sid in r_base.history[0].storage if "fuel" in sid][0]
fuel_base_200 = r_base.history[200*24].storage[fuel_id].level
fuel_base_end = r_base.final_state.storage[fuel_id].level

# Cold scenario
cold_cfg = dict(cfg)
cold_weather = dict(cfg["weather"])
cold_weather["winter_temp_avg"] = {"value": -30.0, "unit": "C", "source": "cold scenario"}
cold_weather["summer_temp_avg"] = {"value": -5.0, "unit": "C", "source": "cold scenario"}
cold_cfg["weather"] = cold_weather
r_cold = SimulationEngine(cold_cfg, params, seed=42).run(days=365)
fuel_cold_200 = r_cold.history[200*24].storage[fuel_id].level
fuel_cold_end = r_cold.final_state.storage[fuel_id].level

print(f"  Day 200 fuel: baseline={fuel_base_200:,.0f}L, cold={fuel_cold_200:,.0f}L")
print(f"  Day 365 fuel: baseline={fuel_base_end:,.0f}L, cold={fuel_cold_end:,.0f}L")
print(f"  Cascade verified: {'YES' if fuel_cold_200 < fuel_base_200 else 'NO'}")

# Check heating demand increased
heat_base = sum(s.total_heating_demand_kw for s in r_base.history[1:]) / len(r_base.history[1:])
heat_cold = sum(s.total_heating_demand_kw for s in r_cold.history[1:]) / len(r_cold.history[1:])
print(f"  Avg heating demand: baseline={heat_base:.1f}kW, cold={heat_cold:.1f}kW")
print(f"  Heating increased: {'YES' if heat_cold > heat_base else 'NO'}")
