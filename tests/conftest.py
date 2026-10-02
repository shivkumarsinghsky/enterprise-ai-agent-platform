import pytest

from agent_platform.config import Settings
from agent_platform.llm import RuleBasedToolCaller
from agent_platform.security import Principal
from agent_platform.service import AgentService, build_service

TECH = Principal("acme", "tara", frozenset({"technician"}))
SUPERVISOR = Principal("acme", "sam", frozenset({"supervisor"}))
VIEWER = Principal("acme", "vic", frozenset({"viewer"}))
GLOBEX = Principal("globex", "gina", frozenset({"supervisor"}))


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def service(settings: Settings) -> AgentService:
    return build_service(settings, RuleBasedToolCaller())
