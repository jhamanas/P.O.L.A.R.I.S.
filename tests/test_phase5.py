"""Phase 5 tests: RBAC, audit log, data source interface, provenance."""


from antarctic_twin.config import load_params
from antarctic_twin.database import (
    ROLE_PERMISSIONS,
    AuditLogger,
    Role,
    User,
    check_permission,
    get_provenance,
)
from antarctic_twin.interfaces import YamlDataSource
from tests.conftest import REPO_ROOT as BASE

# ================================================================
# RBAC
# ================================================================

def test_admin_has_all_permissions():
    """Admin should have permission for every action."""
    admin = User(username="admin", role=Role.ADMIN)
    for action in ["run_simulation", "run_scenario", "run_sensitivity",
                    "run_backtest", "acknowledge_alert", "view_audit",
                    "change_params", "clear_audit"]:
        assert check_permission(admin, action), f"Admin missing '{action}'"


def test_guest_can_only_view_audit():
    """Guest should only have view_audit and run_simulation permissions."""
    guest = User(username="guest", role=Role.GUEST)
    assert check_permission(guest, "view_audit")
    assert check_permission(guest, "run_simulation")
    assert not check_permission(guest, "run_scenario")
    assert not check_permission(guest, "change_params")
    assert not check_permission(guest, "acknowledge_alert")
    assert not check_permission(guest, "clear_audit")
    assert not check_permission(guest, "clear_audit")


def test_operator_cannot_change_params():
    """Operator should not be able to change params."""
    op = User(username="operator", role=Role.OPERATOR)
    assert check_permission(op, "run_simulation")
    assert check_permission(op, "acknowledge_alert")
    assert not check_permission(op, "change_params")
    assert not check_permission(op, "clear_audit")


def test_scientist_can_run_but_not_acknowledge():
    """Scientist can run simulations but cannot acknowledge alerts."""
    sci = User(username="scientist", role=Role.SCIENTIST)
    assert check_permission(sci, "run_simulation")
    assert check_permission(sci, "run_backtest")
    assert not check_permission(sci, "acknowledge_alert")


def test_all_roles_defined():
    """Every Role should have an entry in ROLE_PERMISSIONS."""
    for role in Role:
        assert role in ROLE_PERMISSIONS, f"Missing permissions for {role}"


# ================================================================
# Audit Log (SQLite)
# ================================================================

def test_audit_log_write_and_read(tmp_path):
    """Audit logger should store and retrieve entries."""
    db_path = tmp_path / "test_audit.db"
    logger = AuditLogger(db_path)
    user = User(username="test_user", role=Role.OPERATOR)

    logger.log_action(user, "Run Simulation", "Bharati, seed=42")
    logger.log_action(user, "Run Scenario", "Cold Snap")

    logs = logger.get_logs(limit=10)
    assert len(logs) == 2
    # Newest first
    assert logs[0].action == "Run Scenario"
    assert logs[1].action == "Run Simulation"
    assert logs[0].username == "test_user"
    assert logs[0].role == "Operator"


def test_audit_log_count(tmp_path):
    """Count should match number of logged actions."""
    db_path = tmp_path / "test_count.db"
    logger = AuditLogger(db_path)
    user = User(username="counter", role=Role.ADMIN)

    assert logger.count() == 0
    logger.log_action(user, "Action 1", "details")
    logger.log_action(user, "Action 2", "details")
    logger.log_action(user, "Action 3", "details")
    assert logger.count() == 3


def test_audit_log_clear(tmp_path):
    """Clear should delete all entries."""
    db_path = tmp_path / "test_clear.db"
    logger = AuditLogger(db_path)
    user = User(username="clearer", role=Role.ADMIN)

    logger.log_action(user, "A", "d")
    logger.log_action(user, "B", "d")
    assert logger.count() == 2

    logger.clear()
    assert logger.count() == 0


def test_audit_entries_have_timestamps(tmp_path):
    """Every audit entry should have a non-empty timestamp."""
    db_path = tmp_path / "test_ts.db"
    logger = AuditLogger(db_path)
    user = User(username="ts_test", role=Role.SCIENTIST)
    logger.log_action(user, "Test", "checking timestamps")

    logs = logger.get_logs()
    assert len(logs) == 1
    assert len(logs[0].timestamp) > 10  # ISO timestamp


# ================================================================
# DataSource interface
# ================================================================

def test_yaml_data_source_loads_station():
    """YamlDataSource should load station configs."""
    ds = YamlDataSource(BASE)
    cfg = ds.get_station_config("bharati")
    assert cfg["name"] == "Bharati"
    assert "zones" in cfg


def test_yaml_data_source_loads_params():
    """YamlDataSource should load global params."""
    ds = YamlDataSource(BASE)
    params = ds.get_global_params()
    assert "generator_efficiency" in params


def test_yaml_data_source_lists_stations():
    """list_stations should discover both station YAML files."""
    ds = YamlDataSource(BASE)
    stations = ds.list_stations()
    assert "bharati" in stations
    assert "maitri" in stations
    assert len(stations) >= 2


def test_second_station_by_config():
    """Both stations should work with the same code path (no hardcoded names)."""
    ds = YamlDataSource(BASE)
    for station_name in ds.list_stations():
        cfg = ds.get_station_config(station_name)
        assert "zones" in cfg
        assert "generators" in cfg
        assert "storage" in cfg
        assert "weather" in cfg


# ================================================================
# Provenance
# ================================================================

def test_provenance_extracts_sources():
    """get_provenance should extract parameter sources from params.yaml."""
    params = load_params(BASE / "params.yaml")
    prov = get_provenance(params)

    assert len(prov) > 10  # should have many parameters
    for row in prov:
        assert "Parameter" in row
        assert "Value" in row
        assert "Source" in row
        assert len(row["Source"]) > 5  # non-trivial source text


def test_provenance_has_sourced_and_assumptions():
    """Provenance should include both sourced and assumption-marked params."""
    params = load_params(BASE / "params.yaml")
    prov = get_provenance(params)

    sources = [p["Source"] for p in prov]
    has_sourced = any("assumption" not in s.lower() for s in sources)
    has_assumptions = any("assumption" in s.lower() for s in sources)
    assert has_sourced, "No parameters have published sources"
    assert has_assumptions, "No parameters are marked as assumptions"
