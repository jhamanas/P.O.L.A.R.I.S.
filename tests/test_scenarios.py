from pathlib import Path

from antarctic_twin.config import load_params, load_station
from antarctic_twin.scenarios import Scenario, fork_scenario

BASE = Path(__file__).resolve().parent.parent

def test_fork_scenario_does_not_mutate_base():
    cfg = load_station(BASE / "stations" / "bharati.yaml")
    params = load_params(BASE / "params.yaml")
    
    # Check baseline initial values
    base_winter_temp = cfg["weather"]["winter_temp_avg"]["value"]
    base_crew = cfg["crew"]["winter"]
    
    scenario = Scenario(name="Test", temp_offset=-10.0, crew_delta=5)
    modified_cfg = fork_scenario(cfg, params, scenario)
    
    # Assert modifications applied
    assert modified_cfg["weather"]["winter_temp_avg"]["value"] == base_winter_temp - 10.0
    assert modified_cfg["crew"]["winter"] == base_crew + 5
    
    # Assert base is not mutated
    assert cfg["weather"]["winter_temp_avg"]["value"] == base_winter_temp
    assert cfg["crew"]["winter"] == base_crew

def test_fork_scenario_wind_mult():
    cfg = load_station(BASE / "stations" / "bharati.yaml")
    params = load_params(BASE / "params.yaml")
    base_wind = cfg["weather"]["avg_wind"]["value"]
    
    scenario = Scenario(name="High Wind", wind_mult=2.0)
    modified_cfg = fork_scenario(cfg, params, scenario)
    
    assert modified_cfg["weather"]["avg_wind"]["value"] == base_wind * 2.0
    assert cfg["weather"]["avg_wind"]["value"] == base_wind
