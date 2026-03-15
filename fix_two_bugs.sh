#!/bin/sh
# fix_two_bugs.sh — Drop in ~/EIDOLON_VAULT_v-1.4/ and run: sh fix_two_bugs.sh
#
# BUG-1  engine.py:  run_from_url() never passed max_bytes to parse_url().
#                    The 20 MB streaming body cap (FIX-3) was silently bypassed
#                    for every URL-sourced run. One-line fix.
#
# BUG-2  config.py:  DEFAULT_CONFIG missing task_max_tokens + task_timeouts.
#                    fix_report_tokens removed the hardcoded max_tokens=2048
#                    from report_generator relying on those keys in config.yaml.
#                    Fresh installs got an empty dict, fell back to 1024 tokens,
#                    and Gemini 2.5 Flash reports were silently truncated.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

python3 - << 'PYEOF'
import ast
from pathlib import Path

# ── BUG-1: engine.py ─────────────────────────────────────────────────────────
p = Path("eidolon-vault/engine.py")
src = p.read_text()

old = (
    '        timeout_s = self.cfg.get("input", {}).get("url_timeout_s", 20)\n'
    '        ctx = parse_url(url, timeout_s=timeout_s, allow_private_ip=allow_private_ip)\n'
)
new = (
    '        timeout_s = self.cfg.get("input", {}).get("url_timeout_s", 20)\n'
    '        max_bytes = self.cfg.get("input", {}).get("max_file_bytes", 20 * 1024 * 1024)\n'
    '        ctx = parse_url(url, timeout_s=timeout_s, allow_private_ip=allow_private_ip,\n'
    '                        max_bytes=max_bytes)\n'
)

assert old in src, "BUG-1 pattern not found — already fixed or source changed"
src = src.replace(old, new)
ast.parse(src)
p.write_text(src)
print("BUG-1 fixed — engine.py now passes max_bytes to parse_url()")

# ── BUG-2: config.py ─────────────────────────────────────────────────────────
# Rationale for values:
#   task_max_tokens:
#     report_generate: 4096 — Gemini 2.5 Flash uses ~1500 thinking tokens
#       before output; a full transcript report needs ~2500+ output tokens
#     agent_action: 512 — caller caps at 300 anyway; 512 gives safe headroom
#     all others: 1024 — compact JSON output, ample for these tasks
#   task_timeouts:
#     agent_action: 45s — small local model (gemma3:4b), must be snappy
#     report_generate: 180s — large model + long prompt, needs more time
#     all others: inherit global request_timeout (60s)
p = Path("eidolon-vault/config.py")
src = p.read_text()

old = (
    '        "retry_attempts": 2,\n'
    '        "retry_delay_s": 3.0,\n'
    '        "request_timeout": DEFAULT_LLM_TIMEOUT,\n'
    '    },\n'
)
new = (
    '        "retry_attempts": 2,\n'
    '        "retry_delay_s": 3.0,\n'
    '        "request_timeout": DEFAULT_LLM_TIMEOUT,\n'
    '        # Per-task token limits — override global max_tokens per task type.\n'
    '        # report_generate needs 4096 for Gemini 2.5 Flash thinking overhead.\n'
    '        "task_max_tokens": {\n'
    '            "graph_build":      1024,\n'
    '            "persona_generate": 1024,\n'
    '            "agent_action":      512,\n'
    '            "report_generate":  4096,\n'
    '            "skill_extract":    1024,\n'
    '            "fact_extract":     1024,\n'
    '        },\n'
    '        # Per-task timeout overrides in seconds (inherits request_timeout if absent).\n'
    '        "task_timeouts": {\n'
    '            "agent_action":    45,\n'
    '            "report_generate": 180,\n'
    '        },\n'
    '    },\n'
)

assert old in src, "BUG-2 pattern not found — already fixed or source changed"
src = src.replace(old, new)
ast.parse(src)
p.write_text(src)
print("BUG-2 fixed — DEFAULT_CONFIG now has task_max_tokens + task_timeouts")

print()
print("Verifying tests...")
PYEOF

python3 -m pytest tests/ -q
