"""LiteLLM gateway: key pool, ordered failover, built-in response cache, failure simulation."""
import logging
import warnings
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import litellm
from litellm import Cache

from .config import Settings

logger = logging.getLogger("gateway")
litellm.suppress_debug_info = True
litellm.set_verbose = False
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")  # noisy, harmless (litellm cache)

VALID_SIMULATED_STATUSES = {429, 500, 502, 503}


class AllProvidersUnavailable(Exception):
    """Every key in the pool failed for this request."""


class SimulatedProviderError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"simulated provider error {status_code}")
        self.status_code = status_code


class EmptyReplyError(Exception):
    pass


@dataclass
class LLMResult:
    reply: str
    resolution_source: str  # LIVE_INFERENCE | LLM_CACHE
    model: str
    input_tokens: int
    output_tokens: int
    key_id: Optional[str]
    fallback_attempts: int


def parse_simulation(header: Optional[str]) -> Dict[str, int]:
    """'key_1=429,key_2=500' or '*=429' -> {'key_1': 429, 'key_2': 500}. Bad pairs are ignored."""
    result: Dict[str, int] = {}
    if not header:
        return result
    for part in header.split(","):
        name, sep, value = part.strip().partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name:
            continue
        try:
            status = int(value)
        except ValueError:
            continue
        if status in VALID_SIMULATED_STATUSES:
            result[name] = status
    return result


class LLMGateway:
    def __init__(self, settings: Settings):
        self.settings = settings
        # key ids follow pool order: key_1, key_2, ...  (the key itself is never exposed)
        self.keys: Dict[str, str] = {f"key_{i}": k for i, k in enumerate(settings.api_keys, start=1)}
        self.cache_enabled = self._init_cache()

    def _init_cache(self) -> bool:
        """Enable LiteLLM's built-in cache. The cache key is built from model + messages
        (+ sampling params) and does NOT depend on which API key served the call."""
        try:
            if self.settings.cache_type == "disk":
                litellm.cache = Cache(type="disk", disk_cache_dir=self.settings.cache_dir)
            else:
                litellm.cache = Cache(type="local")
            return True
        except Exception as exc:  # disk cache unavailable -> degrade to in-memory
            logger.warning("cache init failed (%s); falling back to in-memory", type(exc).__name__)
            try:
                litellm.cache = Cache(type="local")
                return True
            except Exception:
                litellm.cache = None
                return False

    # -- single provider call (monkeypatched in tests) ------------------------------------
    def _complete(self, api_key: str, messages: List[Dict[str, str]], bypass_cache: bool):
        kwargs: Dict[str, Any] = dict(
            model=self.settings.model,
            messages=messages,
            api_key=api_key,
            timeout=self.settings.request_timeout,
            num_retries=0,  # we fail over to the next key instead of retrying the same one
            caching=self.cache_enabled,
            max_tokens=self.settings.max_tokens,
            drop_params=True,  # silently drop params a model doesn't support instead of erroring
        )
        if self.settings.reasoning_effort:
            kwargs["reasoning_effort"] = self.settings.reasoning_effort
        if bypass_cache:
            kwargs["cache"] = {"no-cache": True}  # skip cache READ (used by failure simulation)
        if self.settings.mock_llm:
            last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            kwargs["mock_response"] = f"Mock reply to: {last_user} [context={len(messages)} messages]"
        return litellm.completion(**kwargs)

    def chat(self, messages: List[Dict[str, str]], simulate: Optional[Dict[str, int]] = None) -> LLMResult:
        simulate = simulate or {}
        attempts = 0
        for key_id, api_key in self.keys.items():
            status = simulate.get(key_id, simulate.get("*"))
            try:
                if status is not None:
                    raise SimulatedProviderError(status)
                response = self._complete(api_key, messages, bypass_cache=bool(simulate))
                reply = (response.choices[0].message.content or "").strip()
                if not reply:
                    raise EmptyReplyError()
            except Exception as exc:
                attempts += 1
                logger.warning(
                    "%s failed (%s, status=%s); trying next key",
                    key_id, type(exc).__name__, getattr(exc, "status_code", None),
                )
                continue

            hidden = getattr(response, "_hidden_params", None) or {}
            cache_hit = bool(hidden.get("cache_hit"))
            usage = getattr(response, "usage", None)
            return LLMResult(
                reply=reply,
                resolution_source="LLM_CACHE" if cache_hit else "LIVE_INFERENCE",
                model=self.settings.model,
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                key_id=None if cache_hit else key_id,
                fallback_attempts=0 if cache_hit else attempts,
            )
        raise AllProvidersUnavailable()