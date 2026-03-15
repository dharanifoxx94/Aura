#!/bin/sh
# fix_skill_memory.sh — Drop in ~/EIDOLON_VAULT_v-1.4/ and run: sh fix_skill_memory.sh
#
# CRITICAL  skill_bank.py + memory_store.py: gateway.complete() returns a
#           (content, tokens) tuple since fix_medium.py, but both callers
#           assigned the whole tuple to `raw` and passed it to safe_parse_json,
#           which crashed with TypeError. The except block in extract_from_log
#           only caught gateway.complete() failures — so the TypeError propagated
#           up and crashed the post-simulation learning step on every run.
#           All skill extraction and fact extraction has been silently broken.
#
# HIGH      No deduplication: same facts/skills inserted repeatedly on each run.
#           Fix: UNIQUE constraints + INSERT OR IGNORE on all three tables.
#
# HIGH      recall_facts() LIKE query used subject_filter (= agent name) raw.
#           SQLite LIKE treats % and _ as wildcards — an agent named "%" would
#           match all fact rows. Fix: _escape_like() helper + ESCAPE clause.
#
# MEDIUM    Confidence values from LLM stored with no range check.
#           Fix: clamp to [0.0, 1.0] before storage.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

python3 << 'PYEOF'
import ast
from pathlib import Path

# ── skill_bank.py ─────────────────────────────────────────────────────────────
p = Path("eidolon-vault/skill_bank.py")
src = p.read_text()

src = src.replace(
    '            raw = gateway.complete("skill_extract", messages, json_mode=True)\n',
    '            raw, _tokens = gateway.complete("skill_extract", messages, json_mode=True)\n'
)
src = src.replace(
    '    success_count    INTEGER DEFAULT 0,\n'
    '    created_at       TEXT\n'
    ');\n',
    '    success_count    INTEGER DEFAULT 0,\n'
    '    created_at       TEXT,\n'
    '    UNIQUE(name, archetype_filter, scenario_type)\n'
    ');\n'
)
src = src.replace(
    '                """INSERT INTO skills\n',
    '                """INSERT OR IGNORE INTO skills\n'
)

ast.parse(src)
p.write_text(src)
print("skill_bank.py patched")

# ── memory_store.py ───────────────────────────────────────────────────────────
p = Path("eidolon-vault/memory_store.py")
src = p.read_text()

# CRIT: tuple unpack
src = src.replace(
    '            raw = gateway.complete("fact_extract", messages, json_mode=True)\n',
    '            raw, _tokens = gateway.complete("fact_extract", messages, json_mode=True)\n'
)

# UNIQUE constraints
src = src.replace(
    '    turn_number  INTEGER,\n'
    '    content      TEXT,\n'
    '    ts           TEXT\n'
    ');\n',
    '    turn_number  INTEGER,\n'
    '    content      TEXT,\n'
    '    ts           TEXT,\n'
    '    UNIQUE(run_id, agent_id, turn_number)\n'
    ');\n'
)
src = src.replace(
    '    source_run_id TEXT,\n'
    '    ts           TEXT\n'
    ');\n',
    '    source_run_id TEXT,\n'
    '    ts           TEXT,\n'
    '    UNIQUE(scenario_hash, subject, predicate, object)\n'
    ');\n'
)

# INSERT OR IGNORE
src = src.replace(
    '                """INSERT INTO episodes\n',
    '                """INSERT OR IGNORE INTO episodes\n'
)
src = src.replace(
    '                    """INSERT INTO facts\n',
    '                    """INSERT OR IGNORE INTO facts\n'
)

# LIKE wildcard escape
src = src.replace(
    '            if subject_filter:\n'
    '                rows = conn.execute(\n'
    '                    """SELECT subject, predicate, object, confidence\n'
    '                       FROM facts\n'
    '                       WHERE scenario_hash = ?\n'
    '                         AND (subject LIKE ? OR object LIKE ?)\n'
    '                       ORDER BY confidence DESC LIMIT ?""",\n'
    '                    (scenario_hash, f"%{subject_filter}%", f"%{subject_filter}%", k),\n'
    '                ).fetchall()\n',
    '            if subject_filter:\n'
    '                safe_f = _escape_like(subject_filter)\n'
    '                rows = conn.execute(\n'
    '                    """SELECT subject, predicate, object, confidence\n'
    '                       FROM facts\n'
    "                       WHERE scenario_hash = ?\n"
    "                         AND (subject LIKE ? ESCAPE '\\\\'\n"
    "                              OR  object LIKE ? ESCAPE '\\\\')\n"
    '                       ORDER BY confidence DESC LIMIT ?""",\n'
    '                    (scenario_hash, f"%{safe_f}%", f"%{safe_f}%", k),\n'
    '                ).fetchall()\n'
)

# Confidence clamp
src = src.replace(
    '            confidence = float(f.get("confidence", 0.8))\n'
    '            # Skip if any part is empty after sanitisation\n',
    '            raw_conf = float(f.get("confidence", 0.8))\n'
    '            confidence = max(0.0, min(1.0, raw_conf))\n'
    '            # Skip if any part is empty after sanitisation\n'
)

# Add _escape_like helper before the class if not already present
if '_escape_like' not in src:
    helper = (
        '\n\ndef _escape_like(text: str) -> str:\n'
        '    """Escape SQLite LIKE wildcards (% and _) in *text*.\n\n'
        '    Use with ESCAPE chr(92) in the SQL query.\n'
        '    """\n'
        "    return text.replace('\\\\', '\\\\\\\\').replace('%', '\\\\%').replace('_', '\\\\_')\n"
    )
    src = src.replace('\nclass MemoryStore:', helper + '\nclass MemoryStore:')

ast.parse(src)
p.write_text(src)
print("memory_store.py patched")

print()
print("Verifying tests...")
PYEOF

python3 -m pytest tests/ -q
