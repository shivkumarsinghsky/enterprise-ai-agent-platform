"""Knowledge tools: permission-aware retrieval over the enterprise knowledge base (RAG as a tool).

A compact BM25 index over document sections; the full RAG pipeline (hybrid retrieval, pgvector, evaluation) lives in
https://github.com/shivkumarsinghsky/rag-enterprise-assistant. ACL filtering happens before ranking.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.security import Principal

DATA = Path(__file__).resolve().parents[1] / "data" / "knowledge"
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STOP = frozenset(
    "a an and are as at be by do does for from how i in is it of on or the to what when which who with".split()
)


SUFFIXES = ("ment", "ing", "ion", "ed", "es", "s", "e")


def stem(token: str) -> str:
    """Light suffix stripping (replacement/replacing -> replac). Not a full stemmer."""
    for _ in range(2):
        for suffix in SUFFIXES:
            if len(token) > len(suffix) + 3 and token.endswith(suffix) and not token[-len(suffix) - 1].isdigit():
                token = token[: -len(suffix)]
                break
        else:
            break
    return token


def tokens(text: str) -> list[str]:
    return [stem(t) for t in TOKEN.findall(text.lower()) if t not in STOP]


@dataclass(frozen=True)
class Passage:
    id: str
    document_id: str
    title: str
    section: str
    text: str
    acl: frozenset[str]


class KnowledgeBase:
    def __init__(self, directory: Path = DATA, tenant: str = "acme") -> None:
        acl = json.loads((directory / "acl.json").read_text())
        self.tenant = tenant
        self.passages: list[Passage] = []
        for path in sorted(directory.glob("*.md")):
            text = path.read_text()
            title = text.splitlines()[0].lstrip("# ").strip()
            for i, block in enumerate(re.split(r"^## ", text, flags=re.MULTILINE)[1:]):
                section, _, body = block.partition("\n")
                self.passages.append(
                    Passage(
                        f"{path.stem}#{i}",
                        path.stem,
                        title,
                        section.strip(),
                        " ".join(body.split()),
                        frozenset(acl.get(path.stem, [])),
                    )
                )

    def visible(self, principal: Principal) -> list[Passage]:
        if principal.tenant != self.tenant:
            return []
        return [p for p in self.passages if not p.acl or p.acl & principal.groups]

    def search(self, principal: Principal, query: str, k: int = 3) -> list[tuple[Passage, float]]:
        docs = self.visible(principal)
        q = tokens(query)
        if not docs or not q:
            return []
        toks = [tokens(f"{p.title} {p.section} {p.text}") for p in docs]
        avg = sum(map(len, toks)) / len(toks)
        df = Counter(t for ts in toks for t in set(ts))
        scored = []
        for p, ts in zip(docs, toks, strict=True):
            tf = Counter(ts)
            s = sum(
                math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                * tf[t]
                * 2.5
                / (tf[t] + 1.5 * (0.25 + 0.75 * len(ts) / avg))
                for t in q
                if t in tf
            )
            if s > 0:
                scored.append((p, s))
        return sorted(scored, key=lambda x: -x[1])[:k]


class SearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="Search query in natural language")


def make_search_tool(kb: KnowledgeBase):  # type: ignore[no-untyped-def]
    def search_knowledge_base(principal: Principal, query: str, **_: Any) -> dict[str, Any]:
        hits = kb.search(principal, query)
        return {
            "passages": [
                {"source": p.id, "title": p.title, "section": p.section, "text": p.text, "score": round(s, 3)}
                for p, s in hits
            ]
        }

    return search_knowledge_base


class DocumentArgs(BaseModel):
    document_id: str = Field(pattern=r"^[a-z0-9-]{1,100}$")


class NoArgs(BaseModel):
    pass


def make_document_tools(kb: KnowledgeBase):  # type: ignore[no-untyped-def]
    def list_documents(principal: Principal, **_: Any) -> dict[str, Any]:
        docs: dict[str, str] = {}
        for p in kb.visible(principal):
            docs.setdefault(p.document_id, p.title)
        return {"documents": [{"id": d, "title": t} for d, t in docs.items()]}

    def summarize_document(principal: Principal, document_id: str, **_: Any) -> dict[str, Any]:
        sections = [p for p in kb.visible(principal) if p.document_id == document_id]
        if not sections:
            raise LookupError(f"document '{document_id}' not found")  # same answer for missing and forbidden
        return {
            "document_id": document_id,
            "title": sections[0].title,
            "sections": [
                {"source": p.id, "section": p.section, "summary": re.split(r"(?<=[.!?])\s", p.text)[0]}
                for p in sections
            ],
        }

    return list_documents, summarize_document
