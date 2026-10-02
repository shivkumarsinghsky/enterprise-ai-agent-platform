from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from agent_platform.agents import AGENTS
from agent_platform.security import AuthorizationError, GuardrailViolation, Principal
from agent_platform.service import AgentService, RunRecord


class RunIn(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    agent: str | None = None
    conversation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class DecisionIn(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=500)


def get_principal(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> Principal:
    principals: dict[str, Principal] = request.app.state.principals
    p = principals.get(hashlib.sha256((x_api_key or "").encode()).hexdigest())
    if p is None:
        raise HTTPException(401, "missing or invalid API key")
    return p


Caller = Annotated[Principal, Depends(get_principal)]


def view(r: RunRecord, include_trace: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"runId": r.run_id, "status": r.status}
    if r.pending:
        out["pendingApproval"] = r.pending
    if r.result:
        out["result"] = r.result
    if include_trace:
        out["trace"] = r.trace
    return out


def create_app(service: AgentService, api_keys: str) -> FastAPI:
    app = FastAPI(title="Enterprise AI Agent Platform", version="1.0.0")
    app.state.principals = {
        hashlib.sha256(k.encode()).hexdigest(): Principal(v["tenant"], v["user"], frozenset(v["roles"]))
        for k, v in json.loads(api_keys).items()
    }

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict[str, Any]:
        return {"status": "ready", "tools": len(service.registry.tools)}

    @app.get("/metrics", include_in_schema=False)
    def prometheus() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/agents")
    def agents(_: Caller) -> list[dict[str, Any]]:
        return [{"name": a.name, "description": a.description, "tools": list(a.tools)} for a in AGENTS.values()]

    @app.get("/v1/tools")
    def tools(caller: Caller) -> list[dict[str, Any]]:
        return [t | {"allowed": caller.can(t["permission"])} for t in service.registry.describe()]

    @app.post("/v1/runs")
    def start(body: RunIn, caller: Caller) -> dict[str, Any]:
        try:
            return view(service.start(caller, body.input, body.agent, body.conversation_id))
        except (GuardrailViolation, ValueError) as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/v1/runs/{run_id}")
    def get(run_id: str, caller: Caller, trace: bool = True) -> dict[str, Any]:
        try:
            return view(service.get(run_id, caller, allow_approvers=True), include_trace=trace)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e

    @app.post("/v1/runs/{run_id}/decision")
    def decide(run_id: str, body: DecisionIn, caller: Caller) -> dict[str, Any]:
        try:
            return view(service.decide(run_id, caller, body.approved, body.comment))
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except AuthorizationError as e:
            raise HTTPException(403, str(e)) from e
        except ValueError as e:
            raise HTTPException(409, str(e)) from e

    return app
