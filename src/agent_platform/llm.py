"""Chat models.

- `openai`: LangChain `ChatOpenAI` against any OpenAI-compatible endpoint (OpenAI, Azure via gateway, Ollama, vLLM),
  using native tool calling and structured outputs.
- `offline`: `RuleBasedToolCaller`, a deterministic BaseChatModel that emits real LangChain tool calls from keyword
  rules and composes answers from tool outputs. It is NOT a language model; it exists so the LangGraph workflow,
  authorization, approvals and tracing are fully testable without network access or API keys.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool

from agent_platform.config import Settings
from agent_platform.tools.knowledge import tokens

ASSET = re.compile(r"\b[A-Z]{1,4}-\d{1,4}\b")
WO = re.compile(r"\bWO-\d{1,8}\b")
OUTPUT = re.compile(r"<tool_output tool=\"([\w-]+)\"[^>]*>(.*)</tool_output>", re.DOTALL)
WORDS = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def build_chat_model(settings: Settings) -> BaseChatModel:
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model,
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key or "not-needed-for-local-servers",  # type: ignore[arg-type,unused-ignore]
            temperature=0,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )
    return RuleBasedToolCaller()


def _words(text: str) -> set[str]:
    return set(WORDS.findall(text.lower()))


class RuleBasedToolCaller(BaseChatModel):
    bound_tools: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "rule-based-tool-caller"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> RuleBasedToolCaller:  # type: ignore[override,unused-ignore]
        names = [t.name if isinstance(t, BaseTool) else str(t["name"]) for t in tools]
        return self.model_copy(update={"bound_tools": names})

    # ---- planning ------------------------------------------------------------------------------------

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        question = str(messages[last_human].content)
        results = [self._parse(m) for m in messages[last_human + 1 :] if isinstance(m, ToolMessage)]
        call = self._next_call(question, results)
        if call:
            name, args = call
            msg = AIMessage(
                content="",
                tool_calls=[{"name": name, "args": args, "id": f"call_{len(results)}_{name}", "type": "tool_call"}],
            )
        else:
            msg = AIMessage(content=self._compose(question, results))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @staticmethod
    def _parse(m: ToolMessage) -> tuple[str, dict[str, Any]]:
        match = OUTPUT.search(str(m.content))
        if not match:
            return (m.name or "unknown", {"ok": False, "error": str(m.content)})
        try:
            return match.group(1), json.loads(match.group(2))
        except json.JSONDecodeError:
            return match.group(1), {"ok": False, "error": "unreadable tool output"}

    def _next_call(self, q: str, results: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]] | None:
        done = {name for name, _ in results}
        has = set(self.bound_tools)
        asset = ASSET.search(q)
        wo = WO.search(q)
        lower = q.lower()
        if results:
            name, payload = results[-1]
            if name == "list_documents" and "summarize_document" in has and payload.get("ok") and "summar" in lower:
                docs = payload["data"]["documents"]
                if docs:
                    best = max(docs, key=lambda d: len(_words(d["title"] + " " + d["id"]) & _words(q)))
                    return "summarize_document", {"document_id": best["id"]}
            return None
        if "create_work_order" in has and re.search(r"\b(create|raise|open|log)\b.*work order", lower) and asset:
            description = q.split(":", 1)[1].strip() if ":" in q else q
            priority = 1 if re.search(r"urgent|emergency|immediately", lower) else 2 if "high" in lower else 3
            return "create_work_order", {
                "asset_id": asset.group(0),
                "description": description[:300],
                "priority": priority,
            }
        if "get_work_order" in has and wo:
            return "get_work_order", {"wo_num": wo.group(0)}
        if "maintenance_kpis" in has and asset and re.search(r"mttr|mtbf|availability|kpi|downtime|reliab|cost", lower):
            return "maintenance_kpis", {"asset_id": asset.group(0)}
        if "top_failure_modes" in has and re.search(r"failure mode|most common failure", lower):
            return "top_failure_modes", {"limit": 5}
        if "list_open_work_orders" in has and re.search(r"open work order|backlog", lower):
            return "list_open_work_orders", {"asset_id": asset.group(0) if asset else None}
        if "list_documents" in has and "list_documents" not in done:
            return "list_documents", {}
        if "search_knowledge_base" in has:
            return "search_knowledge_base", {"query": q[:500]}
        return None

    # ---- answer composition ----------------------------------------------------------------------------

    def _compose(self, q: str, results: list[tuple[str, dict[str, Any]]]) -> str:
        if not results:
            return "I can't help with that using the tools available to me."
        name, payload = results[-1]
        if not payload.get("ok"):
            error = str(payload.get("error", "unknown error"))
            if "rejected" in error:
                return "The action was not executed because the approver rejected it."
            return f"I could not complete that: {error}"
        data: dict[str, Any] = payload["data"]
        if name == "search_knowledge_base":
            # Answer only if the best sentences cover at least half of the question's terms (grounding check).
            q_terms = set(tokens(q))
            sentences = [
                (len(q_terms & set(tokens(s))), s.strip(), p["source"])
                for p in data["passages"]
                for s in re.split(r"(?<=[.!?])\s+", p["text"])
            ]
            best = [s for s in sorted(sentences, key=lambda x: -x[0]) if s[0] >= 1][:2]
            covered = set().union(*(set(tokens(text)) for _, text, _ in best)) & q_terms if best else set()
            if len(covered) < 0.5 * len(q_terms):
                return "I don't know based on the documents available to you."
            return " ".join(f"{text} [{src}]" for _, text, src in best)
        if name == "maintenance_kpis":
            if not data["failures"]:
                return f"{data['asset_id']} had no unplanned failures in {data['period']}."
            return (
                f"{data['asset_id']} ({data['description']}, criticality {data['criticality']}) in {data['period']}: "
                f"{data['failures']} unplanned failures, MTTR {data['mttr_hours']} h, MTBF {data['mtbf_hours']} h, "
                f"availability {data['availability'] * 100:.2f}%, "
                f"maintenance cost {data['maintenance_cost_eur']:.0f} EUR."
            )
        if name == "top_failure_modes":
            modes = ", ".join(
                f"{m['problem_code']} ({m['n']} work orders, {m['cost']:.0f} EUR)" for m in data["failure_modes"]
            )
            return f"Most frequent failure modes: {modes}." if modes else "No failure modes recorded."
        if name == "list_open_work_orders":
            wos = data["work_orders"]
            if not wos:
                return "There are no open work orders."
            return (
                "Open work orders: "
                + "; ".join(
                    f"{w['wo_num']} {w['asset_id']} {w['status']} P{w['priority']} ({w['description']})" for w in wos
                )
                + "."
            )
        if name == "get_work_order":
            return (
                f"{data['wo_num']} for {data['asset_id']} is {data['status']} "
                f"(priority {data['priority']}): {data['description']}."
            )
        if name == "create_work_order":
            return (
                f"Created work order {data['wo_num']} for {data['asset_id']} with priority {data['priority']}; "
                f"status {data['status']}."
            )
        if name == "summarize_document":
            parts = " ".join(f"{s['section']}: {s['summary']} [{s['source']}]" for s in data["sections"])
            return f"{data['title']} — {parts}"
        if name == "list_documents":
            return "Documents you can access: " + ", ".join(d["title"] for d in data["documents"]) + "."
        return json.dumps(data)[:1000]
