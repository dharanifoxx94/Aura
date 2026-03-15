"""
Eidolon Vault — Memory Consolidator
===========================
Maintenance utility that scans the ``facts`` table for potentially contradictory
entries and suggests (or applies) targeted prunes.

How it works
------------
1. Fetch all facts for a given ``scenario_hash`` (or every hash if none given).
2. Group facts that share the same ``(subject, predicate)`` pair — multiple
   objects for the same predicate on the same subject are candidates for
   contradiction.
3. For groups with >1 distinct object, ask the LLM to judge which (if any)
   entries are contradictory and which should be kept.
4. Return a structured list of suggested deletions; optionally apply them.

Usage::

    from eidolon_vault.config import get_config
    from eidolon_vault.llm_gateway import LLMGateway
    from eidolon_vault.memory_consolidator import MemoryConsolidator

    cfg = get_config()
    gw  = LLMGateway(cfg)
    mc  = MemoryConsolidator(cfg, gw)

    suggestions = mc.find_contradictions(dry_run=True)
    for s in suggestions:
        print(s)

    # Apply the suggested prune:
    pruned = mc.prune([s["id"] for s in suggestions if s["action"] == "delete"])
    print(f"Pruned {pruned} facts.")
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .db import db_connect
from .utils import safe_parse_json, truncate

logger = logging.getLogger(__name__)

_CONSOLIDATE_SYSTEM = """\
You are a knowledge-base curator. Your task is to identify contradictory facts
in a small set of (subject, predicate, object) triples that share the same
subject and predicate.

Rules:
  • Only flag entries as contradictory when they are logically incompatible
    (e.g. two different birthdates for the same person).
  • Prefer HIGHER confidence entries.
  • If all objects are plausibly complementary (e.g. multiple valid roles),
    return an empty contradictions list.

Respond ONLY with valid JSON:
{
  "contradictions": [
    {
      "id_to_delete": <integer fact id>,
      "reason": "<short reason>",
      "keep_id": <integer fact id to keep, or null>
    }
  ]
}"""

_CONSOLIDATE_USER = """\
Evaluate these facts for contradictions.
Subject: {subject}
Predicate: {predicate}

Candidates:
{candidates}

Return only the JSON object described in the system prompt."""


class MemoryConsolidator:
    """
    Scan the Eidolon Vault memory store for contradictory facts and suggest/apply prunes.

    Parameters
    ----------
    cfg:
        Eidolon Vault config dict (needs ``memory.db_path``).
    gateway:
        LLMGateway used for contradiction judgement.
    """

    def __init__(self, cfg: Dict[str, Any], gateway: Any) -> None:
        db_raw = cfg["memory"]["db_path"]
        self.db_path = str(Path(db_raw).expanduser())
        self.gateway = gateway

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_contradictions(
        self,
        scenario_hash: str = "",
        *,
        min_group_size: int = 2,
        dry_run: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Identify contradictory fact entries.

        Parameters
        ----------
        scenario_hash:
            If non-empty, restrict analysis to facts for this scenario.
            Pass ``""`` to analyse the entire database.
        min_group_size:
            Minimum number of conflicting objects before a group is sent to
            the LLM for evaluation (default 2).
        dry_run:
            When ``True`` (default), only return suggestions without deleting
            anything.  Set to ``False`` and wrap with ``prune()`` to apply.

        Returns
        -------
        list of dicts with keys: ``id``, ``subject``, ``predicate``,
        ``object``, ``confidence``, ``action`` (``"delete"`` | ``"keep"``),
        ``reason``.
        """
        groups = self._load_candidate_groups(scenario_hash, min_group_size)
        logger.info(
            "Found %d candidate conflict groups (scenario_hash=%r)",
            len(groups), scenario_hash or "*",
        )

        suggestions: List[Dict[str, Any]] = []
        for (subject, predicate), rows in groups.items():
            group_suggestions = self._evaluate_group(subject, predicate, rows)
            suggestions.extend(group_suggestions)

        if not dry_run and suggestions:
            ids_to_delete = [s["id"] for s in suggestions if s["action"] == "delete"]
            if ids_to_delete:
                deleted = self.prune(ids_to_delete)
                logger.info("Auto-pruned %d contradictory facts.", deleted)

        return suggestions

    def prune(self, fact_ids: List[int]) -> int:
        """
        Permanently delete facts by their integer IDs.

        Returns the number of rows deleted.
        """
        if not fact_ids:
            return 0
        placeholders = ",".join("?" * len(fact_ids))
        with db_connect(self.db_path) as conn:
            cursor = conn.execute(
                f"DELETE FROM facts WHERE id IN ({placeholders})", fact_ids
            )
            deleted = cursor.rowcount
        logger.info("Pruned %d fact(s): ids=%s", deleted, fact_ids)
        return deleted

    def summary(self, scenario_hash: str = "") -> Dict[str, Any]:
        """
        Return quick statistics about the facts table.
        """
        with db_connect(self.db_path) as conn:
            scenario_hash_count = 0
            if scenario_hash:
                total = conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE scenario_hash = ?",
                    (scenario_hash,),
                ).fetchone()[0]
                distinct_subjects = conn.execute(
                    "SELECT COUNT(DISTINCT subject) FROM facts WHERE scenario_hash = ?",
                    (scenario_hash,),
                ).fetchone()[0]
            else:
                total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
                distinct_subjects = conn.execute(
                    "SELECT COUNT(DISTINCT subject) FROM facts"
                ).fetchone()[0]
                scenario_hash_count = conn.execute(
                    "SELECT COUNT(DISTINCT scenario_hash) FROM facts"
                ).fetchone()[0]

        result: Dict[str, Any] = {
            "total_facts": total,
            "distinct_subjects": distinct_subjects,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        if not scenario_hash:
            result["scenario_hashes"] = scenario_hash_count
        return result

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_candidate_groups(
        self,
        scenario_hash: str,
        min_group_size: int,
    ) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
        """
        Load all facts grouped by (subject, predicate).
        Only groups with ≥ min_group_size distinct objects are returned.
        """
        with db_connect(self.db_path) as conn:
            if scenario_hash:
                rows = conn.execute(
                    """SELECT id, subject, predicate, object, confidence
                       FROM facts WHERE scenario_hash = ?
                       ORDER BY subject, predicate, confidence DESC""",
                    (scenario_hash,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, subject, predicate, object, confidence
                       FROM facts
                       ORDER BY subject, predicate, confidence DESC"""
                ).fetchall()

        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = (row[1], row[2])  # (subject, predicate)
            groups[key].append(
                {
                    "id": row[0],
                    "subject": row[1],
                    "predicate": row[2],
                    "object": row[3],
                    "confidence": row[4],
                }
            )

        return {
            k: v for k, v in groups.items()
            if len({r["object"] for r in v}) >= min_group_size
        }

    def _evaluate_group(
        self,
        subject: str,
        predicate: str,
        rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Ask the LLM whether any of the rows in this group contradict each other."""
        candidates_text = "\n".join(
            f"  id={r['id']}  object=\"{r['object']}\"  confidence={r['confidence']:.2f}"
            for r in rows
        )
        messages = [
            {"role": "system", "content": _CONSOLIDATE_SYSTEM},
            {
                "role": "user",
                "content": _CONSOLIDATE_USER.format(
                    subject=subject,
                    predicate=predicate,
                    candidates=candidates_text,
                ),
            },
        ]

        try:
            raw, _tokens = self.gateway.complete(
                "consolidate",
                messages,
                max_tokens=300,
                temperature=0.1,
                json_mode=True,
            )
            # Handle both tuple (mock) and str (real gateway) returns
            data = safe_parse_json(raw, fallback={"contradictions": []})
            contradictions: List[Dict[str, Any]] = data.get("contradictions", [])
        except Exception as exc:
            logger.warning(
                "LLM evaluation failed for (%s, %s): %s", subject, predicate, exc
            )
            return []

        id_set = {r["id"] for r in rows}
        suggestions: List[Dict[str, Any]] = []

        delete_ids = {int(c["id_to_delete"]) for c in contradictions if "id_to_delete" in c}

        for row in rows:
            if row["id"] in delete_ids:
                # Find matching reason
                reason = next(
                    (
                        c.get("reason", "Contradicts a higher-confidence entry")
                        for c in contradictions
                        if int(c.get("id_to_delete", -1)) == row["id"]
                    ),
                    "Contradicts another entry",
                )
                suggestions.append({**row, "action": "delete", "reason": reason})
            else:
                suggestions.append({**row, "action": "keep", "reason": ""})

        logger.debug(
            "Group (%s, %s): %d entries, %d flagged for deletion",
            subject, predicate, len(rows), len(delete_ids),
        )
        return suggestions
