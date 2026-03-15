"""
PSIE — Skill Bank
==================
MetaClaw‑inspired SQLite‑backed skill store with FTS5 and sanitisation.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from .models import Skill, SimulationLog
from .utils import safe_parse_json, sanitise_for_fts, sanitise_injected_text, truncate
from .db import db_connect
from .constants import ALLOWED_SCENARIO_TYPES
from .exceptions import DatabaseError

if TYPE_CHECKING:
    from .llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    "trigger"        TEXT    NOT NULL,
    archetype_filter TEXT    DEFAULT '*',
    scenario_type    TEXT    DEFAULT '*',
    instruction      TEXT    NOT NULL,
    source_run_id    TEXT    DEFAULT '',
    success_count    INTEGER DEFAULT 0,
    created_at       TEXT,
    UNIQUE(name, archetype_filter, scenario_type)
);

CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
    name,
    trigger_col,
    instruction,
    content=skills,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS skills_ai
AFTER INSERT ON skills BEGIN
    INSERT INTO skills_fts(rowid, name, trigger_col, instruction)
    VALUES (new.id, new.name, new."trigger", new.instruction);
END;

CREATE TRIGGER IF NOT EXISTS skills_ad
AFTER DELETE ON skills BEGIN
    INSERT INTO skills_fts(skills_fts, rowid, name, trigger_col, instruction)
    VALUES ('delete', old.id, old.name, old."trigger", old.instruction);
END;

CREATE TRIGGER IF NOT EXISTS skills_au
AFTER UPDATE ON skills BEGIN
    INSERT INTO skills_fts(skills_fts, rowid, name, trigger_col, instruction)
    VALUES ('delete', old.id, old.name, old."trigger", old.instruction);
    INSERT INTO skills_fts(rowid, name, trigger_col, instruction)
    VALUES (new.id, new.name, new."trigger", new.instruction);
END;
"""

SKILL_EXTRACT_SYSTEM = """\
You are an AI behavior analyst reviewing a completed multi‑agent simulation.
Your job: extract 1‑3 reusable behavioural skills that would make future simulations more accurate.

A skill is a SHORT markdown instruction (2‑4 sentences) that tells an agent HOW to behave better.
Focus on patterns that were effective OR mistakes that should be avoided.

RESPOND ONLY WITH VALID JSON:
{
  "skills": [
    {
      "name": "descriptive_skill_name",
      "trigger": "keyword or phrase that indicates this skill is relevant",
      "archetype_filter": "archetype_name or *",
      "scenario_type": "scenario_type or *",
      "instruction": "Markdown instruction text. 2-4 sentences max."
    }
  ]
}

Return ONLY the JSON — no preamble, no markdown fences.\
"""

SKILL_EXTRACT_USER_TEMPLATE = """\
Review this simulation and extract behavioural skills:

SCENARIO: {scenario_title}
SCENARIO TYPE: {scenario_type}
AGENTS: {agent_list}

SIMULATION LOG (last 20 turns):
{log_excerpt}

Extract skills that would improve FUTURE simulations of similar scenarios.\
"""


class SkillBank:
    """SQLite‑backed store for learned behavioural skills."""

    def __init__(self, cfg: dict) -> None:
        db_path = Path(os.path.expanduser(cfg["skills"]["db_path"]))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)
        self.top_k: int = cfg["skills"].get("top_k_inject", 3)
        self._init_db()

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_skills_for(
        self,
        archetype: str,
        scenario_type: str,
        context_text: str,
        top_k: int | None = None,
    ) -> List[Skill]:
        """
        Retrieve the most relevant skills for an agent archetype + context.
        """
        k = top_k if top_k is not None else self.top_k
        query_words = sanitise_for_fts(context_text)

        with db_connect(self.db_path) as conn:
            try:
                if query_words:
                    rows = conn.execute(
                        """
                        SELECT s.id, s.name, s."trigger", s.archetype_filter,
                               s.scenario_type, s.instruction, s.source_run_id,
                               s.success_count, s.created_at
                        FROM skills s
                        JOIN skills_fts f ON f.rowid = s.id
                        WHERE skills_fts MATCH ?
                          AND (s.archetype_filter = '*' OR s.archetype_filter = ?)
                          AND (s.scenario_type    = '*' OR s.scenario_type    = ?)
                        ORDER BY s.success_count DESC, rank
                        LIMIT ?
                        """,
                        (query_words, archetype, scenario_type, k),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT id, name, "trigger", archetype_filter, scenario_type,
                               instruction, source_run_id, success_count, created_at
                        FROM skills
                        WHERE (archetype_filter = '*' OR archetype_filter = ?)
                          AND (scenario_type    = '*' OR scenario_type    = ?)
                        ORDER BY success_count DESC
                        LIMIT ?
                        """,
                        (archetype, scenario_type, k),
                    ).fetchall()
            except Exception as exc:
                logger.debug("Skill retrieval error: %s", exc)
                rows = []

        return [_row_to_skill(r) for r in rows]

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_skill(self, skill: Skill) -> int:
        """Insert a new skill after sanitising its fields. Returns the new row ID."""
        # Sanitise before storage (already done in Skill.__post_init__, but double‑check)
        name = sanitise_injected_text(skill.name, max_len=100)
        trigger = sanitise_injected_text(skill.trigger, max_len=200)
        archetype_filter = sanitise_injected_text(skill.archetype_filter, max_len=50)
        scenario_type = sanitise_injected_text(skill.scenario_type, max_len=50)
        instruction = sanitise_injected_text(skill.instruction, max_len=800)

        with db_connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO skills
                   (name, "trigger", archetype_filter, scenario_type,
                    instruction, source_run_id, success_count, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    name,
                    trigger,
                    archetype_filter,
                    scenario_type,
                    instruction,
                    skill.source_run_id,
                    skill.success_count,
                    skill.created_at,
                ),
            )
            new_id: int = cur.lastrowid or 0
        logger.info("Skill added: [%d] %s", new_id, name)
        return new_id

    def record_success(self, skill_id: int) -> None:
        """Increment success_count for a skill (used for ranking)."""
        with db_connect(self.db_path) as conn:
            conn.execute(
                "UPDATE skills SET success_count = success_count + 1 WHERE id = ?",
                (skill_id,),
            )

    def list_all(self) -> List[Skill]:
        with db_connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, name, "trigger", archetype_filter, scenario_type,
                          instruction, source_run_id, success_count, created_at
                   FROM skills ORDER BY id"""
            ).fetchall()
        return [_row_to_skill(r) for r in rows]

    def delete(self, skill_id: int) -> None:
        """Delete a skill by ID."""
        with db_connect(self.db_path) as conn:
            conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        logger.info("Skill %d deleted.", skill_id)

    # ------------------------------------------------------------------
    # Auto‑extraction from simulation log
    # ------------------------------------------------------------------

    def extract_from_log(
        self,
        sim_log: SimulationLog,
        gateway: "LLMGateway",
        scenario_type: str = "*",
    ) -> List[Skill]:
        """
        Use an LLM to extract new skills from a completed simulation log.
        Returns the list of newly added ``Skill`` objects.
        """
        # Validate scenario_type (if not "*")
        if scenario_type != "*" and scenario_type not in ALLOWED_SCENARIO_TYPES:
            raise ValueError(f"Invalid scenario_type '{scenario_type}'. Allowed: {sorted(ALLOWED_SCENARIO_TYPES)}")

        log_excerpt = truncate(
            "\n".join(
                f"[Turn {t.turn_number}] {t.agent_name}: {t.response[:200]}"
                for t in sim_log.turns[-20:]
            ),
            3000,
        )
        agent_list = ", ".join(
            f"{a.name} ({a.archetype})" for a in sim_log.agents[:8]
        )

        messages = [
            {"role": "system", "content": SKILL_EXTRACT_SYSTEM},
            {"role": "user", "content": SKILL_EXTRACT_USER_TEMPLATE.format(
                scenario_title=sim_log.scenario_title,
                scenario_type=scenario_type,
                agent_list=agent_list,
                log_excerpt=log_excerpt,
            )},
        ]

        try:
            raw = gateway.complete("skill_extract", messages, json_mode=True)
        except Exception as e:
            logger.warning("Skill extraction failed: %s", e)
            return []

        data = safe_parse_json(raw, fallback={"skills": []})

        new_skills: List[Skill] = []
        for s in data.get("skills", []):
            if not isinstance(s, dict):
                continue
            name        = str(s.get("name", "")).strip()
            trigger     = str(s.get("trigger", "")).strip()
            instruction = str(s.get("instruction", "")).strip()

            if not name or not trigger or not instruction:
                logger.debug("Skipping invalid skill entry: %s", s)
                continue

            try:
                skill = Skill(
                    skill_id=None,
                    name=name[:100],
                    trigger=trigger[:200],
                    archetype_filter=str(s.get("archetype_filter", "*"))[:50],
                    scenario_type=scenario_type,
                    instruction=instruction[:800],
                    source_run_id=sim_log.run_id,
                )
                new_id = self.add_skill(skill)
                skill.skill_id = new_id
                new_skills.append(skill)
            except Exception as exc:
                logger.warning("Skill insertion error: %s", exc)

        logger.info("Extracted %d new skills from run %s", len(new_skills), sim_log.run_id)
        return new_skills

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with db_connect(self.db_path) as conn:
            conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_skill(row: Tuple) -> Skill:
    return Skill(
        skill_id=row[0],
        name=row[1],
        trigger=row[2],
        archetype_filter=row[3],
        scenario_type=row[4],
        instruction=row[5],
        source_run_id=row[6],
        success_count=row[7],
        created_at=row[8],
    )
