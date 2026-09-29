"""Database, Audit Log, and Role-Based Access Control (RBAC).

Provides:
  - Role enum (Admin, Operator, Scientist, Guest) with permission tiers
  - User dataclass
  - AuditLogger: SQLite-backed audit log tracking who did what and when
  - check_permission: RBAC gate
  - get_provenance: returns the source metadata for every parameter
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any


class Role(str, Enum):
    ADMIN = "Admin"
    OPERATOR = "Operator"
    SCIENTIST = "Scientist"
    GUEST = "Guest"


# Permission tiers: what each role can do
ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: {"run_simulation", "run_scenario", "run_sensitivity",
                 "run_backtest", "acknowledge_alert", "view_audit",
                 "change_params", "clear_audit"},
    Role.OPERATOR: {"run_simulation", "run_scenario", "run_sensitivity",
                    "run_backtest", "acknowledge_alert", "view_audit"},
    Role.SCIENTIST: {"run_simulation", "run_scenario", "run_sensitivity",
                     "run_backtest", "view_audit"},
    Role.GUEST: {"view_audit"},
}


@dataclass
class User:
    username: str
    role: Role


@dataclass
class AuditEntry:
    id: int
    timestamp: str
    username: str
    role: str
    action: str
    details: str


class AuditLogger:
    """SQLite-backed audit logger for system events."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self):
        """Create the audit_log table if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL
                )
            ''')
            conn.commit()

    def log_action(self, user: User, action: str, details: str) -> None:
        """Record an action in the audit log."""
        ts = datetime.now().isoformat(sep=" ", timespec="seconds")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, username, role, action, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, user.username, user.role.value, action, details),
            )
            conn.commit()

    def get_logs(self, limit: int = 100) -> list[AuditEntry]:
        """Retrieve recent audit log entries, newest first."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, timestamp, username, role, action, details "
                "FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AuditEntry(id=r[0], timestamp=r[1], username=r[2],
                       role=r[3], action=r[4], details=r[5])
            for r in rows
        ]

    def count(self) -> int:
        """Total number of audit entries."""
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

    def clear(self) -> None:
        """Delete all audit entries (Admin only — checked by caller)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM audit_log")
            conn.commit()


def check_permission(user: User, action: str) -> bool:
    """Check if user's role permits the given action."""
    perms = ROLE_PERMISSIONS.get(user.role, set())
    return action in perms


def get_provenance(params: dict[str, Any]) -> list[dict[str, str]]:
    """Extract provenance (source citations) from the params dict.

    Returns a list of {parameter, value, unit, source} dicts for the
    provenance page.
    """
    rows = []
    for key, entry in params.items():
        if isinstance(entry, dict) and "value" in entry:
            rows.append({
                "Parameter": key,
                "Value": str(entry["value"]),
                "Unit": entry.get("unit", "-"),
                "Source": entry.get("source", "assumption"),
            })
    return rows
