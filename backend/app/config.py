"""Environment-driven configuration. Nothing here ever logs or returns API keys."""
import os
from dataclasses import dataclass, field
from typing import List, Optional


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_list(value: Optional[str]) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def sqlite_path_from_url(url: str) -> str:
    """sqlite:////data/app.db -> /data/app.db ; sqlite:///./app.db -> ./app.db"""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return url[len(prefix):]
    return url  # allow a bare filesystem path


@dataclass
class Settings:
    api_keys: List[str] = field(default_factory=list)
    database_url: str = "sqlite:///./app.db"
    allowed_origins: List[str] = field(default_factory=lambda: ["http://localhost:3000"])
    enable_failure_simulation: bool = False
    model: str = "gemini/gemini-3.5-flash-lite"
    default_system_prompt: str = "You are a helpful, concise assistant."
    cache_type: str = "disk"  # "disk" (survives restarts) or "local" (in-memory)
    cache_dir: str = ""
    request_timeout: float = 20.0
    max_tokens: int = 1024
    reasoning_effort: str = ""  # minimal | low | medium | high | disable | "" (provider default)
    max_history_messages: int = 50
    mock_llm: bool = False  # tests only: skip the network, return a canned reply

    @property
    def db_path(self) -> str:
        return sqlite_path_from_url(self.database_url)

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        s = cls(
            api_keys=_as_list(env.get("LLM_API_KEYS")),
            database_url=env.get("DATABASE_URL", "sqlite:///./app.db"),
            allowed_origins=_as_list(env.get("ALLOWED_ORIGINS")) or ["http://localhost:3000"],
            enable_failure_simulation=_as_bool(env.get("ENABLE_FAILURE_SIMULATION")),
            model=env.get("LLM_MODEL", "gemini/gemini-3.5-flash-lite"),
            default_system_prompt=env.get("DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant."),
            cache_type=env.get("CACHE_TYPE", "disk").strip().lower(),
            cache_dir=env.get("CACHE_DIR", ""),
            request_timeout=float(env.get("REQUEST_TIMEOUT_SECONDS", "20")),
            max_tokens=int(env.get("MAX_TOKENS", "1024")),
            reasoning_effort=env.get("REASONING_EFFORT", "").strip().lower(),
            max_history_messages=int(env.get("MAX_HISTORY_MESSAGES", "50")),
            mock_llm=_as_bool(env.get("MOCK_LLM")),
        )
        if not s.cache_dir:
            s.cache_dir = os.path.join(os.path.dirname(os.path.abspath(s.db_path)), "cache")
        return s