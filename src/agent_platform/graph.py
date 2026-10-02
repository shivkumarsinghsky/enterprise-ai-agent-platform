"""LangGraph workflow.

    START → guard_input → route → agent ⇄ tools → finalize → END
                                          │
                                          └─ interrupt() for write tools (human-in-the-loop approval)

State is checkpointed per thread, so a run can pause for approval and resume later (even in another process when
a persistent checkpointer is used), and conversation history carries over between turns of the same thread.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from agent_platform import metrics
from agent_platform.agents import AGENTS, RouteDecision, route_by_keywords
from agent_platform.config import Settings
from agent_platform.security import Principal, check_input, looks_like_injection, redact
from agent_platform.tools.registry import RunBudget, ToolExecutor, ToolRegistry

HISTORY_WINDOW = 20


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    principal: dict[str, Any]
    run_id: str
    requested_agent: str | None
    agent: str
    steps: int
    tool_calls: int
    write_actions: int
    injection_suspected: bool
    sources: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    final: dict[str, Any]


def _principal(state: AgentState) -> Principal:
    p = state["principal"]
    return Principal(p["tenant"], p["user"], frozenset(p["roles"]))


def build_graph(
    settings: Settings,
    llm: BaseChatModel,
    registry: ToolRegistry,
    checkpointer: BaseCheckpointSaver[Any],
    use_llm_router: bool = False,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    executor = ToolExecutor(registry, settings.tool_timeout_seconds)

    def guard_input(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        _, suspected = check_input(str(last.content))
        # Per-run fields are reset here; messages persist across turns of the same thread.
        return {
            "steps": 0,
            "tool_calls": 0,
            "write_actions": 0,
            "injection_suspected": suspected,
            "sources": [],
            "actions": [],
            "trace": [
                {"node": "guard_input", "injection_suspected": suspected, "input": redact(str(last.content))[:300]}
            ],
        }

    def route(state: AgentState) -> dict[str, Any]:
        requested = state.get("requested_agent")
        question = str(state["messages"][-1].content)
        if requested:
            agent, how = requested, "requested"
        elif use_llm_router:
            decision = llm.with_structured_output(RouteDecision).invoke(
                [
                    SystemMessage(
                        "Choose the agent for the user's request:\n"
                        + "\n".join(f"- {a.name}: {a.description}" for a in AGENTS.values())
                    ),
                    HumanMessage(question),
                ]
            )
            assert isinstance(decision, RouteDecision)
            agent, how = decision.agent, f"llm: {decision.reason}"
        else:
            agent, how = route_by_keywords(question), "keywords"
        return {"agent": agent, "trace": state["trace"] + [{"node": "route", "agent": agent, "method": how}]}

    def agent_node(state: AgentState) -> dict[str, Any]:
        spec = AGENTS[state["agent"]]
        model = llm.bind_tools([registry.get(t).as_langchain_tool() for t in spec.tools])
        started = time.perf_counter()
        response = model.invoke([SystemMessage(spec.system_prompt), *state["messages"][-HISTORY_WINDOW:]])
        elapsed = time.perf_counter() - started
        metrics.LLM_SECONDS.observe(elapsed)
        usage = getattr(response, "usage_metadata", None) or {}
        for kind in ("input_tokens", "output_tokens"):
            metrics.TOKENS.labels(kind=kind).inc(int(usage.get(kind, 0)))
        entry = {
            "node": "agent",
            "agent": spec.name,
            "step": state["steps"] + 1,
            "tool_calls": [c["name"] for c in getattr(response, "tool_calls", [])],
            "latency_ms": round(elapsed * 1000, 2),
            "tokens": {k: int(usage.get(k, 0)) for k in ("input_tokens", "output_tokens")},
        }
        return {"messages": [response], "steps": state["steps"] + 1, "trace": state["trace"] + [entry]}

    def after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and state["steps"] < settings.max_agent_steps:
            return "tools"
        return "finalize"

    def tools_node(state: AgentState) -> dict[str, Any]:
        principal = _principal(state)
        spec_agent = AGENTS[state["agent"]]
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        budget = RunBudget(
            settings.max_tool_calls_per_run,
            settings.max_write_actions_per_run,
            state["tool_calls"],
            state["write_actions"],
        )
        messages: list[ToolMessage] = []
        sources, actions, trace = list(state["sources"]), list(state["actions"]), list(state["trace"])
        for call in last.tool_calls:
            name, args = call["name"], call["args"]
            call_id = call["id"] or name
            spec = registry.tools.get(name)
            if spec is not None and spec.requires_approval and principal.can(spec.permission):
                decision = interrupt(
                    {
                        "tool": name,
                        "args": args,
                        "tool_call_id": call_id,
                        "requested_by": principal.user,
                        "agent": spec_agent.name,
                        "reason": f"'{name}' changes data in an external system",
                    }
                )
                trace.append({"node": "approval", "tool": name, "decision": decision})
                if not decision.get("approved"):
                    content = (
                        f'<tool_output tool="{name}" trust="untrusted">'
                        '{"ok": false, "error": "rejected by approver"}</tool_output>'
                    )
                    messages.append(ToolMessage(content=content, tool_call_id=call_id, name=name))
                    actions.append(
                        {"tool": name, "args": args, "status": "rejected", "approver": decision.get("approver")}
                    )
                    continue
            result = executor.execute(
                principal,
                name,
                args,
                set(spec_agent.tools),
                budget,
                context={"run_id": state["run_id"], "tool_call_id": call_id} if spec and spec.risk == "write" else None,
            )
            metrics.TOOL_CALLS.labels(tool=name, outcome="ok" if result.ok else "error").inc()
            trace.append(
                {"node": "tool", "tool": name, "ok": result.ok, "error": result.error, "latency_ms": result.duration_ms}
            )
            if result.ok:
                for passage in result.output.get("passages", []):
                    if looks_like_injection(passage["text"]):
                        passage["text"] = "[removed: content resembled instructions]"
                        trace.append({"node": "guard_tool_output", "source": passage["source"], "flagged": True})
                    sources.append(
                        {"source": passage["source"], "title": passage["title"], "section": passage["section"]}
                    )
                for section in result.output.get("sections", []):
                    sources.append(
                        {"source": section["source"], "title": result.output["title"], "section": section["section"]}
                    )
                if spec and spec.risk == "write":
                    actions.append({"tool": name, "args": args, "status": "executed", "result": result.output})
            messages.append(
                ToolMessage(
                    content=result.to_message_content(settings.max_tool_output_chars), tool_call_id=call_id, name=name
                )
            )
        return {
            "messages": messages,
            "tool_calls": budget.tool_calls,
            "write_actions": budget.write_actions,
            "sources": sources,
            "actions": actions,
            "trace": trace,
        }

    def finalize(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            answer = "I stopped because this request needed more steps than allowed. Please narrow it down."
            limited = True
        else:
            answer, limited = str(last.content), False
        unique_sources = list({s["source"]: s for s in state["sources"]}.values())
        cited = [s for s in unique_sources if f"[{s['source']}]" in answer]
        final: dict[str, Any] = {
            "answer": answer,
            "agent": state["agent"],
            "sources": cited or unique_sources,
            "actions": state["actions"],
            "step_limit_reached": limited,
            "injection_suspected": state["injection_suspected"],
        }
        metrics.RUNS.labels(agent=state["agent"], outcome="step_limit" if limited else "completed").inc()
        return {"final": final, "trace": state["trace"] + [{"node": "finalize", "sources": len(final["sources"])}]}

    graph = StateGraph(AgentState)
    graph.add_node("guard_input", guard_input)
    graph.add_node("route", route)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "guard_input")
    graph.add_edge("guard_input", "route")
    graph.add_edge("route", "agent")
    graph.add_conditional_edges("agent", after_agent, {"tools": "tools", "finalize": "finalize"})
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
