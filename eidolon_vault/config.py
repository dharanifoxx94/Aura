"""
Eidolon Vault — Configuration Loader
============================
Thread-safe singleton with validation and directory creation.

FIX: _default_config_path() resolves Path.home() at call time (not import
time) so test fixtures that patch Path.home() are respected by load_config().
"""
from __future__ import annotations
import copy, logging, os, threading
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from .constants import DEFAULT_LLM_TIMEOUT
from .exceptions import ConfigurationError

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".eidolon_vault" / "config.yaml"
LOCAL_CONFIG_PATH   = Path("config.yaml")


def _default_config_path() -> Path:
    """Resolved fresh on every call so tests can patch Path.home()."""
    return Path.home() / ".eidolon_vault" / "config.yaml"


DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "provider": "ollama",
        "model": "llama3.2:3b",
        "routing": {
            "graph_build":      {"preferred": "groq/llama-3.3-70b-versatile",  "fallback": ["gemini/gemini-2.5-flash", "ollama/gemma3:4b"]},
            "persona_generate": {"preferred": "groq/llama-3.3-70b-versatile",  "fallback": ["gemini/gemini-2.5-flash", "ollama/gemma3:4b"]},
            "agent_action":     {"preferred": "ollama/gemma3:4b",               "fallback": ["groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"]},
            "report_generate":  {"preferred": "gemini/gemini-2.5-flash",        "fallback": ["groq/llama-3.3-70b-versatile", "ollama/gemma3:4b"]},
            "skill_extract":    {"preferred": "groq/llama-3.3-70b-versatile",   "fallback": ["gemini/gemini-2.5-flash", "ollama/gemma3:4b"]},
            "fact_extract":     {"preferred": "groq/llama-3.3-70b-versatile",   "fallback": ["gemini/gemini-2.5-flash", "ollama/gemma3:4b"]},
            "summarise":        {"preferred": "gemini/gemini-2.5-flash",        "fallback": ["groq/llama-3.3-70b-versatile", "ollama/gemma3:4b"]},
        },
        "providers": {
            "ollama":     {"base_url": "http://localhost:11434", "api_key": "ollama"},
            "groq":       {"api_key": "${GROQ_API_KEY}"},
            "gemini":     {"api_key": "${GEMINI_API_KEY}"},
            "openrouter": {"api_key": "${OPENROUTER_API_KEY}", "base_url": "https://openrouter.ai/api/v1"},
        },
        "max_tokens": 1024,
        "temperature": 0.7,
        "cost_db_path": "~/.eidolon_vault/eidolon_vault_usage.db",
        "retry_attempts": 2,
        "retry_delay_s": 3.0,
        "request_timeout": DEFAULT_LLM_TIMEOUT,
        # Per-task token limits — override global max_tokens per task type.
        # report_generate needs 4096 for Gemini 2.5 Flash thinking overhead.
        "task_max_tokens": {
            "graph_build":      1024,
            "persona_generate": 1024,
            "agent_action":      512,
            "report_generate":  4096,
            "skill_extract":    1024,
            "fact_extract":     1024,
            "summarise":       512,
            "consolidate":     512,
        },
        # Per-task timeout overrides in seconds (inherits request_timeout if absent).
        "task_timeouts": {
            "agent_action":    120,
            "fact_extract":    180,
            "report_generate": 180,
            "summarise":       60,
            "consolidate":     120,
        },
    },
    "simulation": {
        "max_agents": 12,
        "max_turns": 15,
        "sensitive_mode": False,
        "persona_anchor_interval": 3,
        "max_injected_items": 6,  # Configurable injection limit
    },
    "graph":  {"backend": "custom", "storage_dir": "~/.eidolon_vault/graphs", "max_entities": 20},
    "memory": {"db_path": "~/.eidolon_vault/eidolon_vault_memory.db", "max_episodic_per_run": 50,
               "max_semantic_inject": 5, "max_total_episodes": 5000},
    "skills": {"db_path": "~/.eidolon_vault/eidolon_vault_skills.db", "top_k_inject": 3},
    "output": {"reports_dir": "~/eidolon_vault_reports"},
    "input":  {"max_file_bytes": 20971520, "url_timeout_s": 20, "allow_private_ip_url": False},
}


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        expanded = os.path.expandvars(value)
        if expanded == value and expanded.startswith("${") and expanded.endswith("}"):
            logger.warning(
                "Config placeholder ${%s} is not set in the environment — "
                "provider will be skipped. Set the env var or edit config.yaml.",
                value[2:-1],
            )
            return ""
        return os.path.expanduser(expanded)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def validate_config(cfg: Dict[str, Any]) -> None:
    sim = cfg.get("simulation", {})
    max_agents = sim.get("max_agents", 12)
    if not (1 <= int(max_agents) <= 50):
        raise ConfigurationError(f"simulation.max_agents must be 1-50, got {max_agents!r}")
    max_turns = sim.get("max_turns", 15)
    if not (1 <= int(max_turns) <= 200):
        raise ConfigurationError(f"simulation.max_turns must be 1-200, got {max_turns!r}")
    llm = cfg.get("llm", {})
    temp = float(llm.get("temperature", 0.7))
    if not (0.0 <= temp <= 2.0):
        raise ConfigurationError(f"llm.temperature must be 0.0-2.0, got {temp!r}")
    max_tok = int(llm.get("max_tokens", 1024))
    if max_tok <= 0:
        raise ConfigurationError(f"llm.max_tokens must be positive, got {max_tok!r}")
    timeout = int(llm.get("request_timeout", DEFAULT_LLM_TIMEOUT))
    if timeout <= 0:
        raise ConfigurationError(f"llm.request_timeout must be positive, got {timeout!r}")
    mep = int(cfg.get("memory", {}).get("max_episodic_per_run", 50))
    if mep <= 0:
        raise ConfigurationError(f"memory.max_episodic_per_run must be positive, got {mep!r}")
    
    mii = int(sim.get("max_injected_items", 6))
    if mii < 0:
         raise ConfigurationError(f"simulation.max_injected_items must be non-negative, got {mii!r}")


def _bool(v: str) -> bool:
    return v.lower() in ("1", "true", "yes")


_ENV_OVERRIDES: Dict[str, tuple] = {
    "EIDOLON_VAULT_LLM_TIMEOUT":    ("llm",        "request_timeout", int),
    "EIDOLON_VAULT_RETRY_ATTEMPTS": ("llm",        "retry_attempts",  int),
    "EIDOLON_VAULT_RETRY_DELAY":    ("llm",        "retry_delay_s",   float),
    "EIDOLON_VAULT_MAX_AGENTS":     ("simulation", "max_agents",      int),
    "EIDOLON_VAULT_MAX_TURNS":      ("simulation", "max_turns",       int),
    "EIDOLON_VAULT_SENSITIVE":      ("simulation", "sensitive_mode",  _bool),
    "EIDOLON_VAULT_MAX_INJECTED":   ("simulation", "max_injected_items", int),
}


def _apply_env_overrides(cfg: Dict[str, Any]) -> None:
    for var, (section, key, cast) in _ENV_OVERRIDES.items():
        val = os.environ.get(var)
        if val is not None:
            try:
                cfg.setdefault(section, {})[key] = cast(val)
                logger.info("Config override via %s=%s", var, val)
            except (ValueError, TypeError) as e:
                logger.warning("Ignoring invalid env var %s=%r: %s", var, val, e)


def load_config(config_path=None) -> Dict[str, Any]:
    """Load, merge, expand, and validate the configuration.

    Priority (lowest to highest):
      1. DEFAULT_CONFIG
      2. .env (via load_dotenv)
      3. ~/.eidolon_vault/config.yaml  (resolved at call time via _default_config_path)
      4. ./config.yaml
      5. explicit config_path argument
      6. EIDOLON_VAULT_* environment variables
    """
    if load_dotenv:
        # Load .env from current directory or recurse up
        load_dotenv()
        
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    search_paths: List[Path] = [_default_config_path(), LOCAL_CONFIG_PATH]
    if config_path:
        search_paths.append(Path(config_path))
    for p in search_paths:
        expanded_p = Path(os.path.expanduser(str(p)))
        if expanded_p.exists():
            try:
                with open(expanded_p, encoding="utf-8") as fh:
                    user_cfg = yaml.safe_load(fh) or {}
                cfg = _deep_merge(cfg, user_cfg)
                logger.debug("Loaded config layer: %s", expanded_p)
            except yaml.YAMLError as exc:
                logger.warning("Failed to parse config %s: %s — skipping layer", expanded_p, exc)
    _apply_env_overrides(cfg)
    cfg = _expand_env(cfg)
    validate_config(cfg)
    return cfg


_CONFIG: Dict[str, Any] | None = None
_CONFIG_LOCK = threading.Lock()
_SENTINEL = object()
_CONFIG_PATH: object = _SENTINEL


def get_config(config_path=None) -> Dict[str, Any]:
    global _CONFIG, _CONFIG_PATH
    with _CONFIG_LOCK:
        if _CONFIG is None or (config_path is not None and config_path != _CONFIG_PATH):
            _CONFIG = load_config(config_path)
            _CONFIG_PATH = config_path
        return _CONFIG


def reset_config() -> None:
    global _CONFIG, _CONFIG_PATH
    with _CONFIG_LOCK:
        _CONFIG = None
        _CONFIG_PATH = _SENTINEL


def ensure_dirs(cfg: Dict[str, Any]) -> None:
    paths = [
        cfg["graph"]["storage_dir"],
        cfg["output"]["reports_dir"],
        str(Path(cfg["memory"]["db_path"]).parent),
        str(Path(cfg["skills"]["db_path"]).parent),
        str(Path(cfg["llm"]["cost_db_path"]).parent),
        str(Path.home() / ".eidolon_vault"),
    ]
    for raw in paths:
        Path(os.path.expanduser(raw)).mkdir(parents=True, exist_ok=True)
