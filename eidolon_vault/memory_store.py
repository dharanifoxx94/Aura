"""
Eidolon Vault — Memory Store
====================
Persistent episodic + semantic memory across simulation runs.
Uses SQLite with FTS5 and automatic pruning.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Dict, Any, Tuple

from .models import SimulationLog
from .utils import safe_parse_json, sanitise_for_fts, sanitise_injected_text, truncate
from .db import db_connect
from .exceptions import DatabaseError

if TYPE_CHECKING:
    from .llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    scenario_hash TEXT NOT NULL,
    agent_id     TEXT,
    agent_name   TEXT,
    turn_number  INTEGER,
    content      TEXT,
    ts           TEXT,
    UNIQUE(run_id, agent_id, turn_number)
);

CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    content,
    agent_name,
    content=episodes,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS eps_ai
AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, content, agent_name)
    VALUES (new.id, new.content, new.agent_name);
END;

CREATE TRIGGER IF NOT EXISTS eps_ad
AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content, agent_name)
    VALUES ('delete', old.id, old.content, old.agent_name);
END;

CREATE TRIGGER IF NOT EXISTS eps_au
AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content, agent_name)
    VALUES ('delete', old.id, old.content, old.agent_name);
    INSERT INTO episodes_fts(rowid, content, agent_name)
    VALUES (new.id, new.content, new.agent_name);
END;

CREATE TABLE IF NOT EXISTS facts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_hash TEXT NOT NULL,
    subject      TEXT,
    predicate    TEXT,
    object       TEXT,
    confidence   REAL DEFAULT 0.8,
    source_run_id TEXT,
    ts           TEXT,
    UNIQUE(scenario_hash, subject, predicate, object)
);

CREATE INDEX IF NOT EXISTS idx_facts_scenario  ON facts(scenario_hash);
CREATE INDEX IF NOT EXISTS idx_episodes_run    ON episodes(run_id);
CREATE INDEX IF NOT EXISTS idx_episodes_hash   ON episodes(scenario_hash);
CREATE INDEX IF NOT EXISTS idx_episodes_ts     ON episodes(ts);
"""

FACT_EXTRACT_SYSTEM = """\
You are an expert analyst of multi-agent social simulations.
Your task is to extract DURABLE, HIGH-VALUE facts about the agents from the provided simulation log.

Focus on:
1. HIDDEN AGENDAS: What are they truly after?
2. RELATIONSHIPS: Who do they trust, fear, or manipulate?
3. PERSONALITY QUIRKS: Specific triggers, biases, or speech patterns.
4. OUTCOMES: What strategies worked or failed for them?

DO NOT extract:
- Trivial details (e.g., "Agent said hello").
- Transient states (e.g., "Agent is currently sitting").
- Facts already known from the persona description.

RESPOND ONLY WITH VALID JSON:
{
  "facts": [
    {"subject": "Agent Name", "predicate": "is secretly aligned with", "object": "The Corporation", "confidence": 0.95},
    {"subject": "Agent Name", "predicate": "responds poorly to", "object": "aggressive negotiation", "confidence": 0.8}
  ]
}

Confidence: 1.0 = explicitly stated/proven, 0.5 = strong inference, 0.3 = speculative hint.
Return ONLY the JSON object.\
"""

FACT_EXTRACT_USER = """\
Extract facts from this simulation log:

SCENARIO: {scenario_title}

LOG:
{log_excerpt}

Return facts that would be useful for predicting outcomes in future similar scenarios.\
"""


class MemoryStore:
    """Persistent episodic and semantic memory layer."""

    def __init__(self, cfg: dict) -> None:
        db_path = Path(os.path.expanduser(cfg["memory"]["db_path"]))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)
        self.max_episodic_per_run: int = cfg["memory"].get("max_episodic_per_run", 50)
        self.max_semantic_inject: int = cfg["memory"].get("max_semantic_inject", 5)
        self.max_total_episodes: int = cfg["memory"].get("max_total_episodes", 5_000)
        self._init_db()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def store_simulation(self, sim_log: SimulationLog, scenario_hash: str) -> None:
        """Persist all turns from a simulation as episodic memories."""
        ts_now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                sim_log.run_id,
                scenario_hash,
                turn.agent_id,
                turn.agent_name,
                turn.turn_number,
                turn.response,
                turn.timestamp or ts_now,
            )
            for turn in sim_log.turns[: self.max_episodic_per_run]
        ]
        with db_connect(self.db_path) as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO episodes
                   (run_id, scenario_hash, agent_id, agent_name, turn_number, content, ts)
                   VALUES (?,?,?,?,?,?,?)""",
                rows,
            )
        logger.info(
            "Stored %d episodic memories for run %s",
            len(rows), sim_log.run_id,
        )
        self._prune_if_needed()

    def store_facts(self, facts: List[Dict[str, Any]], scenario_hash: str, run_id: str) -> None:
        """Store extracted semantic facts after sanitisation. Skips empty triples."""
        ts_now = datetime.now(timezone.utc).isoformat()
        rows = []
        for f in facts:
            subject = sanitise_injected_text(str(f.get("subject", "")), max_len=200)
            predicate = sanitise_injected_text(str(f.get("predicate", "")), max_len=200)
            obj = sanitise_injected_text(str(f.get("object", "")), max_len=200)
            raw_conf = float(f.get("confidence", 0.8))
            confidence = max(0.0, min(1.0, raw_conf))
            # Skip if any part is empty after sanitisation
            if not subject or not predicate or not obj:
                logger.debug("Skipping fact with empty subject/predicate/object: %s", f)
                continue
            rows.append((
                scenario_hash,
                subject,
                predicate,
                obj,
                confidence,
                run_id,
                ts_now,
            ))
        if rows:
            with db_connect(self.db_path) as conn:
                conn.executemany(
                    """INSERT OR IGNORE INTO facts
                       (scenario_hash, subject, predicate, object, confidence, source_run_id, ts)
                       VALUES (?,?,?,?,?,?,?)""",
                    rows,
                )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def recall_episodes(
        self,
        agent_name: str,
        context_query: str,
        scenario_hash: str = "",
        top_k: int = 5,
    ) -> List[str]:
        """
        Full‑text search over past episode content for a given agent.
        Returns sanitised content strings.
        """
        query_terms = sanitise_for_fts(context_query)

        with db_connect(self.db_path) as conn:
            if query_terms:
                try:
                    rows = conn.execute(
                        """
                        SELECT e.content
                        FROM episodes e
                        JOIN episodes_fts f ON f.rowid = e.id
                        WHERE episodes_fts MATCH ?
                          AND e.agent_name = ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (query_terms, agent_name, top_k),
                    ).fetchall()
                except Exception as exc:
                    logger.debug("Episode FTS recall error (falling back): %s", exc)
                    # Fallback: simple LIKE search if FTS fails or query is complex
                    rows = conn.execute(
                        "SELECT content FROM episodes WHERE agent_name = ? AND content LIKE ? ORDER BY ts DESC LIMIT ?",
                        (agent_name, f"%{query_terms}%", top_k),
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT content FROM episodes WHERE agent_name = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (agent_name, top_k),
                ).fetchall()
            
            # If still no rows, fallback to most recent (context is better than nothing?)
            if not rows and query_terms:
                 rows = conn.execute(
                    "SELECT content FROM episodes WHERE agent_name = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (agent_name, top_k),
                ).fetchall()

        # Sanitise each memory before returning
        return [sanitise_injected_text(r[0], 500) for r in rows]

    def recall_facts(
        self,
        scenario_hash: str,
        subject_filter: str = "",
        top_k: int | None = None,
    ) -> List[str]:
        """Retrieve stored semantic facts, optionally filtered by subject."""
        k = top_k if top_k is not None else self.max_semantic_inject
        with db_connect(self.db_path) as conn:
            if subject_filter:
                safe_f = _escape_like(subject_filter)
                rows = conn.execute(
                    """SELECT subject, predicate, object, confidence
                       FROM facts
                       WHERE scenario_hash = ?
                         AND (subject LIKE ? ESCAPE '\\'
                              OR  object LIKE ? ESCAPE '\\')
                       ORDER BY confidence DESC LIMIT ?""",
                    (scenario_hash, f"%{safe_f}%", f"%{safe_f}%", k),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT subject, predicate, object, confidence
                       FROM facts
                       WHERE scenario_hash = ?
                       ORDER BY confidence DESC LIMIT ?""",
                    (scenario_hash, k),
                ).fetchall()
        return [f"{r[0]} {r[1]} {r[2]} (confidence: {r[3]:.1f})" for r in rows]

    def get_memories_for_agent(
        self,
        agent_name: str,
        archetype: str,
        scenario_hash: str,
        context_text: str,
    ) -> List[str]:
        """
        Combined retrieval: episodic recall + semantic facts for this agent.
        Returns sanitised memory strings ready to inject into system prompts.
        """
        # Prioritise semantic facts as they are more distilled
        semantic = self.recall_facts(
            scenario_hash=scenario_hash,
            subject_filter=agent_name,
            top_k=self.max_semantic_inject,
        )
        
        # Then episodic, filling the gap
        episodic = self.recall_episodes(
            agent_name=agent_name,
            context_query=context_text,
            scenario_hash=scenario_hash,
            top_k=5,
        )
        
        # Interleave or combine? For now, combine, semantic first.
        combined = semantic + episodic
        # The limit is applied by the caller (SimulationRunner), but we can trim here too safely
        return combined

    # ------------------------------------------------------------------
    # Auto fact extraction
    # ------------------------------------------------------------------

    def extract_and_store_facts(
        self,
        sim_log: SimulationLog,
        scenario_hash: str,
        gateway: "LLMGateway",
    ) -> int:
        """Extract semantic facts from a completed sim log and store them."""
        # Increased truncation limit to capture more context
        log_excerpt = truncate(
            "\n".join(
                f"[Turn {t.turn_number}] {t.agent_name}: {t.response[:300]}"
                for t in sim_log.turns
            ),
            12000, 
        )

        messages = [
            {"role": "system", "content": FACT_EXTRACT_SYSTEM},
            {"role": "user", "content": FACT_EXTRACT_USER.format(
                scenario_title=sim_log.scenario_title,
                log_excerpt=log_excerpt,
            )},
        ]

        try:
            # Use dedicated task type "fact_extract"
            raw, _tokens = gateway.complete("fact_extract", messages, json_mode=True)
            data = safe_parse_json(raw, fallback={"facts": []})
            facts = [f for f in data.get("facts", []) if isinstance(f, dict)]
            if facts:
                self.store_facts(facts, scenario_hash, sim_log.run_id)
            logger.info("Extracted %d facts from run %s", len(facts), sim_log.run_id)
            return len(facts)
        except Exception as exc:
            logger.warning("Fact extraction failed: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # History query
    # ------------------------------------------------------------------

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent simulation run IDs and metadata."""
        with db_connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT run_id,
                       scenario_hash,
                       COUNT(*)  AS turns,
                       MAX(ts)   AS last_turn
                FROM episodes
                GROUP BY run_id, scenario_hash
                ORDER BY last_turn DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {"run_id": r[0], "scenario_hash": r[1], "turns": r[2], "last_turn": r[3]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with db_connect(self.db_path) as conn:
            conn.executescript(SCHEMA)

    def _prune_if_needed(self) -> None:
        """
        Delete the oldest episodic rows when the total count exceeds
        ``max_total_episodes``.  Keeps the database from growing without bound.
        """
        with db_connect(self.db_path) as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
            if count > self.max_total_episodes:
                excess = count - self.max_total_episodes
                conn.execute(
                    """
                    DELETE FROM episodes
                    WHERE id IN (
                        SELECT id FROM episodes ORDER BY ts ASC LIMIT ?
                    )
                    """,
                    (excess,),
                )
                logger.info(
                    "Pruned %d old episodic memory rows (total was %d, limit %d).",
                    excess, count, self.max_total_episodes,
                )


def _escape_like(s: str) -> str:
    """Escape SQLite LIKE wildcards — was missing from memory_store.py."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
