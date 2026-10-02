"""Operations tools: actions in business systems through their APIs (here, an EAM work order API).

`create_work_order` is a write tool: it requires the `workorders:write` permission AND a human approval (by someone
with `workorders:approve`) before the graph executes it. The call carries an idempotency key derived from the run and
tool-call ids, so a retried or resumed run cannot create the work order twice.
"""

from __future__ import annotations

import itertools
import threading
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from agent_platform.security import Principal
from agent_platform.tools.reporting import MaintenanceDb


class EamApi(Protocol):
    def create_work_order(
        self, tenant: str, asset_id: str, description: str, priority: int, requested_by: str, idempotency_key: str
    ) -> dict[str, Any]: ...

    def get_work_order(self, tenant: str, wo_num: str) -> dict[str, Any]: ...


class InMemoryEamApi:
    """Stand-in for an EAM system's REST API, backed by the reporting database."""

    def __init__(self, db: MaintenanceDb) -> None:
        self.db = db
        self._lock = threading.Lock()
        self._seq = itertools.count(5001)
        self._by_key: dict[str, dict[str, Any]] = {}

    def create_work_order(
        self, tenant: str, asset_id: str, description: str, priority: int, requested_by: str, idempotency_key: str
    ) -> dict[str, Any]:
        with self._lock:
            if idempotency_key in self._by_key:
                return {**self._by_key[idempotency_key], "replayed": True}
            conn = self.db.write()
            if not conn.execute(
                "SELECT 1 FROM assets WHERE asset_id = ? AND tenant = ?", (asset_id, tenant)
            ).fetchone():
                raise LookupError(f"asset {asset_id} not found")
            wo = f"WO-{next(self._seq)}"
            conn.execute(
                "INSERT INTO work_orders"
                " (wo_num, tenant, asset_id, work_type, status, priority, description, reported_at)"
                " VALUES (?, ?, ?, 'CM', 'REQUESTED', ?, ?, date('now'))",
                (wo, tenant, asset_id, priority, description),
            )
            conn.commit()
            result = {
                "wo_num": wo,
                "asset_id": asset_id,
                "status": "REQUESTED",
                "priority": priority,
                "requested_by": requested_by,
            }
            self._by_key[idempotency_key] = result
            return result

    def get_work_order(self, tenant: str, wo_num: str) -> dict[str, Any]:
        row = (
            self.db.write()
            .execute(
                "SELECT wo_num, asset_id, work_type, status, priority, description FROM work_orders"
                " WHERE wo_num = ? AND tenant = ?",
                (wo_num, tenant),
            )
            .fetchone()
        )
        if row is None:
            raise LookupError(f"work order {wo_num} not found")
        return dict(zip(("wo_num", "asset_id", "work_type", "status", "priority", "description"), row, strict=True))


class HttpEamApi:
    """Client for a real EAM REST API (contract: eam-platform-architecture/docs/api/openapi.yaml)."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def create_work_order(
        self, tenant: str, asset_id: str, description: str, priority: int, requested_by: str, idempotency_key: str
    ) -> dict[str, Any]:
        r = self._client.post(
            "/work-orders",
            headers={"Idempotency-Key": idempotency_key, "X-Tenant": tenant, "X-On-Behalf-Of": requested_by},
            json={"assetId": asset_id, "workType": "CM", "description": description, "priority": priority},
        )
        r.raise_for_status()
        result: dict[str, Any] = r.json()
        return result

    def get_work_order(self, tenant: str, wo_num: str) -> dict[str, Any]:
        r = self._client.get(f"/work-orders/{wo_num}", headers={"X-Tenant": tenant})
        r.raise_for_status()
        result: dict[str, Any] = r.json()
        return result


class CreateWorkOrderArgs(BaseModel):
    asset_id: str = Field(pattern=r"^[A-Z0-9-]{2,20}$")
    description: str = Field(min_length=5, max_length=300)
    priority: Literal[1, 2, 3, 4, 5] = 3


class WorkOrderArgs(BaseModel):
    wo_num: str = Field(pattern=r"^WO-\d{1,8}$")


def make_operation_tools(api: EamApi):  # type: ignore[no-untyped-def]
    def create_work_order(
        principal: Principal,
        asset_id: str,
        description: str,
        priority: int,
        run_id: str = "",
        tool_call_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        return api.create_work_order(
            principal.tenant, asset_id, description, priority, principal.user, f"{run_id}:{tool_call_id}"
        )

    def get_work_order(principal: Principal, wo_num: str, **_: Any) -> dict[str, Any]:
        return api.get_work_order(principal.tenant, wo_num)

    return create_work_order, get_work_order
