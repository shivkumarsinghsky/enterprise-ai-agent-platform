"""Reporting tools over maintenance data.

The model never writes SQL. Each tool is a fixed, parameterised, tenant-scoped query on a read-only connection —
the safe alternative to "text-to-SQL" for enterprise data.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.security import Principal

SEED = Path(__file__).resolve().parents[1] / "data" / "seed.sql"
PERIOD_START, PERIOD_END, PERIOD_HOURS = "2026-01-01", "2026-10-01", 273 * 24.0


class MaintenanceDb:
    """SQLite stand-in for the reporting warehouse. Shared in-memory database; tools get read-only access."""

    def __init__(self, uri: str = "file:agent-maintenance?mode=memory&cache=shared") -> None:
        self._uri = uri
        self._owner = sqlite3.connect(uri, uri=True, check_same_thread=False)  # keeps the shared DB alive
        if not self._owner.execute("SELECT name FROM sqlite_master WHERE name = 'assets'").fetchone():
            self._owner.executescript(SEED.read_text())
        self._local = threading.local()

    def read(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
            conn.execute("PRAGMA query_only = ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def write(self) -> sqlite3.Connection:
        return self._owner


class AssetArgs(BaseModel):
    asset_id: str = Field(pattern=r"^[A-Z0-9-]{2,20}$", description="Asset tag, e.g. P-101")


class LimitArgs(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)


class OpenWorkOrderArgs(BaseModel):
    asset_id: str | None = Field(default=None, pattern=r"^[A-Z0-9-]{2,20}$")


def make_reporting_tools(db: MaintenanceDb):  # type: ignore[no-untyped-def]
    def maintenance_kpis(principal: Principal, asset_id: str, **_: Any) -> dict[str, Any]:
        c = db.read()
        asset = c.execute(
            "SELECT * FROM assets WHERE asset_id = ? AND tenant = ?", (asset_id, principal.tenant)
        ).fetchone()
        if asset is None:
            raise LookupError(f"asset {asset_id} not found")
        d = c.execute(
            "SELECT count(*) AS failures, coalesce(sum(hours), 0) AS hours FROM downtime"
            " WHERE asset_id = ? AND tenant = ? AND planned = 0 AND started_at >= ? AND started_at < ?",
            (asset_id, principal.tenant, PERIOD_START, PERIOD_END),
        ).fetchone()
        cost = c.execute(
            "SELECT coalesce(sum(cost), 0) FROM work_orders WHERE asset_id = ? AND tenant = ? AND reported_at >= ?",
            (asset_id, principal.tenant, PERIOD_START),
        ).fetchone()[0]
        failures, hours = d["failures"], d["hours"]
        return {
            "asset_id": asset_id,
            "description": asset["description"],
            "criticality": asset["criticality"],
            "period": f"{PERIOD_START}..{PERIOD_END}",
            "failures": failures,
            "unplanned_downtime_hours": hours,
            "mttr_hours": round(hours / failures, 2) if failures else None,
            "mtbf_hours": round((PERIOD_HOURS - hours) / failures, 1) if failures else None,
            "availability": round((PERIOD_HOURS - hours) / PERIOD_HOURS, 4),
            "maintenance_cost_eur": cost,
        }

    def top_failure_modes(principal: Principal, limit: int = 5, **_: Any) -> dict[str, Any]:
        rows = (
            db.read()
            .execute(
                "SELECT problem_code, count(*) AS n, sum(cost) AS cost FROM work_orders"
                " WHERE tenant = ? AND problem_code IS NOT NULL"
                " GROUP BY problem_code ORDER BY n DESC, cost DESC LIMIT ?",
                (principal.tenant, limit),
            )
            .fetchall()
        )
        return {"failure_modes": [dict(r) for r in rows]}

    def list_open_work_orders(principal: Principal, asset_id: str | None = None, **_: Any) -> dict[str, Any]:
        rows = (
            db.read()
            .execute(
                "SELECT wo_num, asset_id, work_type, status, priority, description FROM work_orders"
                " WHERE tenant = ? AND status NOT IN ('CLOSED', 'CANCELLED') AND (? IS NULL OR asset_id = ?)"
                " ORDER BY priority, wo_num",
                (principal.tenant, asset_id, asset_id),
            )
            .fetchall()
        )
        return {"work_orders": [dict(r) for r in rows]}

    return maintenance_kpis, top_failure_modes, list_open_work_orders
