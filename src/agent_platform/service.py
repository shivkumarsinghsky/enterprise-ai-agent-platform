"""Run management: starting runs, pausing for approval, resuming, and exposing traces."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent_platform import metrics
from agent_platform.agents import AGENTS, build_registry
from agent_platform.config import Settings
from agent_platform.graph import build_graph
from agent_platform.security import AuthorizationError, Principal
from agent_platform.tools.knowledge import KnowledgeBase
from agent_platform.tools.operations import EamApi, HttpEamApi, InMemoryEamApi
from agent_platform.tools.registry import ToolRegistry
from agent_platform.tools.reporting import MaintenanceDb

log = logging.getLogger("agent.service")


@dataclass
class RunRecord:
    run_id: str
    thread_id: str
    principal: Principal
    status: str = "running"  # running | awaiting_approval | completed | failed
    pending: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


class AgentService:
    def __init__(self, settings: Settings, llm: BaseChatModel, registry: ToolRegistry, use_llm_router: bool = False):
        self.settings = settings
        self.registry = registry
        self.graph = build_graph(settings, llm, registry, InMemorySaver(), use_llm_router)
        self.runs: dict[str, RunRecord] = {}
        self._lock = threading.Lock()

    def start(
        self, principal: Principal, text: str, agent: str | None = None, conversation_id: str | None = None
    ) -> RunRecord:
        if agent is not None and agent not in AGENTS:
            raise ValueError(f"unknown agent '{agent}'")
        run_id = uuid.uuid4().hex
        # Thread = conversation. Scoped by tenant and user so histories never mix.
        thread_id = f"{principal.tenant}:{principal.user}:{conversation_id or run_id}"
        record = RunRecord(run_id, thread_id, principal)
        with self._lock:
            self.runs[run_id] = record
        state = {
            "messages": [HumanMessage(text)],
            "principal": {"tenant": principal.tenant, "user": principal.user, "roles": sorted(principal.roles)},
            "run_id": run_id,
            "requested_agent": agent,
        }
        return self._advance(record, state)

    def decide(self, run_id: str, approver: Principal, approved: bool, comment: str = "") -> RunRecord:
        record = self.get(run_id, approver, allow_approvers=True)
        if record.status != "awaiting_approval":
            raise ValueError("run is not awaiting approval")
        if not approver.can("workorders:approve"):
            raise AuthorizationError("approver lacks permission 'workorders:approve'")
        if approver.user == record.principal.user:
            raise AuthorizationError("separation of duties: requesters cannot approve their own actions")
        metrics.APPROVALS.labels(decision="approved" if approved else "rejected").inc()
        decision = {"approved": approved, "approver": approver.user, "comment": comment[:500]}
        return self._advance(record, Command(resume=decision))

    def get(self, run_id: str, principal: Principal, allow_approvers: bool = False) -> RunRecord:
        record = self.runs.get(run_id)
        if record is None or record.principal.tenant != principal.tenant:
            raise LookupError("run not found")
        if record.principal.user != principal.user and not (allow_approvers and principal.can("workorders:approve")):
            raise LookupError("run not found")
        return record

    def _advance(self, record: RunRecord, payload: Any) -> RunRecord:
        config: RunnableConfig = {
            "configurable": {"thread_id": record.thread_id},
            "recursion_limit": 4 * self.settings.max_agent_steps + 10,
        }
        try:
            result = self.graph.invoke(payload, config)
        except Exception:
            record.status = "failed"
            log.exception("run failed", extra={"fields": {"run": record.run_id}})
            raise
        interrupts = result.get("__interrupt__")
        record.trace = list(result.get("trace", []))
        if interrupts:
            record.status, record.pending = "awaiting_approval", interrupts[0].value
            record.result = None
        else:
            record.status, record.pending, record.result = "completed", None, result["final"]
        log.info("run advanced", extra={"fields": {"run": record.run_id, "status": record.status}})
        return record


def build_service(settings: Settings, llm: BaseChatModel, eam: EamApi | None = None) -> AgentService:
    db = MaintenanceDb(f"file:agent-maintenance-{uuid.uuid4().hex}?mode=memory&cache=shared")
    api: EamApi = eam or (HttpEamApi(settings.eam_api_url) if settings.eam_api_url else InMemoryEamApi(db))
    registry = build_registry(KnowledgeBase(), db, api)
    return AgentService(settings, llm, registry, use_llm_router=settings.llm_provider == "openai")
