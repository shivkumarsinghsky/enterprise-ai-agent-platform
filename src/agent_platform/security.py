"""Principals, role → permission mapping, and input/output guardrails."""

from __future__ import annotations

import re
from dataclasses import dataclass

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"knowledge:read", "documents:read"}),
    "technician": frozenset(
        {"knowledge:read", "documents:read", "reports:read", "workorders:read", "workorders:write"}
    ),
    "supervisor": frozenset(
        {
            "knowledge:read",
            "documents:read",
            "reports:read",
            "workorders:read",
            "workorders:write",
            "workorders:approve",
        }
    ),
}
ROLE_GROUPS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"all-staff"}),
    "technician": frozenset({"all-staff", "maintenance"}),
    "supervisor": frozenset({"all-staff", "maintenance", "management"}),
}


@dataclass(frozen=True)
class Principal:
    tenant: str
    user: str
    roles: frozenset[str]

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset().union(*(ROLE_PERMISSIONS.get(r, frozenset()) for r in self.roles))

    @property
    def groups(self) -> frozenset[str]:
        return frozenset().union(*(ROLE_GROUPS.get(r, frozenset()) for r in self.roles))

    def can(self, permission: str) -> bool:
        return permission in self.permissions


class AuthorizationError(PermissionError):
    pass


class GuardrailViolation(ValueError):
    pass


INJECTION = re.compile(
    r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above)\s+(instructions|rules)|reveal\s+(the\s+|your\s+)?system\s+prompt"
    r"|you\s+are\s+now\b|<\s*/?\s*(system|assistant)\s*>|new\s+instructions\s*:",
    re.IGNORECASE,
)
PII = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[card]"),
]
MAX_INPUT_CHARS = 4_000


def check_input(text: str) -> tuple[str, bool]:
    """Returns (cleaned input, injection_suspected). Suspected injection is flagged and traced, not silently
    blocked: authorization, not prompt wording, is what prevents harmful actions."""
    cleaned = text.strip()
    if not cleaned:
        raise GuardrailViolation("input is empty")
    if len(cleaned) > MAX_INPUT_CHARS:
        raise GuardrailViolation(f"input exceeds {MAX_INPUT_CHARS} characters")
    return cleaned, bool(INJECTION.search(cleaned))


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION.search(text))


def redact(text: str) -> str:
    for pattern, repl in PII:
        text = pattern.sub(repl, text)
    return text
