# ADR-004: Deterministic Offline Model for Development and CI

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

Testing an agent platform against a real LLM is slow, costly, non-deterministic and needs secrets in CI. Most of the
platform's risk lives outside the model: routing, authorization, approvals, budgets, memory and tracing.

## Decision

Provide `RuleBasedToolCaller`, a `BaseChatModel` that emits genuine LangChain tool calls from keyword rules and
composes answers from tool outputs, selected with `LLM_PROVIDER=offline` (the default). The real-model path
(`ChatOpenAI` with tool calling and structured outputs) is tested against a scripted OpenAI-compatible HTTP server.

## Alternatives Considered

- **Recorded LLM responses (cassettes)** — realistic, but brittle when prompts change.
- **Always use a real model in CI** — highest fidelity; cost, flakiness and secret management.

## Trade-offs

The offline model does not test reasoning quality. Model-quality evaluation needs the same scenario suite run against
real models on a schedule, with results tracked over time.

## Consequences

The full graph, including approvals and authorization failures, is tested deterministically on every commit.
