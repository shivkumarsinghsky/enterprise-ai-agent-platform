# ADR-001: LangGraph State Machine for Agent Orchestration

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

Enterprise agents need loops (model → tool → model), pauses for human approval that can last hours, durable state,
hard step limits and a trace of what happened. A free-form "agent executor" loop hides control flow and makes
approvals and resumption awkward.

## Decision

Model the orchestration as an explicit **LangGraph** `StateGraph` (`guard_input → route → agent ⇄ tools → finalize`)
with a checkpointer. Approvals use `interrupt()` and resume with `Command(resume=...)`. LangChain is used for model
abstraction (`BaseChatModel`, `bind_tools`, `with_structured_output`).

## Alternatives Considered

- **Hand-written loop** — no dependency, but checkpointing, interrupts and resumption must be rebuilt.
- **Autonomous multi-agent frameworks** (free-form agent conversations) — flexible, but hard to bound, test and
  audit for enterprise actions.
- **Workflow engines (Temporal, Step Functions)** — excellent durability for long processes; the LLM loop and tool
  schemas would be custom code. A good host for very long-running, multi-day processes.

## Trade-offs

A graph is less "autonomous" than free-form agents, which is the point: control flow is explicit and testable.
Framework upgrades must be tracked (LangGraph evolves quickly).

## Consequences

Each node is unit-testable; runs are resumable; the trace mirrors the graph.
