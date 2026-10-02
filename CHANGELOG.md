# Changelog

All notable changes to this repository are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-10-02

### Added

- LangGraph orchestration: input guard, routing (keywords or LLM structured output), agent/tool loop, finalisation.
- Four agents: knowledge (RAG), document, reporting and operations (EAM API).
- Tool registry and executor: schema validation, permission checks, per-agent allow-lists, budgets, timeouts,
  untrusted output wrapping, injection neutralisation.
- Human-in-the-loop approvals with interrupts, separation of duties and idempotent execution.
- Checkpointed conversation memory per tenant/user; run traces; Prometheus metrics.
- OpenAI-compatible model support (OpenAI, Ollama, vLLM) and a deterministic offline model.
- FastAPI API, CLI, scenario evaluation, tests, Docker, Compose, docs and ADR-001 to ADR-004.
