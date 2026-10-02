from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: str = "INFO"
    #: "offline" (deterministic rule-based tool caller, no network) or "openai" (any OpenAI-compatible API)
    llm_provider: str = "offline"
    llm_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    llm_timeout_seconds: float = 30.0

    #: Hard limits that bound cost and stop runaway loops.
    max_agent_steps: int = Field(default=6, ge=1, le=50)
    max_tool_calls_per_run: int = Field(default=8, ge=1, le=100)
    max_write_actions_per_run: int = Field(default=2, ge=0, le=20)
    tool_timeout_seconds: float = 10.0
    max_tool_output_chars: int = 4_000

    #: Optional external EAM API for operations tools; in-memory fake when unset.
    eam_api_url: str | None = None

    #: JSON: {"<api key>": {"tenant": "...", "user": "...", "roles": ["technician"]}}
    api_keys: str = (
        '{"dev-tech-key-001": {"tenant": "acme", "user": "tara", "roles": ["technician"]},'
        ' "dev-supervisor-01": {"tenant": "acme", "user": "sam", "roles": ["supervisor"]}}'
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
