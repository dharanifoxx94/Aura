"""
Tests for psie/config.py
========================

Root cause of the three recurring failures (now fixed)
-------------------------------------------------------
DEFAULT_CONFIG_PATH = Path.home() / ".psie" / "config.yaml"  was evaluated
ONCE at module-import time and baked in the developer's real home directory.
So even though the tests changed CWD and stripped PSIE_* env vars, the real
~/.psie/config.yaml (which has max_agents=8, request_timeout=120) was always
loaded, overriding DEFAULT_CONFIG values.

The fix:
  • psie/config.py now calls _default_config_path() (a function) inside
    load_config() so Path.home() is consulted at call time, not import time.
  • tests/conftest.py patches Path.home() → a fresh empty tmp dir and also
    cleans PSIE_* env vars before every test (autouse fixture).

This file no longer needs the ``monkeypatch.setattr(os, 'environ', ...)``
anti-pattern.  That approach replaced os.environ with a plain dict, which
broke monkeypatch.setenv()'s internal undo logic and could cause subtle
ordering issues.  Use monkeypatch.delenv() and monkeypatch.setenv() instead.
"""
import copy
import logging
import os
import pytest
from pathlib import Path

from psie.config import (
    load_config, get_config, reset_config,
    _deep_merge, _expand_env, validate_config,
    DEFAULT_CONFIG,
)
from psie.exceptions import ConfigurationError


# ── _deep_merge ───────────────────────────────────────────────────────────────

class TestDeepMerge:
    def test_nested_override(self):
        base     = {"llm": {"timeout": 60, "retries": 2}, "sim": {"agents": 4}}
        override = {"llm": {"timeout": 300}}
        result   = _deep_merge(base, override)
        assert result["llm"]["timeout"]  == 300   # overridden
        assert result["llm"]["retries"]  == 2     # preserved — this was the original bug
        assert result["sim"]["agents"]   == 4     # untouched section preserved

    def test_top_level_key_added(self):
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_does_not_mutate_base(self):
        base = {"llm": {"timeout": 60}}
        _deep_merge(base, {"llm": {"timeout": 300}})
        assert base["llm"]["timeout"] == 60   # base must not be mutated

    def test_list_value_replaced_not_merged(self):
        """Lists are replaced wholesale — deep-merge does not concatenate them."""
        base     = {"fallback": ["a", "b"]}
        override = {"fallback": ["c"]}
        result   = _deep_merge(base, override)
        assert result["fallback"] == ["c"]


# ── _expand_env ───────────────────────────────────────────────────────────────

class TestExpandEnv:
    def test_set_var_expands(self, monkeypatch):
        monkeypatch.setenv("PSIE_TEST_KEY", "hello")
        assert _expand_env("${PSIE_TEST_KEY}") == "hello"

    def test_unset_var_returns_empty_string(self, monkeypatch):
        monkeypatch.delenv("PSIE_NONEXISTENT_VAR", raising=False)
        result = _expand_env("${PSIE_NONEXISTENT_VAR}")
        assert result == ""   # silent drop — warning logged but no crash

    def test_tilde_expanded(self):
        result = _expand_env("~/psie_reports")
        assert result.startswith("/")
        assert "~" not in result

    def test_non_string_passthrough(self):
        assert _expand_env(42)     == 42
        assert _expand_env(True)   == True
        assert _expand_env([1, 2]) == [1, 2]

    def test_nested_dict_expanded(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret")
        result = _expand_env({"provider": {"api_key": "${MY_KEY}"}})
        assert result["provider"]["api_key"] == "secret"


# ── load_config: multi-layer merge ────────────────────────────────────────────

class TestLoadConfigLayers:
    """
    The original bug: load_config() had a 'break' after the first match,
    so ~/.psie/config.yaml was silently ignored if ./config.yaml existed.
    After the fix, ALL layers are loaded in priority order.

    A second independent bug: DEFAULT_CONFIG_PATH was baked in at import time,
    so even after removing PSIE_* env vars the developer's real
    ~/.psie/config.yaml was still being read.  The conftest.py isolated_home
    fixture patches Path.home() to a fresh empty directory, preventing this.
    """

    def test_explicit_path_wins_over_default(self, tmp_path, monkeypatch):
        """An explicit config_path argument has the highest YAML priority."""
        explicit = tmp_path / "custom.yaml"
        explicit.write_text("llm:\n  request_timeout: 999\n")
        # CWD is already tmp_path (set by isolated_home), no local config.yaml
        cfg = load_config(str(explicit))
        assert cfg["llm"]["request_timeout"] == 999

    def test_local_config_overrides_defaults(self, tmp_path, monkeypatch):
        """A config.yaml in CWD overrides built-in defaults."""
        local = tmp_path / "config.yaml"
        local.write_text("llm:\n  request_timeout: 777\n")
        # CWD is tmp_path via isolated_home; config.yaml now exists there.
        cfg = load_config()
        assert cfg["llm"]["request_timeout"] == 777

    def test_explicit_overrides_local(self, tmp_path, monkeypatch):
        """Explicit path wins over local config.yaml (highest YAML layer)."""
        local    = tmp_path / "config.yaml"
        explicit = tmp_path / "custom.yaml"
        local.write_text("llm:\n  request_timeout: 777\n")
        explicit.write_text("llm:\n  request_timeout: 999\n")
        cfg = load_config(str(explicit))
        assert cfg["llm"]["request_timeout"] == 999

    def test_layers_merged_not_replaced(self, tmp_path):
        """A partial override must NOT wipe out sibling keys.

        Previously this asserted 8 == 12 because the developer's
        ~/.psie/config.yaml (with max_agents=8) was loaded despite the test
        using a clean env — the path was baked in at import time.
        """
        local = tmp_path / "config.yaml"
        # Only override max_turns — max_agents should still come from DEFAULT_CONFIG
        local.write_text("simulation:\n  max_turns: 99\n")
        # CWD is tmp_path (via isolated_home), so this local config.yaml is loaded
        cfg = load_config()
        assert cfg["simulation"]["max_turns"]  == 99
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]  # 12

    def test_bad_yaml_skipped_gracefully(self, tmp_path):
        """A malformed YAML layer is skipped; defaults are preserved.

        Previously this asserted 120 == 60 because the developer's
        ~/.psie/config.yaml (with request_timeout=120) was always loaded.
        """
        bad = tmp_path / "config.yaml"
        bad.write_text("llm: [this: is: {bad yaml\n")
        # Should not raise — bad layer is skipped, defaults remain
        cfg = load_config()
        assert cfg["llm"]["request_timeout"] == DEFAULT_CONFIG["llm"]["request_timeout"]  # 60

    def test_user_config_not_loaded_when_absent(self):
        """With an empty fake home (via isolated_home), no user YAML is loaded."""
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]
        assert cfg["llm"]["request_timeout"]   == DEFAULT_CONFIG["llm"]["request_timeout"]

    def test_both_layers_loaded_in_order(self, tmp_path):
        """Both ~/.psie/config.yaml and ./config.yaml are merged (no early break).

        This tests the original 'break' bug: the second layer must be applied
        even though the first already matched.
        """
        fake_home = tmp_path / "fake_home"   # already created by isolated_home
        psie_dir  = fake_home / ".psie"
        psie_dir.mkdir(parents=True, exist_ok=True)
        (psie_dir / "config.yaml").write_text(
            "simulation:\n  max_agents: 5\n"    # home layer — lower priority
        )
        (tmp_path / "config.yaml").write_text(
            "simulation:\n  max_turns: 7\n"     # local layer — higher priority
        )
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == 5   # from ~/.psie/config.yaml
        assert cfg["simulation"]["max_turns"]  == 7   # from ./config.yaml
        # Unrelated key from DEFAULT_CONFIG must survive both layers
        assert cfg["llm"]["request_timeout"]   == DEFAULT_CONFIG["llm"]["request_timeout"]


# ── _apply_env_overrides ──────────────────────────────────────────────────────

class TestEnvOverrides:
    """
    These tests use monkeypatch.setenv() / monkeypatch.delenv() rather than
    the anti-pattern of replacing os.environ with a plain dict.
    The isolated_home fixture already removes all PSIE_* vars before each test.
    """

    def test_psie_llm_timeout(self, monkeypatch):
        monkeypatch.setenv("PSIE_LLM_TIMEOUT", "300")
        cfg = load_config()
        assert cfg["llm"]["request_timeout"] == 300

    def test_psie_max_agents(self, monkeypatch):
        monkeypatch.setenv("PSIE_MAX_AGENTS", "3")
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == 3

    def test_psie_sensitive_true(self, monkeypatch):
        monkeypatch.setenv("PSIE_SENSITIVE", "true")
        cfg = load_config()
        assert cfg["simulation"]["sensitive_mode"] is True

    def test_psie_sensitive_false(self, monkeypatch):
        monkeypatch.setenv("PSIE_SENSITIVE", "0")
        cfg = load_config()
        assert cfg["simulation"]["sensitive_mode"] is False

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        """Env var must win over a YAML file that says something different."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("llm:\n  request_timeout: 60\n")
        monkeypatch.setenv("PSIE_LLM_TIMEOUT", "300")
        cfg = load_config()
        assert cfg["llm"]["request_timeout"] == 300   # env wins

    def test_invalid_env_var_ignored(self, monkeypatch, caplog):
        """An invalid PSIE_* value logs a warning and falls back to the default.

        Previously this asserted 8 == 12 because the developer's
        ~/.psie/config.yaml (max_agents=8) was loaded before the env-var cast
        failed, leaving max_agents at 8 rather than the DEFAULT_CONFIG value 12.

        With isolated_home patching Path.home(), no user config is loaded, so
        the fallback really is DEFAULT_CONFIG["simulation"]["max_agents"] == 12.
        """
        monkeypatch.setenv("PSIE_MAX_AGENTS", "not_a_number")
        with caplog.at_level(logging.WARNING, logger="psie.config"):
            cfg = load_config()
        # Bad env var must not crash
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]  # 12
        # A warning must have been logged
        assert any("PSIE_MAX_AGENTS" in r.message for r in caplog.records)

    def test_all_psie_vars_absent_by_default(self):
        """isolated_home strips all PSIE_* vars; plain load_config yields defaults."""
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]
        assert cfg["llm"]["request_timeout"]   == DEFAULT_CONFIG["llm"]["request_timeout"]


# ── validate_config ───────────────────────────────────────────────────────────

class TestValidateConfig:
    def test_valid_config_passes(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        validate_config(cfg)   # must not raise

    def test_bad_max_agents_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["simulation"]["max_agents"] = 0
        with pytest.raises(ConfigurationError, match="max_agents"):
            validate_config(cfg)

    def test_bad_max_agents_too_high_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["simulation"]["max_agents"] = 51
        with pytest.raises(ConfigurationError, match="max_agents"):
            validate_config(cfg)

    def test_bad_temperature_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["llm"]["temperature"] = 3.5
        with pytest.raises(ConfigurationError, match="temperature"):
            validate_config(cfg)

    def test_bad_timeout_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["llm"]["request_timeout"] = -1
        with pytest.raises(ConfigurationError, match="request_timeout"):
            validate_config(cfg)

    def test_bad_max_turns_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["simulation"]["max_turns"] = 0
        with pytest.raises(ConfigurationError, match="max_turns"):
            validate_config(cfg)

    def test_bad_episodic_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["memory"]["max_episodic_per_run"] = 0
        with pytest.raises(ConfigurationError, match="max_episodic_per_run"):
            validate_config(cfg)


# ── get_config singleton ──────────────────────────────────────────────────────

class TestSingleton:
    def test_returns_same_object_on_repeat_call(self):
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2   # same object, not just equal

    def test_explicit_path_reloads(self, tmp_path, monkeypatch):
        cfg1 = get_config()                     # loads defaults (no user config)
        explicit = tmp_path / "custom.yaml"
        explicit.write_text("llm:\n  request_timeout: 555\n")
        cfg2 = get_config(str(explicit))        # explicit path forces reload
        assert cfg2["llm"]["request_timeout"] == 555

    def test_reset_clears_singleton(self):
        get_config()
        reset_config()
        # After reset, next call re-reads from disk (defaults in hermetic test env)
        cfg = get_config()
        assert cfg is not None
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]

    def test_singleton_isolation_between_tests(self):
        """Each test gets a clean singleton thanks to the autouse clean_config fixture."""
        cfg = get_config()
        # Mutate to verify the NEXT test starts fresh (handled by clean_config)
        cfg["__test_marker__"] = True
        # This assertion is just to confirm we can mutate the returned dict
        assert get_config()["__test_marker__"] is True
