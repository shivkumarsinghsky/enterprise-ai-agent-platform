from prometheus_client import Counter, Histogram

RUNS = Counter("agent_runs_total", "Agent runs by outcome", ["agent", "outcome"])
TOOL_CALLS = Counter("agent_tool_calls_total", "Tool executions", ["tool", "outcome"])
APPROVALS = Counter("agent_approvals_total", "Human approval decisions", ["decision"])
TOKENS = Counter("agent_llm_tokens_total", "LLM tokens", ["kind"])
LLM_SECONDS = Histogram("agent_llm_seconds", "LLM call latency", buckets=(0.01, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
