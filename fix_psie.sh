#!/bin/sh
# fix_eidolon-vault.sh — Drop this in ~/EIDOLON_VAULT_v-1.4/ and run: sh fix_eidolon-vault.sh
set -e
cd "$(dirname "$0")"

echo "==> Writing eidolon-vault/config.py ..."
cat > eidolon-vault/config.py << 'EOF'
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
from .constants import DEFAULT_LLM_TIMEOUT
from .exceptions import ConfigurationError

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".eidolon-vault" / "config.yaml"
LOCAL_CONFIG_PATH   = Path("config.yaml")


def _default_config_path() -> Path:
    """Resolved fresh on every call so tests can patch Path.home()."""
    return Path.home() / ".eidolon-vault" / "config.yaml"


DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "routing": {
            "graph_build":      {"preferred": "groq/llama-3.3-70b-versatile",  "fallback": ["gemini/gemini-2.5-flash", "ollama/qwen2.5:7b"]},
            "persona_generate": {"preferred": "groq/llama-3.3-70b-versatile",  "fallback": ["gemini/gemini-2.5-flash", "ollama/qwen2.5:7b"]},
            "agent_action":     {"preferred": "ollama/gemma3:4b",               "fallback": ["groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"]},
            "report_generate":  {"preferred": "gemini/gemini-2.5-flash",        "fallback": ["groq/llama-3.3-70b-versatile", "ollama/qwen2.5:7b"]},
            "skill_extract":    {"preferred": "groq/llama-3.3-70b-versatile",   "fallback": ["gemini/gemini-2.5-flash", "ollama/gemma3:4b"]},
            "fact_extract":     {"preferred": "groq/llama-3.3-70b-versatile",   "fallback": ["gemini/gemini-2.5-flash", "ollama/gemma3:4b"]},
        },
        "providers": {
            "ollama":     {"base_url": "http://localhost:11434", "api_key": "ollama"},
            "groq":       {"api_key": "${GROQ_API_KEY}"},
            "gemini":     {"api_key": "${GEMINI_API_KEY}"},
            "openrouter": {"api_key": "${OPENROUTER_API_KEY}", "base_url": "https://openrouter.ai/api/v1"},
        },
        "max_tokens": 1024,
        "temperature": 0.7,
        "cost_db_path": "~/.eidolon-vault/eidolon-vault_usage.db",
        "retry_attempts": 2,
        "retry_delay_s": 3.0,
        "request_timeout": DEFAULT_LLM_TIMEOUT,
    },
    "simulation": {
        "max_agents": 12,
        "max_turns": 15,
        "sensitive_mode": False,
        "persona_anchor_interval": 3,
    },
    "graph":  {"backend": "custom", "storage_dir": "~/.eidolon-vault/graphs", "max_entities": 20},
    "memory": {"db_path": "~/.eidolon-vault/eidolon-vault_memory.db", "max_episodic_per_run": 50,
               "max_semantic_inject": 5, "max_total_episodes": 5000},
    "skills": {"db_path": "~/.eidolon-vault/eidolon-vault_skills.db", "top_k_inject": 3},
    "output": {"reports_dir": "~/eidolon-vault_reports"},
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


def _bool(v: str) -> bool:
    return v.lower() in ("1", "true", "yes")


_ENV_OVERRIDES: Dict[str, tuple] = {
    "EIDOLON_VAULT_LLM_TIMEOUT":    ("llm",        "request_timeout", int),
    "EIDOLON_VAULT_RETRY_ATTEMPTS": ("llm",        "retry_attempts",  int),
    "EIDOLON_VAULT_RETRY_DELAY":    ("llm",        "retry_delay_s",   float),
    "EIDOLON_VAULT_MAX_AGENTS":     ("simulation", "max_agents",      int),
    "EIDOLON_VAULT_MAX_TURNS":      ("simulation", "max_turns",       int),
    "EIDOLON_VAULT_SENSITIVE":      ("simulation", "sensitive_mode",  _bool),
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
      2. ~/.eidolon-vault/config.yaml  (resolved at call time via _default_config_path)
      3. ./config.yaml
      4. explicit config_path argument
      5. EIDOLON_VAULT_* environment variables
    """
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
        str(Path.home() / ".eidolon-vault"),
    ]
    for raw in paths:
        Path(os.path.expanduser(raw)).mkdir(parents=True, exist_ok=True)
EOF

echo "==> Writing tests/conftest.py ..."
cat > tests/conftest.py << 'EOF'
"""Eidolon Vault shared test fixtures."""
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from eidolon-vault.config import reset_config


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Give every test a hermetic home with no .eidolon-vault/config.yaml.

    Patches applied before each test (all restored after automatically):
      - Path.home()  -> tmp_path/fake_home  (empty directory, no .eidolon-vault subdir)
      - HOME env var -> same fake dir so os.path.expanduser agrees
      - CWD          -> tmp_path so ./config.yaml does not exist by default
      - All EIDOLON_VAULT_*   -> removed from the environment
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    for key in [k for k in os.environ if k.startswith("EIDOLON_VAULT_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    yield fake_home


@pytest.fixture(autouse=True)
def clean_config():
    """Reset the config singleton before and after every test."""
    reset_config()
    yield
    reset_config()


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.complete.return_value = ('{"entities": [], "relations": []}', 10)
    gw.last_used_backend = "ollama/test"
    return gw


@pytest.fixture
def minimal_cfg(tmp_path):
    """Minimal valid config — all paths inside tmp_path, no real disk writes."""
    return {
        "llm": {
            "routing": {k: {"preferred": "ollama/test", "fallback": []}
                        for k in ("graph_build", "persona_generate", "agent_action",
                                  "report_generate", "skill_extract", "fact_extract")},
            "providers": {"ollama": {"base_url": "http://localhost:11434", "api_key": "ollama"}},
            "max_tokens": 512,
            "temperature": 0.7,
            "cost_db_path": str(tmp_path / "usage.db"),
            "retry_attempts": 0,
            "retry_delay_s": 0.0,
            "request_timeout": 10,
            "task_timeouts": {},
            "task_max_tokens": {},
        },
        "simulation": {"max_agents": 4, "max_turns": 4,
                       "sensitive_mode": False, "persona_anchor_interval": 2},
        "graph":  {"backend": "custom", "storage_dir": str(tmp_path / "graphs"), "max_entities": 5},
        "memory": {"db_path": str(tmp_path / "memory.db"), "max_episodic_per_run": 10,
                   "max_semantic_inject": 2, "max_total_episodes": 100},
        "skills": {"db_path": str(tmp_path / "skills.db"), "top_k_inject": 2},
        "output": {"reports_dir": str(tmp_path / "reports")},
        "input":  {"max_file_bytes": 1048576, "url_timeout_s": 5, "allow_private_ip_url": False},
    }
EOF

echo "==> Writing tests/test_config.py ..."
cat > tests/test_config.py << 'EOF'
"""
Tests for eidolon-vault/config.py

Root cause of the three recurring failures (now fixed)
------------------------------------------------------
DEFAULT_CONFIG_PATH was evaluated at module-import time, freezing the real
Path.home() before any test fixture could patch it.  load_config() now calls
_default_config_path() at call time, and conftest.py patches Path.home() via
the autouse isolated_home fixture so every test gets a hermetic empty home
with no ~/.eidolon-vault/config.yaml to bleed in.
"""
import copy
import logging
import os
import pytest
from pathlib import Path

from eidolon-vault.config import (
    load_config, get_config, reset_config,
    _deep_merge, _expand_env, validate_config,
    DEFAULT_CONFIG,
)
from eidolon-vault.exceptions import ConfigurationError


class TestDeepMerge:
    def test_nested_override(self):
        base   = {"llm": {"timeout": 60, "retries": 2}, "sim": {"agents": 4}}
        result = _deep_merge(base, {"llm": {"timeout": 300}})
        assert result["llm"]["timeout"] == 300
        assert result["llm"]["retries"] == 2    # preserved — original bug
        assert result["sim"]["agents"]  == 4    # untouched section

    def test_top_level_key_added(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_does_not_mutate_base(self):
        base = {"llm": {"timeout": 60}}
        _deep_merge(base, {"llm": {"timeout": 300}})
        assert base["llm"]["timeout"] == 60

    def test_list_replaced_not_merged(self):
        assert _deep_merge({"x": [1, 2]}, {"x": [3]})["x"] == [3]


class TestExpandEnv:
    def test_set_var_expands(self, monkeypatch):
        monkeypatch.setenv("EIDOLON_VAULT_TEST_KEY", "hello")
        assert _expand_env("${EIDOLON_VAULT_TEST_KEY}") == "hello"

    def test_unset_var_returns_empty(self, monkeypatch):
        monkeypatch.delenv("EIDOLON_VAULT_MISSING", raising=False)
        assert _expand_env("${EIDOLON_VAULT_MISSING}") == ""

    def test_tilde_expanded(self):
        result = _expand_env("~/eidolon-vault_reports")
        assert result.startswith("/") and "~" not in result

    def test_non_string_passthrough(self):
        assert _expand_env(42) == 42
        assert _expand_env(True) is True
        assert _expand_env([1, 2]) == [1, 2]

    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret")
        assert _expand_env({"p": {"k": "${MY_KEY}"}})["p"]["k"] == "secret"


class TestLoadConfigLayers:
    """All three regression tests pass because isolated_home (autouse) gives
    every test a fake home with no .eidolon-vault/config.yaml present."""

    def test_explicit_path_wins(self, tmp_path):
        (tmp_path / "custom.yaml").write_text("llm:\n  request_timeout: 999\n")
        assert load_config(str(tmp_path / "custom.yaml"))["llm"]["request_timeout"] == 999

    def test_local_config_overrides_defaults(self, tmp_path):
        (tmp_path / "config.yaml").write_text("llm:\n  request_timeout: 777\n")
        assert load_config()["llm"]["request_timeout"] == 777

    def test_explicit_overrides_local(self, tmp_path):
        (tmp_path / "config.yaml").write_text("llm:\n  request_timeout: 777\n")
        (tmp_path / "custom.yaml").write_text("llm:\n  request_timeout: 999\n")
        assert load_config(str(tmp_path / "custom.yaml"))["llm"]["request_timeout"] == 999

    def test_layers_merged_not_replaced(self, tmp_path):
        """Partial override must NOT wipe sibling keys — regression test."""
        (tmp_path / "config.yaml").write_text("simulation:\n  max_turns: 99\n")
        cfg = load_config()
        assert cfg["simulation"]["max_turns"]  == 99
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]

    def test_bad_yaml_skipped_gracefully(self, tmp_path):
        """Malformed YAML is skipped; defaults survive — regression test."""
        (tmp_path / "config.yaml").write_text("llm: [this: is: {bad yaml\n")
        cfg = load_config()
        assert cfg["llm"]["request_timeout"] == DEFAULT_CONFIG["llm"]["request_timeout"]

    def test_empty_home_yields_defaults(self):
        """With no ~/.eidolon-vault/config.yaml (empty fake home), only defaults load."""
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]
        assert cfg["llm"]["request_timeout"]   == DEFAULT_CONFIG["llm"]["request_timeout"]

    def test_both_layers_loaded_in_order(self, tmp_path):
        """Both ~/.eidolon-vault/config.yaml AND ./config.yaml are merged (no early break)."""
        fake_home = tmp_path / "fake_home"
        eidolon-vault_dir  = fake_home / ".eidolon-vault"
        eidolon-vault_dir.mkdir(parents=True, exist_ok=True)
        (eidolon-vault_dir / "config.yaml").write_text("simulation:\n  max_agents: 5\n")
        (tmp_path / "config.yaml").write_text("simulation:\n  max_turns: 7\n")
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == 5
        assert cfg["simulation"]["max_turns"]  == 7


class TestEnvOverrides:
    def test_timeout(self, monkeypatch):
        monkeypatch.setenv("EIDOLON_VAULT_LLM_TIMEOUT", "300")
        assert load_config()["llm"]["request_timeout"] == 300

    def test_max_agents(self, monkeypatch):
        monkeypatch.setenv("EIDOLON_VAULT_MAX_AGENTS", "3")
        assert load_config()["simulation"]["max_agents"] == 3

    def test_sensitive_true(self, monkeypatch):
        monkeypatch.setenv("EIDOLON_VAULT_SENSITIVE", "true")
        assert load_config()["simulation"]["sensitive_mode"] is True

    def test_sensitive_false(self, monkeypatch):
        monkeypatch.setenv("EIDOLON_VAULT_SENSITIVE", "0")
        assert load_config()["simulation"]["sensitive_mode"] is False

    def test_env_beats_yaml(self, tmp_path, monkeypatch):
        (tmp_path / "config.yaml").write_text("llm:\n  request_timeout: 60\n")
        monkeypatch.setenv("EIDOLON_VAULT_LLM_TIMEOUT", "300")
        assert load_config()["llm"]["request_timeout"] == 300

    def test_invalid_env_var_ignored(self, monkeypatch, caplog):
        """Bad cast logs warning and falls back to default — regression test."""
        monkeypatch.setenv("EIDOLON_VAULT_MAX_AGENTS", "not_a_number")
        with caplog.at_level(logging.WARNING, logger="eidolon-vault.config"):
            cfg = load_config()
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]
        assert any("EIDOLON_VAULT_MAX_AGENTS" in r.message for r in caplog.records)

    def test_no_eidolon-vault_vars_yields_defaults(self):
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]
        assert cfg["llm"]["request_timeout"]   == DEFAULT_CONFIG["llm"]["request_timeout"]


class TestValidateConfig:
    def test_valid_passes(self):
        validate_config(copy.deepcopy(DEFAULT_CONFIG))

    def test_bad_max_agents_zero(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["simulation"]["max_agents"] = 0
        with pytest.raises(ConfigurationError, match="max_agents"):
            validate_config(cfg)

    def test_bad_max_agents_high(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["simulation"]["max_agents"] = 51
        with pytest.raises(ConfigurationError, match="max_agents"):
            validate_config(cfg)

    def test_bad_temperature(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["llm"]["temperature"] = 3.5
        with pytest.raises(ConfigurationError, match="temperature"):
            validate_config(cfg)

    def test_bad_timeout(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["llm"]["request_timeout"] = -1
        with pytest.raises(ConfigurationError, match="request_timeout"):
            validate_config(cfg)

    def test_bad_max_turns(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["simulation"]["max_turns"] = 0
        with pytest.raises(ConfigurationError, match="max_turns"):
            validate_config(cfg)

    def test_bad_episodic(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["memory"]["max_episodic_per_run"] = 0
        with pytest.raises(ConfigurationError, match="max_episodic_per_run"):
            validate_config(cfg)


class TestSingleton:
    def test_same_object_on_repeat(self):
        assert get_config() is get_config()

    def test_explicit_path_reloads(self, tmp_path):
        get_config()
        (tmp_path / "c.yaml").write_text("llm:\n  request_timeout: 555\n")
        assert get_config(str(tmp_path / "c.yaml"))["llm"]["request_timeout"] == 555

    def test_reset_clears(self):
        get_config()
        reset_config()
        cfg = get_config()
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]
EOF

echo ""
echo "All three files written successfully."
echo "Run:  python3 -m pytest tests/ -v"
