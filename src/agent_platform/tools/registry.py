"""Tool registry and executor.

The model *proposes* tool calls; the platform *executes* them. Every call goes through `ToolExecutor.execute`, which:
1. validates arguments against the tool's pydantic schema,
2. checks the caller's permission (the user's, never the model's),
3. enforces per-run budgets (tool calls, write actions),
4. runs with a timeout,
5. truncates and marks the output as untrusted data before it goes back to the model.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ValidationError

from agent_platform.security import AuthorizationError, Principal

ToolFunc = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    permission: str
    risk: Literal["read", "write"]
    func: ToolFunc
    requires_approval: bool = False

    def as_langchain_tool(self) -> StructuredTool:
        """Schema-only tool for `bind_tools`. Execution never happens through LangChain, only via the executor."""

        def _not_executed(**_: Any) -> str:
            raise RuntimeError("tools are executed by ToolExecutor, not by LangChain")

        return StructuredTool.from_function(
            func=_not_executed, name=self.name, description=self.description, args_schema=self.args_schema
        )


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: dict[str, Any]
    duration_ms: float
    error: str | None = None

    def to_message_content(self, max_chars: int) -> str:
        payload = {"ok": self.ok, "data": self.output} if self.ok else {"ok": False, "error": self.error}
        text = json.dumps(payload, default=str)
        if len(text) > max_chars:
            text = text[:max_chars] + '..."(truncated)"'
        # Delimit tool output and remind the model it is data, not instructions.
        return f'<tool_output tool="{self.tool}" trust="untrusted">{text}</tool_output>'


@dataclass
class RunBudget:
    max_tool_calls: int
    max_write_actions: int
    tool_calls: int = 0
    write_actions: int = 0


@dataclass
class ToolRegistry:
    tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self.tools:
            raise ValueError(f"duplicate tool {spec.name}")
        self.tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self.tools:
            raise KeyError(f"unknown tool '{name}'")
        return self.tools[name]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "permission": t.permission,
                "risk": t.risk,
                "requiresApproval": t.requires_approval,
                "parameters": t.args_schema.model_json_schema(),
            }
            for t in self.tools.values()
        ]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, timeout_seconds: float = 10.0) -> None:
        self.registry = registry
        self.timeout_seconds = timeout_seconds
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")

    def authorize(self, principal: Principal, name: str, allowed: set[str]) -> ToolSpec:
        if name not in allowed:
            raise AuthorizationError(f"tool '{name}' is not available to this agent")
        spec = self.registry.get(name)
        if not principal.can(spec.permission):
            raise AuthorizationError(f"user lacks permission '{spec.permission}' required by '{name}'")
        return spec

    def execute(
        self,
        principal: Principal,
        name: str,
        args: dict[str, Any],
        allowed: set[str],
        budget: RunBudget,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        try:
            spec = self.authorize(principal, name, allowed)
            if budget.tool_calls >= budget.max_tool_calls:
                raise AuthorizationError("tool call budget for this run is exhausted")
            if spec.risk == "write" and budget.write_actions >= budget.max_write_actions:
                raise AuthorizationError("write action budget for this run is exhausted")
            validated = spec.args_schema.model_validate(args)
            budget.tool_calls += 1
            if spec.risk == "write":
                budget.write_actions += 1
            future = self._pool.submit(spec.func, principal, **validated.model_dump(), **(context or {}))
            output = future.result(timeout=self.timeout_seconds)
            return ToolResult(name, True, output, round((time.perf_counter() - started) * 1000, 2))
        except ValidationError as e:
            error = "invalid arguments: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        except AuthorizationError as e:
            error = f"not authorized: {e}"
        except concurrent.futures.TimeoutError:
            error = f"tool timed out after {self.timeout_seconds}s"
        except (KeyError, ValueError, LookupError) as e:
            error = str(e)
        return ToolResult(name, False, {}, round((time.perf_counter() - started) * 1000, 2), error)
