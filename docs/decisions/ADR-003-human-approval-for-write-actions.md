# ADR-003: Human Approval for Write Actions, With Separation of Duties

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

Agents that change business systems (create work orders, raise purchase requests) can cause real cost when they act
on a misunderstanding. Users also need accountability: who decided this action?

## Decision

Write tools are marked `requires_approval`. Before execution the graph calls `interrupt()` with the proposed tool and
arguments; the run is checkpointed and returns `awaiting_approval`. An approver with `workorders:approve`, **who is not
the requester**, approves or rejects. Approved calls execute with an idempotency key; rejections are returned to the
model as tool results. Decisions are recorded in the trace.

## Alternatives Considered

- **Self-confirmation by the requester** — lower friction; acceptable for low-risk actions (could be a per-tool
  policy).
- **No approval, with undo** — fast, but many external actions cannot be undone cleanly.
- **Approval outside the agent (ticket)** — strong controls, loses the conversational context.

## Trade-offs

Slower for routine actions. A per-tool policy (risk thresholds, amount limits) is the natural refinement.

## Consequences

Write actions are auditable, idempotent and never executed without a second person.
