# ADR-002: Tool Authorization Enforced by the Platform, Not the Model

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

LLMs can be persuaded — by users or by injected content — to call tools they should not. Instructions such as "only
create work orders for supervisors" in a prompt are not a security control.

## Decision

Tools are registered with a required permission, a risk level and an approval flag. The model only proposes calls;
the `ToolExecutor` checks the agent's allow-list and the **user's** permission, validates arguments with pydantic,
enforces per-run budgets and timeouts, and wraps outputs as untrusted data. Data tools are fixed, parameterised and
tenant-scoped; there is no text-to-SQL.

## Alternatives Considered

- **Prompt-based restrictions** — not enforceable.
- **Giving the agent a service account** — simple, but every user effectively gets the service account's rights
  (confused deputy).
- **Text-to-SQL for reporting** — flexible, but requires a robust SQL sandbox, row-level security and query cost
  limits; deferred.

## Trade-offs

Each new capability needs a tool definition with a schema and permission, rather than "the model can call any API".

## Consequences

Tests show that a viewer whose request makes the model call `create_work_order` receives "not authorized", and that
reporting tools cannot modify data.
