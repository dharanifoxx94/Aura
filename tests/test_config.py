"""
Tests for psie/config.py

Root cause of the three recurring failures (now fixed)
------------------------------------------------------
DEFAULT_CONFIG_PATH was evaluated at module-import time, freezing the real
Path.home() before any test fixture could patch it.  load_config() now calls
_default_config_path() at call time, and conftest.py patches Path.home() via
the autouse isolated_home fixture so every test gets a hermetic empty home
with no ~/.psie/config.yaml to bleed in.
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
        monkeypatch.setenv("PSIE_TEST_KEY", "hello")
        assert _expand_env("${PSIE_TEST_KEY}") == "hello"

    def test_unset_var_returns_empty(self, monkeypatch):
        monkeypatch.delenv("PSIE_MISSING", raising=False)
        assert _expand_env("${PSIE_MISSING}") == ""

    def test_tilde_expanded(self):
        result = _expand_env("~/psie_reports")
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
    every test a fake home with no .psie/config.yaml present."""

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
        """With no ~/.psie/config.yaml (empty fake home), only defaults load."""
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]
        assert cfg["llm"]["request_timeout"]   == DEFAULT_CONFIG["llm"]["request_timeout"]

    def test_both_layers_loaded_in_order(self, tmp_path):
        """Both ~/.psie/config.yaml AND ./config.yaml are merged (no early break)."""
        fake_home = tmp_path / "fake_home"
        psie_dir  = fake_home / ".psie"
        psie_dir.mkdir(parents=True, exist_ok=True)
        (psie_dir / "config.yaml").write_text("simulation:\n  max_agents: 5\n")
        (tmp_path / "config.yaml").write_text("simulation:\n  max_turns: 7\n")
        cfg = load_config()
        assert cfg["simulation"]["max_agents"] == 5
        assert cfg["simulation"]["max_turns"]  == 7


class TestEnvOverrides:
    def test_timeout(self, monkeypatch):
        monkeypatch.setenv("PSIE_LLM_TIMEOUT", "300")
        assert load_config()["llm"]["request_timeout"] == 300

    def test_max_agents(self, monkeypatch):
        monkeypatch.setenv("PSIE_MAX_AGENTS", "3")
        assert load_config()["simulation"]["max_agents"] == 3

    def test_sensitive_true(self, monkeypatch):
        monkeypatch.setenv("PSIE_SENSITIVE", "true")
        assert load_config()["simulation"]["sensitive_mode"] is True

    def test_sensitive_false(self, monkeypatch):
        monkeypatch.setenv("PSIE_SENSITIVE", "0")
        assert load_config()["simulation"]["sensitive_mode"] is False

    def test_env_beats_yaml(self, tmp_path, monkeypatch):
        (tmp_path / "config.yaml").write_text("llm:\n  request_timeout: 60\n")
        monkeypatch.setenv("PSIE_LLM_TIMEOUT", "300")
        assert load_config()["llm"]["request_timeout"] == 300

    def test_invalid_env_var_ignored(self, monkeypatch, caplog):
        """Bad cast logs warning and falls back to default — regression test."""
        monkeypatch.setenv("PSIE_MAX_AGENTS", "not_a_number")
        with caplog.at_level(logging.WARNING, logger="psie.config"):
            cfg = load_config()
        assert cfg["simulation"]["max_agents"] == DEFAULT_CONFIG["simulation"]["max_agents"]
        assert any("PSIE_MAX_AGENTS" in r.message for r in caplog.records)

    def test_no_psie_vars_yields_defaults(self):
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
