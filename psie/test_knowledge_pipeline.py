"""
Integration tests — Knowledge Ingestion Pipeline
=================================================
Covers the feeder → knowledge_worker → memory_store flow using the existing
``mock_gateway`` and ``minimal_cfg`` fixtures from tests/conftest.py.

Run with:
    pytest tests/test_knowledge_pipeline.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def worker_setup(minimal_cfg, mock_gateway):
    """
    Build a PSIEEngine (from_config) wired to mock_gateway, then return both
    the engine and a KnowledgeWorker instance.
    """
    from psie.engine import PSIEEngine
    from psie.knowledge_worker import KnowledgeWorker

    engine = PSIEEngine.from_config(minimal_cfg)
    # Replace the real gateway with the mock so no network/LLM calls are made.
    engine.gateway = mock_gateway
    engine.simulation_runner.gateway = mock_gateway
    engine.memory_store  # ensure DB is initialised
    worker = KnowledgeWorker(engine)
    return engine, worker


def _make_sim_response(text: str = "I agree with the analysis.") -> str:
    """Return a mock gateway response string matching the real gateway signature."""
    return text


def _make_fact_response(facts: list | None = None) -> str:
    payload = {"facts": facts or [
        {"subject": "The Analyst", "predicate": "prefers", "object": "evidence-based claims", "confidence": 0.9},
        {"subject": "The Skeptic",  "predicate": "challenges", "object": "unverified assumptions", "confidence": 0.8},
    ]}
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# ContentFeeder tests
# ---------------------------------------------------------------------------

class TestContentFeeder:
    def test_ingest_text_returns_scenario_context(self):
        from psie.feeder import ContentFeeder, ingest
        ctx = ingest("Alice and Bob are negotiating a contract.", source_type="text")
        assert ctx.raw_text
        assert ctx.source_type == "text"

    def test_auto_detect_text(self):
        from psie.feeder import ContentFeeder
        feeder = ContentFeeder()
        ctx = feeder.ingest("plain scenario text without a URL")
        assert ctx.source_type == "text"

    def test_auto_detect_url_pattern(self):
        """_detect_type should route http:// to url, not rss."""
        from psie.feeder import _detect_type
        assert _detect_type("https://example.com/article") == "url"

    def test_auto_detect_rss_pattern(self):
        from psie.feeder import _detect_type
        assert _detect_type("https://example.com/feed") == "rss"
        assert _detect_type("https://example.com/rss.xml") == "rss"

    def test_hash_is_deterministic(self):
        from psie.feeder import ContentFeeder
        feeder = ContentFeeder()
        ctx = feeder.ingest("Stable scenario text.", source_type="text")
        h1 = feeder.hash_for(ctx)
        h2 = feeder.hash_for(ctx)
        assert h1 == h2
        assert len(h1) == 16

    def test_ingest_rss_fallback_no_gateway(self, monkeypatch):
        """Without a gateway, feeder should concatenate items (no LLM call)."""
        import types
        fake_feed = types.SimpleNamespace(
            bozo=False,
            feed=types.SimpleNamespace(title="Test Feed"),
            entries=[
                types.SimpleNamespace(title="Item 1", summary="Summary one", description=""),
                types.SimpleNamespace(title="Item 2", summary="Summary two", description=""),
            ],
        )
        monkeypatch.setattr("feedparser.parse", lambda _: fake_feed)
        from psie.feeder import ContentFeeder
        feeder = ContentFeeder(gateway=None)
        ctx = feeder.ingest_rss("https://example.com/feed.xml", max_items=2)
        assert "Item 1" in ctx.raw_text
        assert "Item 2" in ctx.raw_text

    def test_ingest_rss_uses_gateway_for_condensation(self, mock_gateway, monkeypatch):
        """With a gateway present, feeder should call complete() for condensation."""
        import types
        fake_feed = types.SimpleNamespace(
            bozo=False,
            feed=types.SimpleNamespace(title="Tech News"),
            entries=[
                types.SimpleNamespace(title="Chip shortage", summary="Chip supply fell.", description=""),
            ],
        )
        monkeypatch.setattr("feedparser.parse", lambda _: fake_feed)
        mock_gateway.complete.return_value = "Condensed scenario about chips."

        from psie.feeder import ContentFeeder
        feeder = ContentFeeder(gateway=mock_gateway)
        ctx = feeder.ingest_rss("https://example.com/feed.xml", max_items=1)

        mock_gateway.complete.assert_called_once()
        assert ctx.raw_text == "Condensed scenario about chips."

    def test_ingest_rss_empty_feed_raises(self, monkeypatch):
        import types
        from psie.exceptions import InputError
        fake_feed = types.SimpleNamespace(bozo=False, feed=types.SimpleNamespace(title=""), entries=[])
        monkeypatch.setattr("feedparser.parse", lambda _: fake_feed)
        from psie.feeder import ContentFeeder
        feeder = ContentFeeder()
        with pytest.raises(InputError, match="no entries"):
            feeder.ingest_rss("https://example.com/empty.xml")


# ---------------------------------------------------------------------------
# Static persona loading
# ---------------------------------------------------------------------------

class TestPersonaLoading:
    def test_load_bundled_personas(self, worker_setup):
        _, worker = worker_setup
        personas = worker.load_personas()
        assert len(personas) >= 3
        names = [p.name for p in personas]
        assert "The Analyst"  in names
        assert "The Skeptic"  in names
        assert "The Archivist" in names

    def test_personas_are_valid_agent_personas(self, worker_setup):
        from psie.models import AgentPersona
        _, worker = worker_setup
        for p in worker.load_personas():
            assert isinstance(p, AgentPersona)
            assert p.agent_id
            assert p.archetype
            assert 0.0 <= p.openness <= 1.0

    def test_personas_cached(self, worker_setup):
        _, worker = worker_setup
        p1 = worker.load_personas()
        p2 = worker.load_personas()
        assert p1 is p2  # same list object (cache hit)

    def test_missing_yaml_raises(self, worker_setup, tmp_path):
        engine, _ = worker_setup
        from psie.knowledge_worker import KnowledgeWorker
        bad_worker = KnowledgeWorker(engine, personas_path=str(tmp_path / "nope.yaml"))
        with pytest.raises(FileNotFoundError):
            bad_worker.load_personas()

    def test_malformed_yaml_raises(self, worker_setup, tmp_path):
        engine, _ = worker_setup
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("personas: not_a_list\n")
        from psie.knowledge_worker import KnowledgeWorker
        bad_worker = KnowledgeWorker(engine, personas_path=str(bad_yaml))
        with pytest.raises(ValueError, match="empty"):
            bad_worker.load_personas()


# ---------------------------------------------------------------------------
# KnowledgeWorker end-to-end (feeder → sim → memory)
# ---------------------------------------------------------------------------

class TestKnowledgeWorker:
    def _configure_mock(self, mock_gateway):
        """Make the mock gateway return something sensible for all task types."""
        def side_effect(task_type, messages, **kwargs):
            if task_type == "fact_extract":
                return _make_fact_response()
            if task_type == "summarise":
                return "Condensed text."
            # Default: agent_action / anything else
            return _make_sim_response()

        mock_gateway.complete.side_effect = side_effect
        mock_gateway.last_used_backend = "ollama/test"
        return mock_gateway

    def test_learn_from_text_runs_full_pipeline(self, worker_setup):
        engine, worker = worker_setup
        self._configure_mock(engine.gateway)

        result = worker.learn_from_source(
            "Two engineers debate the best caching strategy.",
            source_type="text",
            num_turns=2,
        )

        assert result["turns"] >= 1
        assert isinstance(result["run_id"], str)
        assert isinstance(result["facts_stored"], int)
        assert not result["interrupted"]

    def test_episodes_stored_after_learn(self, worker_setup):
        engine, worker = worker_setup
        self._configure_mock(engine.gateway)

        result = worker.learn_from_source(
            "A startup founder pitches to a skeptical investor.",
            source_type="text",
            num_turns=2,
        )

        runs = engine.memory_store.list_runs(limit=10)
        run_ids = [r["run_id"] for r in runs]
        assert result["run_id"] in run_ids

    def test_learn_from_context_accepts_scenario_context(self, worker_setup):
        from psie.feeder import ingest
        engine, worker = worker_setup
        self._configure_mock(engine.gateway)

        ctx = ingest("The board disagrees on the acquisition.", source_type="text")
        result = worker.learn_from_context(ctx, num_turns=2)

        assert result["scenario_hash"] == worker._feeder.hash_for(ctx)

    def test_progress_callback_is_called(self, worker_setup):
        engine, worker = worker_setup
        self._configure_mock(engine.gateway)

        messages_seen = []
        worker.learn_from_source(
            "Scenario text.",
            source_type="text",
            num_turns=2,
            progress_callback=messages_seen.append,
        )

        assert any("Ingesting" in m for m in messages_seen)
        assert any("Done" in m or "✅" in m for m in messages_seen)

    def test_scenario_hash_is_consistent(self, worker_setup):
        engine, worker = worker_setup
        self._configure_mock(engine.gateway)

        text = "Identical scenario text used twice."
        r1 = worker.learn_from_source(text, source_type="text", num_turns=2)
        r2 = worker.learn_from_source(text, source_type="text", num_turns=2)
        assert r1["scenario_hash"] == r2["scenario_hash"]


# ---------------------------------------------------------------------------
# MemoryConsolidator tests
# ---------------------------------------------------------------------------

class TestMemoryConsolidator:
    def _insert_facts(self, cfg, rows):
        from psie.db import db_connect
        from pathlib import Path
        import os
        db_path = str(Path(os.path.expanduser(cfg["memory"]["db_path"])))
        with db_connect(db_path) as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO facts
                   (scenario_hash, subject, predicate, object, confidence, source_run_id, ts)
                   VALUES (?,?,?,?,?,?,datetime('now'))""",
                rows,
            )

    def test_summary_returns_counts(self, minimal_cfg, mock_gateway):
        from psie.memory_store import MemoryStore
        from psie.memory_consolidator import MemoryConsolidator

        MemoryStore(minimal_cfg)  # ensure schema exists
        self._insert_facts(minimal_cfg, [
            ("abc123", "Alice", "prefers", "chocolate", 0.9, "run1"),
            ("abc123", "Alice", "prefers", "vanilla",   0.7, "run2"),
        ])
        mc = MemoryConsolidator(minimal_cfg, mock_gateway)
        stats = mc.summary("abc123")
        assert stats["total_facts"] == 2
        assert stats["distinct_subjects"] == 1

    def test_find_contradictions_calls_llm(self, minimal_cfg, mock_gateway):
        from psie.memory_store import MemoryStore
        from psie.memory_consolidator import MemoryConsolidator

        MemoryStore(minimal_cfg)
        self._insert_facts(minimal_cfg, [
            ("hash1", "Bob", "birthdate", "1980-01-01", 0.9, "run1"),
            ("hash1", "Bob", "birthdate", "1990-06-15", 0.5, "run2"),
        ])
        mock_gateway.complete.return_value = json.dumps(
            {"contradictions": [{"id_to_delete": 2, "reason": "Lower confidence", "keep_id": 1}]}
        )
        mc = MemoryConsolidator(minimal_cfg, mock_gateway)
        suggestions = mc.find_contradictions("hash1", dry_run=True)

        mock_gateway.complete.assert_called_once()
        delete_suggestions = [s for s in suggestions if s["action"] == "delete"]
        assert len(delete_suggestions) >= 1

    def test_prune_removes_rows(self, minimal_cfg, mock_gateway):
        from psie.memory_store import MemoryStore
        from psie.memory_consolidator import MemoryConsolidator
        from psie.db import db_connect
        import os

        MemoryStore(minimal_cfg)
        self._insert_facts(minimal_cfg, [
            ("hashX", "Carol", "likes", "cats", 0.8, "run1"),
            ("hashX", "Carol", "likes", "dogs", 0.6, "run2"),
        ])
        db_path = str(Path(os.path.expanduser(minimal_cfg["memory"]["db_path"])))
        with db_connect(db_path) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM facts").fetchall()]

        mc = MemoryConsolidator(minimal_cfg, mock_gateway)
        deleted = mc.prune([ids[0]])
        assert deleted == 1

        with db_connect(db_path) as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert remaining == 1
