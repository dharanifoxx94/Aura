"""Eidolon Vault shared test fixtures."""
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from eidolon_vault.config import reset_config


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Give every test a hermetic home with no .eidolon_vault/config.yaml.

    Patches applied before each test (all restored after automatically):
      - Path.home()  -> tmp_path/fake_home  (empty directory, no .eidolon_vault subdir)
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
