"""
PSIE — Shared test fixtures.

Key design decisions
--------------------
1.  isolated_home (autouse)
    The root cause of the three recurring test failures was that
    ``DEFAULT_CONFIG_PATH = Path.home() / ".psie" / "config.yaml"`` was
    evaluated once at module-import time and baked in the developer's real
    home directory.  Even though load_config() now calls _default_config_path()
    lazily, we ALSO need every test to get a hermetic home so that:
      • Path.home() returns a temp dir with no .psie/config.yaml
      • The CWD contains no accidental config.yaml
      • All PSIE_* env vars are absent (they would override YAML layers)

    This single autouse fixture eliminates all three root causes without
    requiring any test function to remember to do it manually.

2.  clean_config (autouse)
    Resets the thread-safe singleton so each test starts fresh.

3.  minimal_cfg
    A fully in-memory config dict.  All DB / directory paths live under
    tmp_path so they are isolated per-test and cleaned up automatically.
    (The old version used hardcoded /tmp/ paths which could collide between
    parallel test workers.)
"""
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

import psie.config
from psie.config import reset_config


# ---------------------------------------------------------------------------
# Hermetic home-directory isolation — autouse so every test gets it
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Make every test completely independent of the developer's real config.

    Three things are patched:
      1. Path.home() → a fresh empty directory inside tmp_path
         so _default_config_path() returns a path that does not exist.
      2. HOME env var → same fake dir, so os.path.expanduser("~") agrees.
      3. CWD → tmp_path, so LOCAL_CONFIG_PATH ("config.yaml") points at an
         empty directory rather than the project root (which may or may not
         have a real config.yaml).

    Individual tests that need a specific local config.yaml or a specific
    CWD can call monkeypatch.chdir() again — the last call wins and
    monkeypatch still restores the original CWD after the test.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    # 1. Patch Path.home() at the class level — affects all call sites.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    # 2. Patch HOME so os.path.expanduser("~") is consistent.
    monkeypatch.setenv("HOME", str(fake_home))

    # 3. Remove any stray PSIE_* env vars from the real environment.
    for key in [k for k in os.environ if k.startswith("PSIE_")]:
        monkeypatch.delenv(key, raising=False)

    # 4. Change CWD so a real ./config.yaml in the project root is never loaded.
    monkeypatch.chdir(tmp_path)

    yield fake_home


# ---------------------------------------------------------------------------
# Singleton reset — autouse so each test starts with a clean slate
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_config():
    """Reset the config singleton before *and* after every test.

    Without this, one test's load_config() call poisons all later tests
    (the singleton caches the first result forever).
    """
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_gateway():
    """A gateway stub that returns valid JSON for any task type."""
    gw = MagicMock()
    gw.complete.return_value = ('{"entities": [], "relations": []}', 10)
    gw.last_used_backend = "ollama/test"
    return gw


@pytest.fixture
def minimal_cfg(tmp_path):
    """Minimal valid config dict — avoids touching the real filesystem.

    All DB and directory paths live under tmp_path so they are isolated per
    test and cleaned up automatically.  The old version used hardcoded /tmp/
    paths which could cause collisions between parallel test workers.
    """
    return {
        "llm": {
            "routing": {
                "graph_build":      {"preferred": "ollama/test", "fallback": []},
                "persona_generate": {"preferred": "ollama/test", "fallback": []},
                "agent_action":     {"preferred": "ollama/test", "fallback": []},
                "report_generate":  {"preferred": "ollama/test", "fallback": []},
                "skill_extract":    {"preferred": "ollama/test", "fallback": []},
                "fact_extract":     {"preferred": "ollama/test", "fallback": []},
            },
            "providers": {"ollama": {"base_url": "http://localhost:11434", "api_key": "ollama"}},
            "max_tokens": 512,
            "temperature": 0.7,
            "cost_db_path": str(tmp_path / "psie_test_usage.db"),
            "retry_attempts": 0,
            "retry_delay_s": 0.0,
            "request_timeout": 10,
            "task_timeouts": {},
            "task_max_tokens": {},
        },
        "simulation": {
            "max_agents": 4,
            "max_turns": 4,
            "sensitive_mode": False,
            "persona_anchor_interval": 2,
        },
        "graph":  {
            "backend": "custom",
            "storage_dir": str(tmp_path / "graphs"),
            "max_entities": 5,
        },
        "memory": {
            "db_path": str(tmp_path / "psie_test_memory.db"),
            "max_episodic_per_run": 10,
            "max_semantic_inject": 2,
            "max_total_episodes": 100,
        },
        "skills": {
            "db_path": str(tmp_path / "psie_test_skills.db"),
            "top_k_inject": 2,
        },
        "output": {"reports_dir": str(tmp_path / "reports")},
        "input":  {"max_file_bytes": 1048576, "url_timeout_s": 5, "allow_private_ip_url": False},
    }
