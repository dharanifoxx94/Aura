"""
PSIE — LLM Gateway
===================
Provider‑agnostic router using LiteLLM with fallback, retries, rate limiting,
circuit breaker, and cost logging.
"""

import logging
import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Deque

import concurrent.futures
import litellm
litellm.suppress_debug_info = True   # prevent api_key= leaking in --verbose logs

from .constants import DEFAULT_LLM_TIMEOUT
from .exceptions import LLMError
from .db import db_connect

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Soft rate limits per provider (requests/minute, requests/day).
PROVIDER_LIMITS: Dict[str, Dict[str, int]] = {
    "groq":       {"rpm": 25,    "rpd": 14_000},
    "gemini":     {"rpm": 14,    "rpd": 1_400},
    "openrouter": {"rpm": 18,    "rpd": 45},
    "ollama":     {"rpm": 9_999, "rpd": 9_999_999},
}

_PROVIDER_HINTS: Dict[str, str] = {
    "groq":       "Check GROQ_API_KEY or visit console.groq.com",
    "gemini":     "Check GEMINI_API_KEY or visit ai.google.dev",
    "openrouter": "Check OPENROUTER_API_KEY or visit openrouter.ai",
    "ollama":     "Is Ollama running? Try: ollama serve  |  ollama pull gemma3:4b",
}

# Circuit breaker state per provider.
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.open = False

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.monotonic()
        if self.failures >= self.failure_threshold:
            self.open = True

    def record_success(self) -> None:
        self.failures = 0
        self.open = False

    def can_try(self) -> bool:
        if not self.open:
            return True
        if time.monotonic() - self.last_failure_time > self.recovery_timeout:
            self.open = False
            self.failures = 0
            return True
        return False


# ---------------------------------------------------------------------------
# Rate‑limit tracker (per provider, in‑memory with bounded deque)
# ---------------------------------------------------------------------------

class RateLimitTracker:
    """Track call timestamps per provider for soft rate‑limit enforcement."""

    def __init__(self, max_stored_per_provider: int = 1000) -> None:
        self._calls: Dict[str, Deque[float]] = {}
        self._maxlen = max_stored_per_provider

    def record(self, provider: str) -> None:
        now = time.monotonic()
        if provider not in self._calls:
            self._calls[provider] = deque(maxlen=self._maxlen)
        self._calls[provider].append(now)

    def rpm(self, provider: str) -> int:
        now = time.monotonic()
        return sum(1 for t in self._calls.get(provider, []) if now - t < 60)

    def rpd(self, provider: str) -> int:
        now = time.monotonic()
        return sum(1 for t in self._calls.get(provider, []) if now - t < 86_400)

    def within_limits(self, provider: str) -> bool:
        limits = PROVIDER_LIMITS.get(provider, {"rpm": 10, "rpd": 1_000})
        return self.rpm(provider) < limits["rpm"] and self.rpd(provider) < limits["rpd"]


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

class LLMGateway:
    """Routes LLM requests to the best available backend with fallback, retries, and circuit breaker."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.routing: Dict[str, Any] = cfg["llm"]["routing"]
        self.providers: Dict[str, Any] = cfg["llm"]["providers"]
        self.default_max_tokens: int = int(cfg["llm"].get("max_tokens", 1024))
        self.default_temperature: float = float(cfg["llm"].get("temperature", 0.7))
        self.sensitive_mode: bool = cfg["simulation"].get("sensitive_mode", False)
        self.retry_attempts: int = int(cfg["llm"].get("retry_attempts", 2))
        self.retry_delay_s: float = float(cfg["llm"].get("retry_delay_s", 3.0))
        self.request_timeout: int = int(cfg["llm"].get("request_timeout", DEFAULT_LLM_TIMEOUT))
        # Per-task timeout overrides (fall back to global request_timeout)
        self._task_timeouts: Dict[str, int] = {
            k: int(v)
            for k, v in cfg["llm"].get("task_timeouts", {}).items()
        }
        self._task_max_tokens: Dict[str, int] = {
            k: int(v)
            for k, v in cfg["llm"].get("task_max_tokens", {}).items()
        }
        self._tracker = RateLimitTracker()
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.last_used_backend: str = ""

        # Cost DB path.
        cost_db_raw = cfg["llm"].get("cost_db_path", "~/.psie/psie_usage.db")
        self._cost_db = str(Path(cost_db_raw).expanduser())
        self._init_cost_db()

        # Warn about missing cloud API keys.
        self._check_api_keys()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        task_type: str,
        messages: List[Dict[str, str]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        json_mode: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Send a completion request for the given *task_type*.

        Returns the assistant message content as a string.
        Raises ``LLMError`` when every backend in the fallback chain
        has been exhausted (including retries and circuit breakers).
        """
        route = self.routing.get(task_type) or self.routing.get("agent_action", {})
        chain: List[str] = [route["preferred"]] + list(route.get("fallback", []))

        if self.sensitive_mode:
            chain = [b for b in chain if b.startswith("ollama/")]
            if not chain:
                chain = ["ollama/gemma3:4b"]

        # Per-task max_tokens override: config task_max_tokens > caller kwarg > global default
        task_mt = self._task_max_tokens.get(task_type)
        eff_max_tokens  = (
            task_mt if (task_mt is not None and max_tokens is None)
            else (max_tokens if max_tokens is not None else self.default_max_tokens)
        )
        eff_temperature = temperature if temperature is not None else self.default_temperature

        last_err: Optional[Exception] = None
        skipped_backends = []

        for backend in chain:
            provider = backend.split("/")[0] if "/" in backend else backend

            # Circuit breaker check.
            cb = self._circuit_breakers.get(provider)
            if cb and not cb.can_try():
                logger.debug("Circuit breaker open for %s — skipping", provider)
                skipped_backends.append(f"{backend} (circuit breaker open)")
                continue

            if not self._tracker.within_limits(provider):
                logger.debug("Rate limit reached for %s — skipping %s", provider, backend)
                skipped_backends.append(f"{backend} (rate limit)")
                continue

            for attempt in range(self.retry_attempts + 1):
                try:
                    eff_timeout = self._task_timeouts.get(task_type, self.request_timeout)
                    _backend_fn = lambda: self._call_backend(
                        backend, messages,
                        max_tokens=eff_max_tokens,
                        temperature=eff_temperature,
                        json_mode=json_mode,
                        timeout=eff_timeout,
                    )
                    if progress_callback:
                        result, actual_tokens = self._call_with_heartbeat(
                            _backend_fn, cb=progress_callback
                        )
                    else:
                        result, actual_tokens = _backend_fn()
                    self._tracker.record(provider)
                    self.last_used_backend = backend
                    self._log_usage(task_type, backend, result, actual_tokens)
                    # Success: record success for circuit breaker.
                    if cb:
                        cb.record_success()
                    return result

                except Exception as exc:
                    last_err = exc
                    hint = _PROVIDER_HINTS.get(provider, "")
                    err_str = str(exc)

                    # Determine if this is a permanent failure (auth, model not found, etc.)
                    permanent_fragments = (
                        "AuthenticationError",
                        "invalid_api_key",
                        "Invalid API key",
                        "PermissionDeniedError",
                        "NotFoundError",
                        "model not found",
                        "model_not_found",
                    )
                    if any(frag in err_str for frag in permanent_fragments):
                        logger.warning(
                            "Permanent error on %s: %s  [%s]", backend, exc, hint
                        )
                        # Record failure for circuit breaker.
                        if provider not in self._circuit_breakers:
                            self._circuit_breakers[provider] = CircuitBreaker()
                        self._circuit_breakers[provider].record_failure()
                        break  # Move to next backend in chain.

                    if attempt < self.retry_attempts:
                        delay = self.retry_delay_s * (2 ** attempt)
                        logger.warning(
                            "Backend %s failed (attempt %d/%d): %s — retrying in %.1f s",
                            backend, attempt + 1, self.retry_attempts + 1, exc, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.warning(
                            "Backend %s exhausted after %d attempt(s): %s  [%s]",
                            backend, attempt + 1, exc, hint,
                        )
                        # Record failure for circuit breaker after exhausting retries.
                        if provider not in self._circuit_breakers:
                            self._circuit_breakers[provider] = CircuitBreaker()
                        self._circuit_breakers[provider].record_failure()

        # Build a helpful error message.
        if self.sensitive_mode:
            hint = (
                "Sensitive mode is ACTIVE — only local Ollama backends are used.\n"
                "  • ollama serve\n"
                "  • ollama pull gemma3:4b\n"
                "Disable sensitive mode or ensure Ollama is running."
            )
        else:
            hint = (
                "Troubleshooting:\n"
                "  • ollama serve          — start local inference\n"
                "  • ollama pull gemma3:4b — pull default model\n"
                "  • psie init             — verify API keys in ~/.psie/config.yaml"
            )

        if skipped_backends:
            hint += f"\n\nSkipped backends: {', '.join(skipped_backends)}"

        raise LLMError(
            f"All backends exhausted for task_type='{task_type}'.\n"
            f"Last error: {last_err}\n{hint}"
        )

    def get_cost_summary(self) -> List[Dict[str, Any]]:
        """Return recent usage‑log rows for the ``psie cost`` command."""
        with db_connect(self._cost_db) as conn:
            rows = conn.execute(
                "SELECT task_type, backend, tokens, ts FROM usage ORDER BY ts DESC LIMIT 100"
            ).fetchall()
        return [{"task": r[0], "backend": r[1], "tokens": r[2], "at": r[3]} for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_with_heartbeat(
        self,
        fn: Callable[[], Tuple[str, int]],
        cb: Optional[Callable[[str], None]] = None,
        interval: float = 5.0,
    ) -> Tuple[str, int]:
        """Run fn() in a background thread, emitting a heartbeat tick via cb
        every *interval* seconds so the user knows the process is alive.
        Exceptions from fn() are re-raised on the calling thread.

        FIX: added a hard total-timeout ceiling equal to self.request_timeout * 3
        to prevent infinite loops if litellm's internal timeout fails.
        """
        hard_limit: float = float(self.request_timeout) * 3
        elapsed: float = 0.0
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(fn)
            while True:
                try:
                    return future.result(timeout=interval)
                except concurrent.futures.TimeoutError:
                    elapsed += interval
                    if cb:
                        cb("  ⏳ waiting for LLM response…")
                    if elapsed >= hard_limit:
                        future.cancel()
                        raise LLMError(
                            f"LLM call exceeded hard timeout ceiling of {hard_limit:.0f}s — "
                            "aborting.  Check backend availability or reduce request_timeout."
                        )

    def _call_backend(
        self,
        backend: str,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        timeout: Optional[int] = None,
    ) -> Tuple[str, int]:
        eff_timeout = timeout if timeout is not None else self.request_timeout
        # Only fallback to raw HTTP if litellm is not installed AND it's ollama
        if "litellm" not in sys.modules:
             # This is a very unlikely case if installed via pip, but kept for robustness
             return self._call_http_fallback(
                backend, messages,
                max_tokens=max_tokens, temperature=temperature,
                json_mode=json_mode,
            )
        
        return self._call_litellm(
            backend, messages,
            max_tokens=max_tokens, temperature=temperature,
            json_mode=json_mode, timeout=eff_timeout,
        )

    def _call_litellm(
        self,
        backend: str,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        timeout: Optional[int] = None,
    ) -> Tuple[str, int]:
        provider, _ = backend.split("/", 1) if "/" in backend else ("ollama", backend)
        provider_cfg = self.providers.get(provider, {})
        eff_timeout = timeout if timeout is not None else self.request_timeout

        kwargs: Dict[str, Any] = {
            "model": backend,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": eff_timeout,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if provider == "ollama":
            base_url = provider_cfg.get("base_url", "http://localhost:11434")
            kwargs["api_base"] = base_url
            kwargs["api_key"] = "ollama"
            kwargs["suppress_debug_info"] = True

        elif provider in ("groq", "gemini", "openrouter"):
            api_key = provider_cfg.get("api_key", "")
            if not api_key:
                raise LLMError(
                    f"{provider.upper()} API key not set. "
                    f"{_PROVIDER_HINTS.get(provider, '')}"
                )
            kwargs["api_key"] = api_key
            kwargs["suppress_debug_info"] = True  # match ollama path
            if "base_url" in provider_cfg:
                kwargs["api_base"] = provider_cfg["base_url"]

        try:
            response = litellm.completion(**kwargs)
        except Exception as e:
            raise LLMError(f"LiteLLM call failed: {e}") from e

        content = response.choices[0].message.content or ""
        # Read actual token count from the response — more accurate than char/4
        actual_tokens = (
            response.usage.total_tokens
            if (response.usage and response.usage.total_tokens)
            else max(1, len(content) // 4)
        )
        return content, actual_tokens

    def _call_http_fallback(
        self,
        backend: str,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> Tuple[str, int]:
        """
        Direct HTTP call for Ollama (OpenAI‑compatible) when litellm is absent.
        """
        import urllib.request

        provider, model = backend.split("/", 1) if "/" in backend else ("ollama", backend)

        if provider != "ollama":
            raise LLMError(
                f"Cannot call provider '{provider}' without litellm.\n"
                "  pip install litellm"
            )

        base_url = self.providers.get("ollama", {}).get("base_url", "http://localhost:11434")
        url = f"{base_url}/v1/chat/completions"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        encoded = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer ollama",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise LLMError(f"HTTP fallback failed: {e}") from e

        content = data["choices"][0]["message"]["content"] or ""
        # HTTP fallback has no usage metadata — use estimate
        est_tokens = max(1, len(content) // 4)
        return content, est_tokens


    def _check_api_keys(self) -> None:
        """Log at DEBUG level for cloud providers with no API key configured.

        If no cloud providers are configured, we assume the user intends to use
        Ollama only.
        """
        missing = []
        for provider in ("groq", "gemini", "openrouter"):
            key = self.providers.get(provider, {}).get("api_key", "")
            if not key:
                logger.debug(
                    "Provider '%s' has no API key — it will be skipped. %s",
                    provider,
                    _PROVIDER_HINTS.get(provider, ""),
                )
                missing.append(provider)

        if len(missing) == 3:
            logger.debug(
                "No cloud API keys found. Ensure you have a .env file or "
                "export variables if you intend to use cloud providers."
            )

    def _init_cost_db(self) -> None:
        Path(self._cost_db).parent.mkdir(parents=True, exist_ok=True)
        with db_connect(self._cost_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT,
                    backend   TEXT,
                    tokens    INTEGER,
                    ts        TEXT
                )
            """)

    def _log_usage(
        self, task_type: str, backend: str, content: str,
        actual_tokens: int = 0
    ) -> None:
        tokens = actual_tokens if actual_tokens > 0 else max(1, len(content) // 4)
        ts = datetime.now(timezone.utc).isoformat()
        with db_connect(self._cost_db) as conn:
            conn.execute(
                "INSERT INTO usage (task_type, backend, tokens, ts) VALUES (?,?,?,?)",
                (task_type, backend, tokens, ts),
            )
