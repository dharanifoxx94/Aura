#!/usr/bin/env bash
# =============================================================================
# PSIE Knowledge Pipeline — setup & smoke-test
#
# Fixes:
#   1. Writes agent_personas.yaml into psie/
#   2. Patches missing _escape_like() into memory_store.py
#   3. Fixes ~/.psie/config.yaml: un-indented keys under input:
#   4. psie learn --url <TEST_URL> --turns 3
#   5. psie status
#
# Usage:
#   bash setup_and_test.sh
#   TEST_URL=https://other-url bash setup_and_test.sh
#   PSIE_DIR=/custom/path bash setup_and_test.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()   { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()     { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()   { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()    { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
banner() { echo -e "\n${BOLD}$*${RESET}"; }

TEST_URL="${TEST_URL:-https://www.hackster.io/adam-taylor/understanding-rfsoc-bbf149}"
PSIE_CONFIG="${HOME}/.psie/config.yaml"

# ── Step 0: Locate PSIE install ───────────────────────────────────────────────
banner "=== Step 0: Locating PSIE install ==="

if [[ -z "${PSIE_DIR:-}" ]]; then
    PSIE_BIN="$(command -v psie 2>/dev/null || true)"
    [[ -n "$PSIE_BIN" ]] || die "'psie' not found on PATH. Activate your venv first."
    REAL_BIN="$(readlink -f "$PSIE_BIN")"
    info "psie binary: $REAL_BIN"
    PSIE_DIR=""
    SEARCH="$(dirname "$REAL_BIN")"
    for _ in 1 2 3 4 5; do
        SEARCH="$(dirname "$SEARCH")"
        if [[ -f "${SEARCH}/psie/__init__.py" ]]; then PSIE_DIR="$SEARCH"; break; fi
    done
    if [[ -z "$PSIE_DIR" ]]; then
        for candidate in "${HOME}/PSIE_v-1.4" "${HOME}/PSIE" "$(pwd)"; do
            if [[ -f "${candidate}/psie/__init__.py" ]]; then PSIE_DIR="$candidate"; break; fi
        done
    fi
    [[ -n "$PSIE_DIR" ]] || die "Cannot find PSIE project dir. Set PSIE_DIR=/your/path"
fi

PSIE_PKG="${PSIE_DIR}/psie"
[[ -f "${PSIE_PKG}/__init__.py" ]] || die "psie package not found at ${PSIE_PKG}"
ok "PSIE project root: ${PSIE_DIR}"

# ── Step 1: Write agent_personas.yaml ─────────────────────────────────────────
banner "=== Step 1: Writing agent_personas.yaml ==="
echo 'IyBQU0lFIOKAlCBTdGF0aWMgQWdlbnQgUGVyc29uYXMgZm9yIHRoZSBLbm93bGVkZ2UgSW5nZXN0aW9uIFBpcGVsaW5lCiMKIyBUaGVzZSBwZXJzb25hcyBhcmUgbG9hZGVkIGJ5IEtub3dsZWRnZVdvcmtlciBpbnN0ZWFkIG9mIGR5bmFtaWNhbGx5IGdlbmVyYXRpbmcKIyBhZ2VudHMgZnJvbSB0aGUgc2NlbmFyaW8gZ3JhcGguIFRoZWlyIHB1cnBvc2UgaXMgdG8gY3Jvc3MtZXhhbWluZSBpbmdlc3RlZCBjb250ZW50CiMgZnJvbSBjb21wbGVtZW50YXJ5IGVwaXN0ZW1pYyBhbmdsZXMsIHByb2R1Y2luZyByaWNoZXIsIG1vcmUgcmVsaWFibGUgZmFjdCBleHRyYWN0aW9uLgojCiMgRWFjaCBwZXJzb25hIG1hcHMgZGlyZWN0bHkgdG8gdGhlIEFnZW50UGVyc29uYSBkYXRhY2xhc3MgZmllbGRzLgojIFBlcnNvbmFsaXR5IHRyYWl0cyBhcmUgQmlnIEZpdmUgc2NvcmVzIGluIFswLjAsIDEuMF0uCgpwZXJzb25hczoKCiAgLSBuYW1lOiAiVGhlIEFuYWx5c3QiCiAgICByb2xlOiAiU2VuaW9yIEludGVsbGlnZW5jZSBBbmFseXN0IgogICAgYXJjaGV0eXBlOiAiYW5hbHlzdCIKICAgIGRlc2NyaXB0aW9uOiA+CiAgICAgIEEgbWV0aG9kaWNhbCBhbmFseXN0IHdobyBkZWNvbXBvc2VzIGNsYWltcyBpbnRvIGV2aWRlbmNlIGNoYWlucywgd2VpZ2hzCiAgICAgIHNvdXJjZSByZWxpYWJpbGl0eSwgYW5kIGRlbWFuZHMgcXVhbnRpdGF0aXZlIGJhY2tpbmcgYmVmb3JlIGFjY2VwdGluZyBhIGZhY3QuCiAgICAgIFNoZSBsb29rcyBmb3Igd2hhdCB0aGUgbnVtYmVycyBzYXkgYW5kIHdoYXQgdGhleSBkb24ndC4KICAgIG9wZW5uZXNzOiAwLjc1CiAgICBjb25zY2llbnRpb3VzbmVzczogMC45MAogICAgZXh0cmF2ZXJzaW9uOiAwLjQwCiAgICBhZ3JlZWFibGVuZXNzOiAwLjU1CiAgICBuZXVyb3RpY2lzbTogMC4yNQogICAgZ29hbHM6CiAgICAgIC0gIklkZW50aWZ5IHRoZSBzaW5nbGUgbW9zdCBpbXBvcnRhbnQgdmVyaWZpYWJsZSBjbGFpbSBpbiB0aGUgbWF0ZXJpYWwiCiAgICAgIC0gIkV4cG9zZSBnYXBzIGJldHdlZW4gc3RhdGVkIGZhY3RzIGFuZCBzdXBwb3J0aW5nIGV2aWRlbmNlIgogICAgICAtICJRdWFudGlmeSBjb25maWRlbmNlIGxldmVscyBmb3IgZWFjaCBrZXkgYXNzZXJ0aW9uIgogICAgYmlhc2VzOgogICAgICAtICJPdmVyLXdlaWdodHMgcXVhbnRpdGF0aXZlIGRhdGE7IGRpc3RydXN0cyBxdWFsaXRhdGl2ZSBuYXJyYXRpdmUiCiAgICAgIC0gIk1heSB1bmRlcnZhbHVlIGNvbnRleHR1YWwgb3IgaGlzdG9yaWNhbCBudWFuY2UiCgogIC0gbmFtZTogIlRoZSBTa2VwdGljIgogICAgcm9sZTogIkRldmlsJ3MgQWR2b2NhdGUiCiAgICBhcmNoZXR5cGU6ICJza2VwdGljIgogICAgZGVzY3JpcHRpb246ID4KICAgICAgQSBjb250cmFyaWFuIHRoaW5rZXIgdHJhaW5lZCB0byBmaW5kIHRoZSB3ZWFrZXN0IGxpbmsgaW4gYW55IGFyZ3VtZW50LgogICAgICBTaGUgY2hhbGxlbmdlcyBhc3N1bXB0aW9ucywgcHJvYmVzIGZvciBoaWRkZW4gYWdlbmRhcywgYW5kIGZvcmNlcyB0aGUgZ3JvdXAKICAgICAgdG8gc3RlZWxtYW4gb3Bwb3Npbmcgdmlld3MgYmVmb3JlIGNvbW1pdHRpbmcgdG8gYW55IGNvbmNsdXNpb24uCiAgICBvcGVubmVzczogMC44MAogICAgY29uc2NpZW50aW91c25lc3M6IDAuNjAKICAgIGV4dHJhdmVyc2lvbjogMC43MAogICAgYWdyZWVhYmxlbmVzczogMC4yMAogICAgbmV1cm90aWNpc206IDAuNTAKICAgIGdvYWxzOgogICAgICAtICJTdXJmYWNlIHRoZSBzdHJvbmdlc3QgY291bnRlci1hcmd1bWVudCB0byB0aGUgZG9taW5hbnQgbmFycmF0aXZlIgogICAgICAtICJJZGVudGlmeSB3aG8gYmVuZWZpdHMgZnJvbSBlYWNoIGNsYWltIGJlaW5nIGJlbGlldmVkIgogICAgICAtICJGbGFnIGFueSBsb2dpY2FsIGZhbGxhY2llcyBvciByaGV0b3JpY2FsIHNsZWlnaHQtb2YtaGFuZCIKICAgIGJpYXNlczoKICAgICAgLSAiUmVmbGV4aXZlIGNvbnRyYXJpYW5pc20gY2FuIGRpc21pc3MgZ2VudWluZWx5IHN0cm9uZyBldmlkZW5jZSIKICAgICAgLSAiVGVuZGVuY3kgdG8gZXF1YXRlIGNvbXBsZXhpdHkgd2l0aCBkZWNlcHRpb24iCgogIC0gbmFtZTogIlRoZSBBcmNoaXZpc3QiCiAgICByb2xlOiAiSW5zdGl0dXRpb25hbCBNZW1vcnkgS2VlcGVyIgogICAgYXJjaGV0eXBlOiAiYXJjaGl2aXN0IgogICAgZGVzY3JpcHRpb246ID4KICAgICAgQSBoaXN0b3JpYW4tbGlicmFyaWFuIGh5YnJpZCB3aG8gY29udGV4dHVhbGlzZXMgbmV3IGluZm9ybWF0aW9uIGFnYWluc3QKICAgICAgcHJpb3Iga25vd2xlZGdlLiBTaGUgYXNrcyB3aGF0IGhhcyBjaGFuZ2VkLCB3aGF0IHJlbWFpbnMgY29uc3RhbnQsIGFuZAogICAgICB3aGV0aGVyIHRoaXMgY2xhaW0gaGFzIGJlZW4gc2VlbiBiZWZvcmUgaW4gYSBkaWZmZXJlbnQgZ3Vpc2UuCiAgICBvcGVubmVzczogMC42NQogICAgY29uc2NpZW50aW91c25lc3M6IDAuODUKICAgIGV4dHJhdmVyc2lvbjogMC4zMAogICAgYWdyZWVhYmxlbmVzczogMC43MAogICAgbmV1cm90aWNpc206IDAuMjAKICAgIGdvYWxzOgogICAgICAtICJDb25uZWN0IGN1cnJlbnQgY2xhaW1zIHRvIGhpc3RvcmljYWwgcHJlY2VkZW50cyBvciBwcmlvciBzaW11bGF0aW9uIHJ1bnMiCiAgICAgIC0gIkRpc3Rpbmd1aXNoIGdlbnVpbmVseSBuZXcgaW5mb3JtYXRpb24gZnJvbSByZXBhY2thZ2VkIG9sZCBrbm93bGVkZ2UiCiAgICAgIC0gIlByb3Bvc2Ugd2hpY2ggZmFjdHMgc2hvdWxkIGJlIHN0b3JlZCBmb3IgbG9uZy10ZXJtIHJldGVudGlvbiIKICAgIGJpYXNlczoKICAgICAgLSAiQW5jaG9ycyB0b28gaGVhdmlseSBvbiBwcmVjZWRlbnQ7IG1heSByZXNpc3QgcGFyYWRpZ20gc2hpZnRzIgogICAgICAtICJQcmVmZXJzIHN0cnVjdHVyZWQgY2F0YWxvZ3Vpbmcgb3ZlciByYXBpZCBzeW50aGVzaXMiCgogIC0gbmFtZTogIlRoZSBTeW50aGVzaXNlciIKICAgIHJvbGU6ICJDcm9zcy1Eb21haW4gSW50ZWdyYXRvciIKICAgIGFyY2hldHlwZTogInN5bnRoZXNpc2VyIgogICAgZGVzY3JpcHRpb246ID4KICAgICAgQSBnZW5lcmFsaXN0IHdobyBkcmF3cyBjb25uZWN0aW9ucyBhY3Jvc3MgZGlzY2lwbGluZXMuIFNoZSBpZGVudGlmaWVzCiAgICAgIHNlY29uZC1vcmRlciBlZmZlY3RzLCBzeXN0ZW1pYyBkZXBlbmRlbmNpZXMsIGFuZCBlbWVyZ2VudCBwcm9wZXJ0aWVzIHRoYXQKICAgICAgc3BlY2lhbGlzdHMgZm9jdXNlZCBvbiBhIHNpbmdsZSBkb21haW4gbWlnaHQgbWlzcy4KICAgIG9wZW5uZXNzOiAwLjk1CiAgICBjb25zY2llbnRpb3VzbmVzczogMC42NQogICAgZXh0cmF2ZXJzaW9uOiAwLjU1CiAgICBhZ3JlZWFibGVuZXNzOiAwLjc1CiAgICBuZXVyb3RpY2lzbTogMC4zNQogICAgZ29hbHM6CiAgICAgIC0gIklkZW50aWZ5IGNyb3NzLWRvbWFpbiBpbXBsaWNhdGlvbnMgb2YgdGhlIGNvcmUgY2xhaW0iCiAgICAgIC0gIlByb3Bvc2UgYSBjb25jaXNlIHN1bW1hcnkgc3VpdGFibGUgZm9yIGRvd25zdHJlYW0gdXNlIgogICAgICAtICJGbGFnIHdoaWNoIGFzcGVjdHMgYXJlIG1vc3QgbGlrZWx5IHRvIGFmZmVjdCB1bnJlbGF0ZWQgc3lzdGVtcyIKICAgIGJpYXNlczoKICAgICAgLSAiUGF0dGVybi1tYXRjaGluZyBhY3Jvc3MgZG9tYWlucyBjYW4gcHJvZHVjZSBzcHVyaW91cyBjb25uZWN0aW9ucyIKICAgICAgLSAiU3VtbWFyaWVzIG1heSBzYWNyaWZpY2UgcHJlY2lzaW9uIGZvciBlbGVnYW5jZSIK' | base64 -d > "${PSIE_PKG}/agent_personas.yaml"
ok "Written: ${PSIE_PKG}/agent_personas.yaml"

# ── Step 2: Patch _escape_like into memory_store.py ───────────────────────────
banner "=== Step 2: Patching memory_store.py (_escape_like) ==="
MEMORY_STORE="${PSIE_PKG}/memory_store.py"
if grep -q "^def _escape_like" "$MEMORY_STORE"; then
    ok "_escape_like already defined — skipping."
else
    cp "$MEMORY_STORE" "${MEMORY_STORE}.bak"
    info "Backup: ${MEMORY_STORE}.bak"
    echo 'CgpkZWYgX2VzY2FwZV9saWtlKHM6IHN0cikgLT4gc3RyOgogICAgIiIiRXNjYXBlIFNRTGl0ZSBMSUtFIHdpbGRjYXJkcyDigJQgd2FzIG1pc3NpbmcgZnJvbSBtZW1vcnlfc3RvcmUucHkuIiIiCiAgICByZXR1cm4gcy5yZXBsYWNlKCJcXCIsICJcXFxcIikucmVwbGFjZSgiJSIsICJcXCUiKS5yZXBsYWNlKCJfIiwgIlxcXyIpCg==' | base64 -d >> "$MEMORY_STORE"
    echo 'aW1wb3J0IHN5cywgcmUKCnBhdGggPSBzeXMuYXJndlsxXQpzcmMgPSBvcGVuKHBhdGgpLnJlYWQoKQoKIyBDb25maXJtIHRoZSBmdW5jdGlvbiB3YXMgYXBwZW5kZWQKYXNzZXJ0ICJkZWYgX2VzY2FwZV9saWtlIiBpbiBzcmMsICJfZXNjYXBlX2xpa2Ugbm90IGZvdW5kIGluIGZpbGUgYWZ0ZXIgcGF0Y2giCgojIEV4dHJhY3QganVzdCB0aGUgZnVuY3Rpb24gZGVmaW5pdGlvbiBhbmQgZXhlYyBpdCBpbiBhIGNsZWFuIG5hbWVzcGFjZQptID0gcmUuc2VhcmNoKHIiKGRlZiBfZXNjYXBlX2xpa2VcKC4qPykoPz0KZGVmIHxcWikiLCBzcmMsIHJlLkRPVEFMTCkKYXNzZXJ0IG0sICJDb3VsZCBub3QgZXh0cmFjdCBfZXNjYXBlX2xpa2UgYm9keSIKbnMgPSB7fQpleGVjKG0uZ3JvdXAoMSksIG5zKQpmbiA9IG5zWyJfZXNjYXBlX2xpa2UiXQphc3NlcnQgZm4oImhlbGxvIikgICA9PSAiaGVsbG8iLCAgICAgIGYicGxhaW4gYnJva2VuOiB7Zm4oJ2hlbGxvJykhcn0iCmFzc2VydCBmbigiNTAlIikgICAgID09ICI1MFxcJSIsICAgIGYiJSUgYnJva2VuOiB7Zm4oJzUwJScpIXJ9Igphc3NlcnQgZm4oImZvb19iYXIiKSA9PSAiZm9vXFxfYmFyIixmIl8gYnJva2VuOiB7Zm4oJ2Zvb19iYXInKSFyfSIKcHJpbnQoIiAgX2VzY2FwZV9saWtlIHZlcmlmaWVkIE9LIikK' | base64 -d | python3 - "$MEMORY_STORE"
    ok "memory_store.py patched and verified."
fi

# ── Step 3: Fix ~/.psie/config.yaml ───────────────────────────────────────────
banner "=== Step 3: Fixing ~/.psie/config.yaml ==="
if [[ ! -f "$PSIE_CONFIG" ]]; then
    warn "No config found — running psie init."
    psie init
else
    info "Lines 35-45 (before fix):"
    sed -n '35,45p' "$PSIE_CONFIG" | cat -A
    cp "$PSIE_CONFIG" "${PSIE_CONFIG}.bak"
    info "Backup: ${PSIE_CONFIG}.bak"
    echo 'aW1wb3J0IHJlLCBzeXMKS05PV04gPSB7Im1heF9maWxlX2J5dGVzIiwidXJsX3RpbWVvdXRfcyIsImFsbG93X3ByaXZhdGVfaXBfdXJsIiwidHJ1c3RlZF9kb21haW5zIiwibWF4X3JlZGlyZWN0cyJ9CnBhdGggPSBzeXMuYXJndlsxXQpsaW5lcyA9IG9wZW4ocGF0aCkucmVhZGxpbmVzKCkKZml4ZWQgPSBbXQppbl9pbnB1dCA9IEZhbHNlCmZvciBsaW5lIGluIGxpbmVzOgogICAgcmF3ID0gbGluZS5yc3RyaXAoIlxuIikucmVwbGFjZSgiXHQiLCAiICAiKQogICAgbSA9IHJlLm1hdGNoKHIiXihbYS16QS1aX10rKVxzKjoiLCByYXcpCiAgICBpZiBtOgogICAgICAgIGsgPSBtLmdyb3VwKDEpCiAgICAgICAgaWYgayA9PSAiaW5wdXQiOiBpbl9pbnB1dCA9IFRydWUKICAgICAgICBlbGlmIGluX2lucHV0OiBpbl9pbnB1dCA9IEZhbHNlCiAgICBpZiBpbl9pbnB1dCBhbmQgcmF3IGFuZCBub3QgcmF3LnN0YXJ0c3dpdGgoIiAiKSBhbmQgbm90IHJhdy5zdGFydHN3aXRoKCIjIik6CiAgICAgICAgbTIgPSByZS5tYXRjaChyIl4oW2EtekEtWl9dKykiLCByYXcpCiAgICAgICAgaWYgbTIgYW5kIG0yLmdyb3VwKDEpICE9ICJpbnB1dCI6CiAgICAgICAgICAgIHJhdyA9ICIgICIgKyByYXcKICAgIGZpeGVkLmFwcGVuZChyYXcgKyAiXG4iKQpvcGVuKHBhdGgsICJ3Iikud3JpdGVsaW5lcyhmaXhlZCkKcHJpbnQoIiAgU3RydWN0dXJhbCBmaXggYXBwbGllZC4iKQo=' | base64 -d | python3 - "$PSIE_CONFIG"
    YAML_STATUS="$(echo 'aW1wb3J0IHlhbWwsIHN5cwp0cnk6CiAgICB5YW1sLnNhZmVfbG9hZChvcGVuKHN5cy5hcmd2WzFdKSkKICAgIHByaW50KCJPSyIpCmV4Y2VwdCB5YW1sLllBTUxFcnJvciBhcyBlOgogICAgcHJpbnQoc3RyKGUpKQo=' | base64 -d | python3 - "$PSIE_CONFIG")"
    if [[ "$YAML_STATUS" == "OK" ]]; then
        ok "config.yaml parses cleanly."
        info "Lines 35-45 after fix:"
        sed -n '35,45p' "$PSIE_CONFIG"
    else
        warn "Still invalid: $YAML_STATUS"
        info "Lines 35-45 with hidden chars:"
        sed -n '35,45p' "$PSIE_CONFIG" | cat -A
        warn "Restoring backup — psie will fall back to defaults."
        cp "${PSIE_CONFIG}.bak" "$PSIE_CONFIG"
    fi
fi

# ── Step 4: Verify imports ────────────────────────────────────────────────────
banner "=== Step 4: Verifying Python imports ==="
echo 'aW1wb3J0IHN5cwpzeXMucGF0aC5pbnNlcnQoMCwgc3lzLmFyZ3ZbMV0pCmZhaWxlZCA9IFtdCmZvciBtb2QgaW4gWyJwc2llLmZlZWRlciIsICJwc2llLmtub3dsZWRnZV93b3JrZXIiLCAicHNpZS5tZW1vcnlfY29uc29saWRhdG9yIl06CiAgICB0cnk6CiAgICAgICAgX19pbXBvcnRfXyhtb2QpCiAgICAgICAgcHJpbnQoZiIgIFx1MjcxMyAge21vZH0iKQogICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBlOgogICAgICAgIHByaW50KGYiICBcdTI3MTcgIHttb2R9OiB7ZX0iKQogICAgICAgIGZhaWxlZC5hcHBlbmQobW9kKQppZiBmYWlsZWQ6CiAgICBzeXMuZXhpdCgxKQo=' | base64 -d | python3 - "${PSIE_DIR}"
ok "All pipeline modules import OK."

# ── Step 5: psie learn ────────────────────────────────────────────────────────
banner "=== Step 5: psie learn --url '${TEST_URL}' --turns 3 ==="
info "Making real LLM calls — expect ~1-3 minutes."
echo ""
set +e
psie learn --url "$TEST_URL" --turns 3
LEARN_EXIT=$?
set -e
echo ""
if [[ $LEARN_EXIT -eq 0 ]]; then
    ok "psie learn succeeded."
else
    warn "psie learn exited with code ${LEARN_EXIT} — check output above."
fi

# ── Step 6: psie status ───────────────────────────────────────────────────────
banner "=== Step 6: psie status ==="
psie status || true
echo ""
ok "All done."
