#!/usr/bin/env bash
# =============================================================================
# Eidolon Vault — Apply recommended fixes from architecture review
#
# Patches applied:
#   A. config.py   — add 'summarise' route (gemini-2.5-flash preferred)
#                  — bump fact_extract timeout to 180s, agent_action to 120s
#                  — add summarise to task_max_tokens
#   B. input_parser.py — fix SSL bug in eidolon-vault run --url (IP mismatch → use hostname)
#   C. config.yaml — fix indentation + add summarise route + fact_extract timeout
#   D. agent_personas.yaml — add C++ Tutor persona
#
# Usage:
#   bash apply_review_fixes.sh
#   EIDOLON_VAULT_DIR=/custom/path bash apply_review_fixes.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()   { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()     { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()   { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()    { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
banner() { echo -e "\n${BOLD}$*${RESET}"; }

EIDOLON_VAULT_CONFIG="${HOME}/.eidolon-vault/config.yaml"

# ── Locate Eidolon Vault ───────────────────────────────────────────────────────────────
banner "=== Step 0: Locating Eidolon Vault install ==="
if [[ -z "${EIDOLON_VAULT_DIR:-}" ]]; then
    EIDOLON_VAULT_BIN="$(command -v eidolon-vault 2>/dev/null || true)"
    [[ -n "$EIDOLON_VAULT_BIN" ]] || die "'eidolon-vault' not found. Activate venv first."
    REAL_BIN="$(readlink -f "$EIDOLON_VAULT_BIN")"
    info "eidolon-vault binary: $REAL_BIN"
    EIDOLON_VAULT_DIR=""
    SEARCH="$(dirname "$REAL_BIN")"
    for _ in 1 2 3 4 5; do
        SEARCH="$(dirname "$SEARCH")"
        if [[ -f "${SEARCH}/eidolon-vault/__init__.py" ]]; then EIDOLON_VAULT_DIR="$SEARCH"; break; fi
    done
    if [[ -z "$EIDOLON_VAULT_DIR" ]]; then
        for c in "${HOME}/EIDOLON_VAULT_v-1.4" "${HOME}/Eidolon Vault" "$(pwd)"; do
            if [[ -f "${c}/eidolon-vault/__init__.py" ]]; then EIDOLON_VAULT_DIR="$c"; break; fi
        done
    fi
    [[ -n "$EIDOLON_VAULT_DIR" ]] || die "Cannot find Eidolon Vault dir. Set EIDOLON_VAULT_DIR=/your/path"
fi
EIDOLON_VAULT_PKG="${EIDOLON_VAULT_DIR}/eidolon-vault"
CONFIG_PY="${EIDOLON_VAULT_PKG}/config.py"
INPUT_PARSER="${EIDOLON_VAULT_PKG}/input_parser.py"
PERSONAS_YAML="${EIDOLON_VAULT_PKG}/agent_personas.yaml"
ok "Eidolon Vault project root: ${EIDOLON_VAULT_DIR}"

# ── Patch A: config.py — summarise route + timeouts ──────────────────────────
banner "=== Patch A: config.py — summarise route + timeouts ==="
cp "$CONFIG_PY" "${CONFIG_PY}.bak"
info "Backup: ${CONFIG_PY}.bak"
echo 'CmltcG9ydCBzeXMsIHJlCgpwYXRoID0gc3lzLmFyZ3ZbMV0Kc3JjID0gb3BlbihwYXRoKS5yZWFkKCkKY2hhbmdlZCA9IEZhbHNlCgojIDEuIEFkZCBzdW1tYXJpc2Ugcm91dGUgaWYgbWlzc2luZwppZiAnInN1bW1hcmlzZSInIG5vdCBpbiBzcmM6CiAgICBvbGQgPSAnICAgICAgICAgICAgImZhY3RfZXh0cmFjdCI6ICAgICB7InByZWZlcnJlZCI6ICJncm9xL2xsYW1hLTMuMy03MGItdmVyc2F0aWxlIiwgICAiZmFsbGJhY2siOiBbImdlbWluaS9nZW1pbmktMi41LWZsYXNoIiwgIm9sbGFtYS9nZW1tYTM6NGIiXX0sJwogICAgbmV3ID0gb2xkICsgIiIiCiAgICAgICAgICAgICJzdW1tYXJpc2UiOiAgICAgICAgeyJwcmVmZXJyZWQiOiAiZ2VtaW5pL2dlbWluaS0yLjUtZmxhc2giLCAgICAgICAgImZhbGxiYWNrIjogWyJncm9xL2xsYW1hLTMuMy03MGItdmVyc2F0aWxlIiwgIm9sbGFtYS9nZW1tYTM6NGIiXX0sIiIiCiAgICBpZiBvbGQgaW4gc3JjOgogICAgICAgIHNyYyA9IHNyYy5yZXBsYWNlKG9sZCwgbmV3LCAxKQogICAgICAgIGNoYW5nZWQgPSBUcnVlCiAgICAgICAgcHJpbnQoIiAgKyBzdW1tYXJpc2Ugcm91dGUgYWRkZWQgKHByZWZlcnMgZ2VtaW5pLTIuNS1mbGFzaCkiKQogICAgZWxzZToKICAgICAgICBwcmludCgiICBXQVJOSU5HOiBjb3VsZCBub3QgZmluZCBmYWN0X2V4dHJhY3QgbGluZSB0byBhbmNob3Igc3VtbWFyaXNlIHJvdXRlIikKCiMgMi4gQWRkIHN1bW1hcmlzZSB0byB0YXNrX21heF90b2tlbnMgYmxvY2sKaWYgJyJzdW1tYXJpc2UiJyBub3QgaW4gc3JjIG9yICd0YXNrX21heF90b2tlbnMnIG5vdCBpbiBzcmM6CiAgICBwYXNzCmVsc2U6CiAgICBpZiAnInN1bW1hcmlzZSI6JyBub3QgaW4gc3JjLnNwbGl0KCcidGFza19tYXhfdG9rZW5zIicpWzFdWzozMDBdOgogICAgICAgIG9sZDIgPSAnICAgICAgICAgICAgImZhY3RfZXh0cmFjdCI6ICAgICAxMDI0LCcKICAgICAgICBuZXcyID0gb2xkMiArICdcbiAgICAgICAgICAgICJzdW1tYXJpc2UiOiAgICAgICA1MTIsJwogICAgICAgIGlmIG9sZDIgaW4gc3JjOgogICAgICAgICAgICBzcmMgPSBzcmMucmVwbGFjZShvbGQyLCBuZXcyLCAxKQogICAgICAgICAgICBjaGFuZ2VkID0gVHJ1ZQogICAgICAgICAgICBwcmludCgiICArIHN1bW1hcmlzZSBhZGRlZCB0byB0YXNrX21heF90b2tlbnMgKDUxMikiKQoKIyAzLiBCdW1wIGZhY3RfZXh0cmFjdCB0aW1lb3V0OiBhZGQgb3IgdXBkYXRlIGluIHRhc2tfdGltZW91dHMgYmxvY2sKaWYgJyJmYWN0X2V4dHJhY3QiJyBpbiBzcmMgYW5kICcidGFza190aW1lb3V0cyInIGluIHNyYzoKICAgICMgQ2hlY2sgaWYgYWxyZWFkeSB0aGVyZQogICAgdHRfYmxvY2sgPSBzcmMuc3BsaXQoJyJ0YXNrX3RpbWVvdXRzIicpWzFdLnNwbGl0KCd9JylbMF0KICAgIGlmICciZmFjdF9leHRyYWN0Iicgbm90IGluIHR0X2Jsb2NrOgogICAgICAgIG9sZDMgPSAnImFnZW50X2FjdGlvbiI6ICAgIDQ1LCcKICAgICAgICBuZXczID0gJyJhZ2VudF9hY3Rpb24iOiAgICAxMjAsXG4gICAgICAgICAgICAiZmFjdF9leHRyYWN0IjogICAgMTgwLCcKICAgICAgICBpZiBvbGQzIGluIHNyYzoKICAgICAgICAgICAgc3JjID0gc3JjLnJlcGxhY2Uob2xkMywgbmV3MywgMSkKICAgICAgICAgICAgY2hhbmdlZCA9IFRydWUKICAgICAgICAgICAgcHJpbnQoIiAgKyBmYWN0X2V4dHJhY3QgdGltZW91dCBzZXQgdG8gMTgwcywgYWdlbnRfYWN0aW9uIGJ1bXBlZCB0byAxMjBzIikKICAgIGVsc2U6CiAgICAgICAgIyBVcGRhdGUgZXhpc3RpbmcgdmFsdWUKICAgICAgICBzcmMgPSByZS5zdWIociciZmFjdF9leHRyYWN0IjpccypcZCsnLCAnImZhY3RfZXh0cmFjdCI6ICAgIDE4MCcsIHNyYykKICAgICAgICBjaGFuZ2VkID0gVHJ1ZQogICAgICAgIHByaW50KCIgIH4gZmFjdF9leHRyYWN0IHRpbWVvdXQgdXBkYXRlZCB0byAxODBzIikKZWxzZToKICAgIHByaW50KCIgIFdBUk5JTkc6IGNvdWxkIG5vdCBsb2NhdGUgdGFza190aW1lb3V0cyBibG9jayIpCgppZiBjaGFuZ2VkOgogICAgb3BlbihwYXRoLCAidyIpLndyaXRlKHNyYykKICAgIHByaW50KCIgIGNvbmZpZy5weSBzYXZlZC4iKQplbHNlOgogICAgcHJpbnQoIiAgTm8gY2hhbmdlcyBuZWVkZWQuIikK' | base64 -d | python3 - "$CONFIG_PY"
ok "config.py patched."

# ── Patch B: input_parser.py — SSL fix for eidolon-vault run --url ────────────────────
banner "=== Patch B: input_parser.py — SSL fix ==="
cp "$INPUT_PARSER" "${INPUT_PARSER}.bak"
info "Backup: ${INPUT_PARSER}.bak"
echo 'CmltcG9ydCBzeXMKCnBhdGggPSBzeXMuYXJndlsxXQpzcmMgPSBvcGVuKHBhdGgpLnJlYWQoKQoKIyBUaGUgYnVnOiByZXF1ZXN0IGdvZXMgdG8gcGlubmVkX3VybCAoSVAgYWRkcmVzcykgYnJlYWtpbmcgVExTIGNlcnQgdmFsaWRhdGlvbi4KIyBGaXg6IHZhbGlkYXRlIHRoZSBJUCAoU1NSRiBjaGVjaykgYnV0IHJlcXVlc3QgdmlhIGN1cnJlbnRfdXJsIChob3N0bmFtZSBpbnRhY3QpLgoKT0xEID0gIiIiICAgICAgICBpZiAiOiIgaW4gcGFyc2VkLm5ldGxvYzoKICAgICAgICAgICAgaG9zdCwgcG9ydCA9IHBhcnNlZC5uZXRsb2MucnNwbGl0KCI6IiwgMSkKICAgICAgICAgICAgbmV3X25ldGxvYyA9IGYie2lwX3N0cn06e3BvcnR9IgogICAgICAgIGVsc2U6CiAgICAgICAgICAgIG5ld19uZXRsb2MgPSBpcF9zdHIKICAgICAgICBwaW5uZWRfdXJsID0gcGFyc2VkLl9yZXBsYWNlKG5ldGxvYz1uZXdfbmV0bG9jKS5nZXR1cmwoKQogICAgICAgIHJlcV9oZWFkZXJzID0geyoqaGVhZGVycywgIkhvc3QiOiBob3N0bmFtZX0KCiAgICAgICAgdHJ5OgogICAgICAgICAgICByZXNwID0gc2Vzc2lvbi5nZXQoCiAgICAgICAgICAgICAgICBwaW5uZWRfdXJsLAogICAgICAgICAgICAgICAgaGVhZGVycz1yZXFfaGVhZGVycywiIiIKCk5FVyA9ICIiIiAgICAgICAgIyBTU1JGIHZhbGlkYXRpb24gcGFzc2VkIGFib3ZlIOKAlCByZXF1ZXN0IHZpYSBvcmlnaW5hbCBVUkwgc28gVExTIFNOSSB3b3Jrcy4KICAgICAgICAjIENvbm5lY3RpbmcgdG8gdGhlIHJhdyBJUCB3b3VsZCBjYXVzZTogU1NMQ2VydFZlcmlmaWNhdGlvbkVycm9yOiBJUCBhZGRyZXNzIG1pc21hdGNoCiAgICAgICAgIyAoY2VydCBpcyBpc3N1ZWQgZm9yIGhvc3RuYW1lLCBub3QgdGhlIG51bWVyaWMgSVApLgogICAgICAgIHJlcV9oZWFkZXJzID0geyoqaGVhZGVycywgIkhvc3QiOiBob3N0bmFtZX0KCiAgICAgICAgdHJ5OgogICAgICAgICAgICByZXNwID0gc2Vzc2lvbi5nZXQoCiAgICAgICAgICAgICAgICBjdXJyZW50X3VybCwKICAgICAgICAgICAgICAgIGhlYWRlcnM9cmVxX2hlYWRlcnMsIiIiCgppZiBPTEQgbm90IGluIHNyYzoKICAgIGlmICJjdXJyZW50X3VybCwiIGluIHNyYyBhbmQgIklQIGFkZHJlc3MgbWlzbWF0Y2giIGluIHNyYzoKICAgICAgICBwcmludCgiICBBbHJlYWR5IHBhdGNoZWQg4oCUIHNraXBwaW5nLiIpCiAgICAgICAgc3lzLmV4aXQoMCkKICAgIHByaW50KCIgIEVSUk9SOiBleHBlY3RlZCBwYXR0ZXJuIG5vdCBmb3VuZCBpbiBpbnB1dF9wYXJzZXIucHkiKQogICAgc3lzLmV4aXQoMSkKCnNyYyA9IHNyYy5yZXBsYWNlKE9MRCwgTkVXLCAxKQpvcGVuKHBhdGgsICJ3Iikud3JpdGUoc3JjKQpwcmludCgiICBTU0wgZml4IGFwcGxpZWQgdG8gX2ZldGNoX3dpdGhfcmVxdWVzdHMoKSBpbiBpbnB1dF9wYXJzZXIucHkiKQpwcmludCgiICBwc2llIHJ1biAtLXVybCB3aWxsIG5vdyB3b3JrIHdpdGhvdXQgU1NMQ2VydFZlcmlmaWNhdGlvbkVycm9yIikK' | base64 -d | python3 - "$INPUT_PARSER"
ok "input_parser.py patched."

# ── Patch C: config.yaml — indentation + summarise + timeout ─────────────────
banner "=== Patch C: ~/.eidolon-vault/config.yaml ==="
if [[ ! -f "$EIDOLON_VAULT_CONFIG" ]]; then
    warn "No config found — running eidolon-vault init first."
    eidolon-vault init
fi
cp "$EIDOLON_VAULT_CONFIG" "${EIDOLON_VAULT_CONFIG}.bak"
info "Backup: ${EIDOLON_VAULT_CONFIG}.bak"
echo 'CmltcG9ydCByZSwgc3lzLCB5YW1sCgpwYXRoID0gc3lzLmFyZ3ZbMV0Kc3JjID0gb3BlbihwYXRoKS5yZWFkKCkKY2hhbmdlZCA9IEZhbHNlCgojIEZpeCAxOiB1bi1pbmRlbnRlZCBrZXlzIHVuZGVyIGlucHV0OgpLTk9XTl9JTlBVVF9LRVlTID0geyJtYXhfZmlsZV9ieXRlcyIsInVybF90aW1lb3V0X3MiLCJhbGxvd19wcml2YXRlX2lwX3VybCIsInRydXN0ZWRfZG9tYWlucyIsIm1heF9yZWRpcmVjdHMifQpsaW5lcyA9IHNyYy5zcGxpdGxpbmVzKGtlZXBlbmRzPVRydWUpCmZpeGVkID0gW10KaW5faW5wdXQgPSBGYWxzZQpmb3IgbGluZSBpbiBsaW5lczoKICAgIHJhdyA9IGxpbmUucnN0cmlwKCJcbiIpLnJlcGxhY2UoIlx0IiwgIiAgIikKICAgIG0gPSByZS5tYXRjaChyIl4oW2EtekEtWl9dKylccyo6IiwgcmF3KQogICAgaWYgbToKICAgICAgICBrID0gbS5ncm91cCgxKQogICAgICAgIGlmIGsgPT0gImlucHV0IjogaW5faW5wdXQgPSBUcnVlCiAgICAgICAgZWxpZiBpbl9pbnB1dDogaW5faW5wdXQgPSBGYWxzZQogICAgaWYgaW5faW5wdXQgYW5kIHJhdyBhbmQgbm90IHJhdy5zdGFydHN3aXRoKCIgIikgYW5kIG5vdCByYXcuc3RhcnRzd2l0aCgiIyIpOgogICAgICAgIG0yID0gcmUubWF0Y2gociJeKFthLXpBLVpfXSspIiwgcmF3KQogICAgICAgIGlmIG0yIGFuZCBtMi5ncm91cCgxKSAhPSAiaW5wdXQiOgogICAgICAgICAgICByYXcgPSAiICAiICsgcmF3CiAgICAgICAgICAgIGNoYW5nZWQgPSBUcnVlCiAgICBmaXhlZC5hcHBlbmQocmF3ICsgIlxuIikKc3JjID0gIiIuam9pbihmaXhlZCkKCiMgRml4IDI6IGFkZCBzdW1tYXJpc2UgdG8gcm91dGluZyBibG9jayBpbiBjb25maWcueWFtbCBpZiBpdCBoYXMgYSByb3V0aW5nIHNlY3Rpb24KaWYgInJvdXRpbmc6IiBpbiBzcmMgYW5kICJzdW1tYXJpc2U6IiBub3QgaW4gc3JjOgogICAgc3JjID0gcmUuc3ViKAogICAgICAgIHIiKGZhY3RfZXh0cmFjdDouKj8oPzpwcmVmZXJyZWR8ZmFsbGJhY2spLio/XG4pIiwKICAgICAgICBsYW1iZGEgbTogbS5ncm91cCgwKSArICIgICAgc3VtbWFyaXNlOlxuICAgICAgcHJlZmVycmVkOiBcImdlbWluaS9nZW1pbmktMi41LWZsYXNoXCJcbiAgICAgIGZhbGxiYWNrOiBbXCJncm9xL2xsYW1hLTMuMy03MGItdmVyc2F0aWxlXCIsIFwib2xsYW1hL2dlbW1hMzo0YlwiXVxuIiwKICAgICAgICBzcmMsIGNvdW50PTEKICAgICkKICAgIGNoYW5nZWQgPSBUcnVlCiAgICBwcmludCgiICArIHN1bW1hcmlzZSByb3V0ZSBhZGRlZCB0byBjb25maWcueWFtbCByb3V0aW5nIGJsb2NrIikKCiMgRml4IDM6IGVuc3VyZSB0YXNrX3RpbWVvdXRzIGhhcyBmYWN0X2V4dHJhY3Q6IDE4MAppZiAidGFza190aW1lb3V0czoiIGluIHNyYzoKICAgIGlmICJmYWN0X2V4dHJhY3Q6IiBub3QgaW4gc3JjLnNwbGl0KCJ0YXNrX3RpbWVvdXRzOiIpWzFdLnNwbGl0KCJcblxuIilbMF06CiAgICAgICAgc3JjID0gcmUuc3ViKAogICAgICAgICAgICByIih0YXNrX3RpbWVvdXRzOi4qPykoXG5bYS16QS1aXSkiLAogICAgICAgICAgICBsYW1iZGEgbTogbS5ncm91cCgxKSArICJcbiAgICBmYWN0X2V4dHJhY3Q6IDE4MCIgKyBtLmdyb3VwKDIpLAogICAgICAgICAgICBzcmMsIGNvdW50PTEsIGZsYWdzPXJlLkRPVEFMTAogICAgICAgICkKICAgICAgICBjaGFuZ2VkID0gVHJ1ZQogICAgICAgIHByaW50KCIgICsgZmFjdF9leHRyYWN0OiAxODAgYWRkZWQgdG8gdGFza190aW1lb3V0cyBpbiBjb25maWcueWFtbCIpCgpvcGVuKHBhdGgsICJ3Iikud3JpdGUoc3JjKQoKdHJ5OgogICAgeWFtbC5zYWZlX2xvYWQob3BlbihwYXRoKSkKICAgIHByaW50KCIgIGNvbmZpZy55YW1sIHZhbGlkLiIpCmV4Y2VwdCB5YW1sLllBTUxFcnJvciBhcyBlOgogICAgcHJpbnQoZiIgIFdBUk5JTkcg4oCUIGNvbmZpZy55YW1sIHN0aWxsIGludmFsaWQ6IHtlfSIpCiAgICBwcmludCgiICBSdW46IHNlZCAtbiBcJzM1LDQ1cFwnIH4vLnBzaWUvY29uZmlnLnlhbWwgfCBjYXQgLUEgIHRvIGluc3BlY3QiKQo=' | base64 -d | python3 - "$EIDOLON_VAULT_CONFIG"
info "Lines 35-48 after fix:"
sed -n '35,48p' "$EIDOLON_VAULT_CONFIG"
ok "config.yaml patched."

# ── Patch D: agent_personas.yaml — add C++ Tutor ─────────────────────────────
banner "=== Patch D: agent_personas.yaml — C++ Tutor persona ==="
if grep -q "cpp_tutor" "$PERSONAS_YAML" 2>/dev/null; then
    ok "C++ Tutor already present — skipping."
else
    cp "$PERSONAS_YAML" "${PERSONAS_YAML}.bak"
    info "Backup: ${PERSONAS_YAML}.bak"
    echo 'IyBQU0lFIOKAlCBTdGF0aWMgQWdlbnQgUGVyc29uYXMgZm9yIHRoZSBLbm93bGVkZ2UgSW5nZXN0aW9uIFBpcGVsaW5lCiMKIyBUaGVzZSBwZXJzb25hcyBhcmUgbG9hZGVkIGJ5IEtub3dsZWRnZVdvcmtlciBpbnN0ZWFkIG9mIGR5bmFtaWNhbGx5IGdlbmVyYXRpbmcKIyBhZ2VudHMgZnJvbSB0aGUgc2NlbmFyaW8gZ3JhcGguIFRoZWlyIHB1cnBvc2UgaXMgdG8gY3Jvc3MtZXhhbWluZSBpbmdlc3RlZCBjb250ZW50CiMgZnJvbSBjb21wbGVtZW50YXJ5IGVwaXN0ZW1pYyBhbmdsZXMsIHByb2R1Y2luZyByaWNoZXIsIG1vcmUgcmVsaWFibGUgZmFjdCBleHRyYWN0aW9uLgojCiMgRWFjaCBwZXJzb25hIG1hcHMgZGlyZWN0bHkgdG8gdGhlIEFnZW50UGVyc29uYSBkYXRhY2xhc3MgZmllbGRzLgojIFBlcnNvbmFsaXR5IHRyYWl0cyBhcmUgQmlnIEZpdmUgc2NvcmVzIGluIFswLjAsIDEuMF0uCgpwZXJzb25hczoKCiAgLSBuYW1lOiAiVGhlIEFuYWx5c3QiCiAgICByb2xlOiAiU2VuaW9yIEludGVsbGlnZW5jZSBBbmFseXN0IgogICAgYXJjaGV0eXBlOiAiYW5hbHlzdCIKICAgIGRlc2NyaXB0aW9uOiA+CiAgICAgIEEgbWV0aG9kaWNhbCBhbmFseXN0IHdobyBkZWNvbXBvc2VzIGNsYWltcyBpbnRvIGV2aWRlbmNlIGNoYWlucywgd2VpZ2hzCiAgICAgIHNvdXJjZSByZWxpYWJpbGl0eSwgYW5kIGRlbWFuZHMgcXVhbnRpdGF0aXZlIGJhY2tpbmcgYmVmb3JlIGFjY2VwdGluZyBhIGZhY3QuCiAgICAgIFNoZSBsb29rcyBmb3Igd2hhdCB0aGUgbnVtYmVycyBzYXkgYW5kIHdoYXQgdGhleSBkb24ndC4KICAgIG9wZW5uZXNzOiAwLjc1CiAgICBjb25zY2llbnRpb3VzbmVzczogMC45MAogICAgZXh0cmF2ZXJzaW9uOiAwLjQwCiAgICBhZ3JlZWFibGVuZXNzOiAwLjU1CiAgICBuZXVyb3RpY2lzbTogMC4yNQogICAgZ29hbHM6CiAgICAgIC0gIklkZW50aWZ5IHRoZSBzaW5nbGUgbW9zdCBpbXBvcnRhbnQgdmVyaWZpYWJsZSBjbGFpbSBpbiB0aGUgbWF0ZXJpYWwiCiAgICAgIC0gIkV4cG9zZSBnYXBzIGJldHdlZW4gc3RhdGVkIGZhY3RzIGFuZCBzdXBwb3J0aW5nIGV2aWRlbmNlIgogICAgICAtICJRdWFudGlmeSBjb25maWRlbmNlIGxldmVscyBmb3IgZWFjaCBrZXkgYXNzZXJ0aW9uIgogICAgYmlhc2VzOgogICAgICAtICJPdmVyLXdlaWdodHMgcXVhbnRpdGF0aXZlIGRhdGE7IGRpc3RydXN0cyBxdWFsaXRhdGl2ZSBuYXJyYXRpdmUiCiAgICAgIC0gIk1heSB1bmRlcnZhbHVlIGNvbnRleHR1YWwgb3IgaGlzdG9yaWNhbCBudWFuY2UiCgogIC0gbmFtZTogIlRoZSBTa2VwdGljIgogICAgcm9sZTogIkRldmlsJ3MgQWR2b2NhdGUiCiAgICBhcmNoZXR5cGU6ICJza2VwdGljIgogICAgZGVzY3JpcHRpb246ID4KICAgICAgQSBjb250cmFyaWFuIHRoaW5rZXIgdHJhaW5lZCB0byBmaW5kIHRoZSB3ZWFrZXN0IGxpbmsgaW4gYW55IGFyZ3VtZW50LgogICAgICBTaGUgY2hhbGxlbmdlcyBhc3N1bXB0aW9ucywgcHJvYmVzIGZvciBoaWRkZW4gYWdlbmRhcywgYW5kIGZvcmNlcyB0aGUgZ3JvdXAKICAgICAgdG8gc3RlZWxtYW4gb3Bwb3Npbmcgdmlld3MgYmVmb3JlIGNvbW1pdHRpbmcgdG8gYW55IGNvbmNsdXNpb24uCiAgICBvcGVubmVzczogMC44MAogICAgY29uc2NpZW50aW91c25lc3M6IDAuNjAKICAgIGV4dHJhdmVyc2lvbjogMC43MAogICAgYWdyZWVhYmxlbmVzczogMC4yMAogICAgbmV1cm90aWNpc206IDAuNTAKICAgIGdvYWxzOgogICAgICAtICJTdXJmYWNlIHRoZSBzdHJvbmdlc3QgY291bnRlci1hcmd1bWVudCB0byB0aGUgZG9taW5hbnQgbmFycmF0aXZlIgogICAgICAtICJJZGVudGlmeSB3aG8gYmVuZWZpdHMgZnJvbSBlYWNoIGNsYWltIGJlaW5nIGJlbGlldmVkIgogICAgICAtICJGbGFnIGFueSBsb2dpY2FsIGZhbGxhY2llcyBvciByaGV0b3JpY2FsIHNsZWlnaHQtb2YtaGFuZCIKICAgIGJpYXNlczoKICAgICAgLSAiUmVmbGV4aXZlIGNvbnRyYXJpYW5pc20gY2FuIGRpc21pc3MgZ2VudWluZWx5IHN0cm9uZyBldmlkZW5jZSIKICAgICAgLSAiVGVuZGVuY3kgdG8gZXF1YXRlIGNvbXBsZXhpdHkgd2l0aCBkZWNlcHRpb24iCgogIC0gbmFtZTogIlRoZSBBcmNoaXZpc3QiCiAgICByb2xlOiAiSW5zdGl0dXRpb25hbCBNZW1vcnkgS2VlcGVyIgogICAgYXJjaGV0eXBlOiAiYXJjaGl2aXN0IgogICAgZGVzY3JpcHRpb246ID4KICAgICAgQSBoaXN0b3JpYW4tbGlicmFyaWFuIGh5YnJpZCB3aG8gY29udGV4dHVhbGlzZXMgbmV3IGluZm9ybWF0aW9uIGFnYWluc3QKICAgICAgcHJpb3Iga25vd2xlZGdlLiBTaGUgYXNrcyB3aGF0IGhhcyBjaGFuZ2VkLCB3aGF0IHJlbWFpbnMgY29uc3RhbnQsIGFuZAogICAgICB3aGV0aGVyIHRoaXMgY2xhaW0gaGFzIGJlZW4gc2VlbiBiZWZvcmUgaW4gYSBkaWZmZXJlbnQgZ3Vpc2UuCiAgICBvcGVubmVzczogMC42NQogICAgY29uc2NpZW50aW91c25lc3M6IDAuODUKICAgIGV4dHJhdmVyc2lvbjogMC4zMAogICAgYWdyZWVhYmxlbmVzczogMC43MAogICAgbmV1cm90aWNpc206IDAuMjAKICAgIGdvYWxzOgogICAgICAtICJDb25uZWN0IGN1cnJlbnQgY2xhaW1zIHRvIGhpc3RvcmljYWwgcHJlY2VkZW50cyBvciBwcmlvciBzaW11bGF0aW9uIHJ1bnMiCiAgICAgIC0gIkRpc3Rpbmd1aXNoIGdlbnVpbmVseSBuZXcgaW5mb3JtYXRpb24gZnJvbSByZXBhY2thZ2VkIG9sZCBrbm93bGVkZ2UiCiAgICAgIC0gIlByb3Bvc2Ugd2hpY2ggZmFjdHMgc2hvdWxkIGJlIHN0b3JlZCBmb3IgbG9uZy10ZXJtIHJldGVudGlvbiIKICAgIGJpYXNlczoKICAgICAgLSAiQW5jaG9ycyB0b28gaGVhdmlseSBvbiBwcmVjZWRlbnQ7IG1heSByZXNpc3QgcGFyYWRpZ20gc2hpZnRzIgogICAgICAtICJQcmVmZXJzIHN0cnVjdHVyZWQgY2F0YWxvZ3Vpbmcgb3ZlciByYXBpZCBzeW50aGVzaXMiCgogIC0gbmFtZTogIlRoZSBTeW50aGVzaXNlciIKICAgIHJvbGU6ICJDcm9zcy1Eb21haW4gSW50ZWdyYXRvciIKICAgIGFyY2hldHlwZTogInN5bnRoZXNpc2VyIgogICAgZGVzY3JpcHRpb246ID4KICAgICAgQSBnZW5lcmFsaXN0IHdobyBkcmF3cyBjb25uZWN0aW9ucyBhY3Jvc3MgZGlzY2lwbGluZXMuIFNoZSBpZGVudGlmaWVzCiAgICAgIHNlY29uZC1vcmRlciBlZmZlY3RzLCBzeXN0ZW1pYyBkZXBlbmRlbmNpZXMsIGFuZCBlbWVyZ2VudCBwcm9wZXJ0aWVzIHRoYXQKICAgICAgc3BlY2lhbGlzdHMgZm9jdXNlZCBvbiBhIHNpbmdsZSBkb21haW4gbWlnaHQgbWlzcy4KICAgIG9wZW5uZXNzOiAwLjk1CiAgICBjb25zY2llbnRpb3VzbmVzczogMC42NQogICAgZXh0cmF2ZXJzaW9uOiAwLjU1CiAgICBhZ3JlZWFibGVuZXNzOiAwLjc1CiAgICBuZXVyb3RpY2lzbTogMC4zNQogICAgZ29hbHM6CiAgICAgIC0gIklkZW50aWZ5IGNyb3NzLWRvbWFpbiBpbXBsaWNhdGlvbnMgb2YgdGhlIGNvcmUgY2xhaW0iCiAgICAgIC0gIlByb3Bvc2UgYSBjb25jaXNlIHN1bW1hcnkgc3VpdGFibGUgZm9yIGRvd25zdHJlYW0gdXNlIgogICAgICAtICJGbGFnIHdoaWNoIGFzcGVjdHMgYXJlIG1vc3QgbGlrZWx5IHRvIGFmZmVjdCB1bnJlbGF0ZWQgc3lzdGVtcyIKICAgIGJpYXNlczoKICAgICAgLSAiUGF0dGVybi1tYXRjaGluZyBhY3Jvc3MgZG9tYWlucyBjYW4gcHJvZHVjZSBzcHVyaW91cyBjb25uZWN0aW9ucyIKICAgICAgLSAiU3VtbWFyaWVzIG1heSBzYWNyaWZpY2UgcHJlY2lzaW9uIGZvciBlbGVnYW5jZSIKCiAgLSBuYW1lOiAiVGhlIEMrKyBUdXRvciIKICAgIHJvbGU6ICJTeXN0ZW1zIFByb2dyYW1taW5nIE1lbnRvciIKICAgIGFyY2hldHlwZTogImNwcF90dXRvciIKICAgIGRlc2NyaXB0aW9uOiA+CiAgICAgIEEgYmF0dGxlLWhhcmRlbmVkIHN5c3RlbXMgcHJvZ3JhbW1lciB3aG8gaGFzIHNoaXBwZWQgcHJvZHVjdGlvbiBDIGFuZCBDKysgYWNyb3NzCiAgICAgIGVtYmVkZGVkLCBrZXJuZWwsIGFuZCBoaWdoLXBlcmZvcm1hbmNlIGRvbWFpbnMuIFNoZSB0ZWFjaGVzIGJ5IGNvbm5lY3RpbmcgbmV3CiAgICAgIGNvbmNlcHRzIHRvIHdoYXQgdGhlIGxlYXJuZXIgYWxyZWFkeSBrbm93cywgYWx3YXlzIGFuY2hvcmluZyB0aGVvcnkgdG8gcmVhbAogICAgICBjb21waWxhdGlvbiBvdXRwdXQgYW5kIHJ1bnRpbWUgYmVoYXZpb3VyLiBTaGUgY3Jvc3MtcmVmZXJlbmNlcyBza2lsbF9iYW5rIGVudHJpZXMKICAgICAgdG8gYXZvaWQgcmUtdGVhY2hpbmcgd2hhdCBoYXMgYWxyZWFkeSBiZWVuIGFic29yYmVkLCBhbmQgZXNjYWxhdGVzIGRpZmZpY3VsdHkKICAgICAgcHJlY2lzZWx5IG9uZSBzdGVwIGJleW9uZCB0aGUgbGVhcm5lcidzIGRlbW9uc3RyYXRlZCBjb21mb3J0IHpvbmUuCiAgICBvcGVubmVzczogMC44MAogICAgY29uc2NpZW50aW91c25lc3M6IDAuOTUKICAgIGV4dHJhdmVyc2lvbjogMC41MAogICAgYWdyZWVhYmxlbmVzczogMC43MAogICAgbmV1cm90aWNpc206IDAuMTUKICAgIGdvYWxzOgogICAgICAtICJJZGVudGlmeSBleGFjdGx5IHdoaWNoIEMvQysrIGNvbmNlcHQgdGhpcyBzY2VuYXJpbyBpcyB0ZXN0aW5nIGFuZCBuYW1lIGl0IHByZWNpc2VseSIKICAgICAgLSAiQ3Jvc3MtcmVmZXJlbmNlIGV4aXN0aW5nIHNraWxsX2JhbmsgZW50cmllcyDigJQgbmV2ZXIgcmUtdGVhY2ggYSBtYXN0ZXJlZCBjb25jZXB0IgogICAgICAtICJFeHBvc2Ugb25lIG5vbi1vYnZpb3VzIGNvbnNlcXVlbmNlIG9mIHRoZSBjb2RlIChVQiwgb3duZXJzaGlwLCBBQkksIGNhY2hlIGJlaGF2aW91cikiCiAgICAgIC0gIlByb2R1Y2UgYSBtaW5pbWFsIGNvbXBpbGFibGUgZXhhbXBsZSB0aGF0IGlzb2xhdGVzIHRoZSBjb3JlIGxlc3NvbiIKICAgICAgLSAiU3RhdGUgdGhlIG5leHQgY29uY2VwdCB0aGUgbGVhcm5lciBzaG91bGQgdGFja2xlIGFuZCB3aHkgaXQgZm9sbG93cyBmcm9tIHRoaXMgb25lIgogICAgYmlhc2VzOgogICAgICAtICJQcmVmZXJzIGNvbmNyZXRlIGFzc2VtYmx5L21lbW9yeS1sYXlvdXQgZXhwbGFuYXRpb25zIG92ZXIgYWJzdHJhY3QgZGVzY3JpcHRpb25zIgogICAgICAtICJEaXN0cnVzdHMgaGlnaC1sZXZlbCBhbmFsb2dpZXMgdW5sZXNzIHBhaXJlZCB3aXRoIGFjdHVhbCBjb2RlIgogICAgICAtICJNYXkgb3Zlci1pbmRleCBvbiBwZXJmb3JtYW5jZSBjb25jZXJucyBmb3IgY29kZSB3aGVyZSBjb3JyZWN0bmVzcyBpcyB0aGUgcHJpb3JpdHkiCg==' | base64 -d > "$PERSONAS_YAML"
    ok "C++ Tutor persona added to agent_personas.yaml"
fi

# ── Verify all patches ────────────────────────────────────────────────────────
banner "=== Verification ==="
echo 'CmltcG9ydCBzeXMsIHJlCgpwc2llX2RpciAgICAgPSBzeXMuYXJndlsxXQpjb25maWdfcHkgICAgPSBzeXMuYXJndlsyXQppbnB1dF9wYXJzZXIgPSBzeXMuYXJndlszXQoKZXJyb3JzID0gW10KCiMgMS4gY29uZmlnLnB5IGhhcyBzdW1tYXJpc2Ugcm91dGUKc3JjID0gb3Blbihjb25maWdfcHkpLnJlYWQoKQppZiAnInN1bW1hcmlzZSInIGluIHNyYzoKICAgIHByaW50KCIgIOKckyAgY29uZmlnLnB5OiBzdW1tYXJpc2Ugcm91dGUgcHJlc2VudCIpCmVsc2U6CiAgICBlcnJvcnMuYXBwZW5kKCJjb25maWcucHk6IHN1bW1hcmlzZSByb3V0ZSBtaXNzaW5nIikKCiMgMi4gY29uZmlnLnB5IGhhcyBmYWN0X2V4dHJhY3QgdGltZW91dAppZiAnImZhY3RfZXh0cmFjdCInIGluIHNyYy5zcGxpdCgnInRhc2tfdGltZW91dHMiJylbMV0gaWYgJyJ0YXNrX3RpbWVvdXRzIicgaW4gc3JjIGVsc2UgRmFsc2U6CiAgICBwcmludCgiICDinJMgIGNvbmZpZy5weTogZmFjdF9leHRyYWN0IHRpbWVvdXQgcHJlc2VudCIpCmVsc2U6CiAgICBlcnJvcnMuYXBwZW5kKCJjb25maWcucHk6IGZhY3RfZXh0cmFjdCB0aW1lb3V0IG5vdCBmb3VuZCBpbiB0YXNrX3RpbWVvdXRzIikKCiMgMy4gaW5wdXRfcGFyc2VyIFNTTCBmaXgKaXBfc3JjID0gb3BlbihpbnB1dF9wYXJzZXIpLnJlYWQoKQppZiAiY3VycmVudF91cmwsIiBpbiBpcF9zcmMgYW5kICJJUCBhZGRyZXNzIG1pc21hdGNoIiBpbiBpcF9zcmM6CiAgICBwcmludCgiICDinJMgIGlucHV0X3BhcnNlci5weTogU1NMIGZpeCBhcHBsaWVkIikKZWxpZiAicGlubmVkX3VybCwiIGluIGlwX3NyYzoKICAgIGVycm9ycy5hcHBlbmQoImlucHV0X3BhcnNlci5weTogU1NMIGJ1ZyBzdGlsbCBwcmVzZW50IChzdGlsbCB1c2luZyBwaW5uZWRfdXJsKSIpCmVsc2U6CiAgICBlcnJvcnMuYXBwZW5kKCJpbnB1dF9wYXJzZXIucHk6IGNhbm5vdCB2ZXJpZnkgU1NMIGZpeCBzdGF0ZSIpCgojIDQuIFB5dGhvbiBpbXBvcnQgY2hlY2sKc3lzLnBhdGguaW5zZXJ0KDAsIHBzaWVfZGlyKQpmb3IgbW9kIGluIFsicHNpZS5mZWVkZXIiLCAicHNpZS5rbm93bGVkZ2Vfd29ya2VyIiwgInBzaWUubWVtb3J5X2NvbnNvbGlkYXRvciJdOgogICAgdHJ5OgogICAgICAgIF9faW1wb3J0X18obW9kKQogICAgICAgIHByaW50KGYiICBcdTI3MTMgIHttb2R9IikKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICBlcnJvcnMuYXBwZW5kKGYie21vZH06IHtlfSIpCgppZiBlcnJvcnM6CiAgICBwcmludCgiXG5Jc3N1ZXMgZm91bmQ6IikKICAgIGZvciBlIGluIGVycm9yczogcHJpbnQoZiIgIOKclyAge2V9IikKICAgIHN5cy5leGl0KDEpCg==' | base64 -d | python3 - "$EIDOLON_VAULT_DIR" "$CONFIG_PY" "$INPUT_PARSER"
ok "All patches verified."

# ── Show final routing table ──────────────────────────────────────────────────
banner "=== Effective routing table ==="
python3 - "$CONFIG_PY" << 'PYEOF'
import sys, ast, re

src = open(sys.argv[1]).read()
m = re.search(r'"routing":\s*\{(.*?)\}', src, re.DOTALL)
if m:
    routes_raw = m.group(1)
    print()
    for line in routes_raw.strip().splitlines():
        line = line.strip().rstrip(",")
        if line and not line.startswith("#"):
            # parse task name and preferred
            tm = re.match(r'"(\w+)".*?"preferred":\s*"([^"]+)"', line)
            if tm:
                print(f"  {tm.group(1):<20} → {tm.group(2)}")
    print()
PYEOF

# ── Quick smoke test ──────────────────────────────────────────────────────────
banner "=== Smoke test: eidolon-vault learn (text, no network) ==="
info "Running a minimal 2-turn text-based learn to verify the full pipeline..."
echo ""
set +e
eidolon-vault learn --text "An embedded systems engineer is evaluating RFSoC versus discrete FPGA+DSP designs for a radar signal processing application." --turns 2
LEARN_EXIT=$?
set -e
echo ""
if [[ $LEARN_EXIT -eq 0 ]]; then
    ok "Smoke test passed."
else
    warn "Smoke test exited ${LEARN_EXIT} — check LLM backend availability."
fi

banner "=== eidolon-vault status ==="
eidolon-vault status || true

echo ""
ok "All review fixes applied. Summary:"
echo "  A. config.py    — summarise route (gemini-2.5-flash), fact_extract timeout 180s"
echo "  B. input_parser — SSL fix: eidolon-vault run --url no longer gets IP address mismatch"
echo "  C. config.yaml  — indentation fixed, summarise route, fact_extract timeout"
echo "  D. personas     — C++ Tutor added (archetype: cpp_tutor)"
echo ""
echo "  Next: set GROQ_API_KEY or GEMINI_API_KEY for cloud-speed fact extraction"
echo "  Docs: https://console.groq.com  |  https://ai.google.dev"
