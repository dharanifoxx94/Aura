#!/usr/bin/env bash
# =============================================================================
# PSIE — mitigate timeout / unpack bugs and smoke-test
#
# Fixes:
#   1. Repairs config.yaml indentation (un-indented keys under input:)
#   2. Patches memory_store.py: "too many values to unpack" in fact_extract
#   3. Raises task timeouts (fact_extract→180s, agent_action→120s)
#   4. Lowers retry_attempts to 1 / retry_delay to 1s (less waiting on fail)
#   5. Detects available Ollama model and patches config to use it
#   6. Smoke-tests: psie learn --url <TEST_URL> --turns 3
#
# Usage:
#   bash fix_and_test.sh
#   TEST_URL=https://other-url bash fix_and_test.sh
#   PSIE_DIR=/custom/path bash fix_and_test.sh
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

# ── Locate PSIE ───────────────────────────────────────────────────────────────
banner "=== Step 0: Locating PSIE install ==="

if [[ -z "${PSIE_DIR:-}" ]]; then
    PSIE_BIN="$(command -v psie 2>/dev/null || true)"
    [[ -n "$PSIE_BIN" ]] || die "'psie' not found on PATH. Activate venv first."
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
    [[ -n "$PSIE_DIR" ]] || die "Cannot find PSIE dir. Set PSIE_DIR=/your/path"
fi

PSIE_PKG="${PSIE_DIR}/psie"
MEMORY_STORE="${PSIE_PKG}/memory_store.py"
[[ -f "${PSIE_PKG}/__init__.py" ]] || die "psie package not found at ${PSIE_PKG}"
ok "PSIE project root: ${PSIE_DIR}"

# ── Fix 1: config.yaml indentation ───────────────────────────────────────────
banner "=== Fix 1: config.yaml indentation ==="
[[ -f "$PSIE_CONFIG" ]] || { warn "No config — running psie init."; psie init; }
cp "$PSIE_CONFIG" "${PSIE_CONFIG}.bak2"
info "Backup: ${PSIE_CONFIG}.bak2"
echo 'CmltcG9ydCByZSwgc3lzLCB5YW1sCgpwYXRoID0gc3lzLmFyZ3ZbMV0KCiMgQ2hlY2sgaWYgYWxyZWFkeSB2YWxpZAp0cnk6CiAgICB5YW1sLnNhZmVfbG9hZChvcGVuKHBhdGgpKQogICAgcHJpbnQoIiAgY29uZmlnLnlhbWwgYWxyZWFkeSB2YWxpZCDigJQgc2tpcHBpbmcgaW5kZW50IGZpeC4iKQogICAgc3lzLmV4aXQoMCkKZXhjZXB0IHlhbWwuWUFNTEVycm9yOgogICAgcGFzcwoKbGluZXMgPSBvcGVuKHBhdGgpLnJlYWRsaW5lcygpCmZpeGVkID0gW10KaW5faW5wdXQgPSBGYWxzZQpLTk9XTiA9IHsibWF4X2ZpbGVfYnl0ZXMiLCJ1cmxfdGltZW91dF9zIiwiYWxsb3dfcHJpdmF0ZV9pcF91cmwiLCJ0cnVzdGVkX2RvbWFpbnMiLCJtYXhfcmVkaXJlY3RzIn0KCmZvciBsaW5lIGluIGxpbmVzOgogICAgcmF3ID0gbGluZS5yc3RyaXAoIlxuIikucmVwbGFjZSgiXHQiLCAiICAiKQogICAgbSA9IHJlLm1hdGNoKHIiXihbYS16QS1aX10rKVxzKjoiLCByYXcpCiAgICBpZiBtOgogICAgICAgIGsgPSBtLmdyb3VwKDEpCiAgICAgICAgaWYgayA9PSAiaW5wdXQiOgogICAgICAgICAgICBpbl9pbnB1dCA9IFRydWUKICAgICAgICBlbGlmIGluX2lucHV0OgogICAgICAgICAgICBpbl9pbnB1dCA9IEZhbHNlCiAgICBpZiBpbl9pbnB1dCBhbmQgcmF3IGFuZCBub3QgcmF3LnN0YXJ0c3dpdGgoIiAiKSBhbmQgbm90IHJhdy5zdGFydHN3aXRoKCIjIik6CiAgICAgICAgbTIgPSByZS5tYXRjaChyIl4oW2EtekEtWl9dKykiLCByYXcpCiAgICAgICAgaWYgbTIgYW5kIG0yLmdyb3VwKDEpICE9ICJpbnB1dCI6CiAgICAgICAgICAgIHJhdyA9ICIgICIgKyByYXcKICAgIGZpeGVkLmFwcGVuZChyYXcgKyAiXG4iKQoKb3BlbihwYXRoLCAidyIpLndyaXRlbGluZXMoZml4ZWQpCgp0cnk6CiAgICB5YW1sLnNhZmVfbG9hZChvcGVuKHBhdGgpKQogICAgcHJpbnQoIiAgSW5kZW50IGZpeCBhcHBsaWVkIOKAlCBjb25maWcueWFtbCBub3cgdmFsaWQuIikKZXhjZXB0IHlhbWwuWUFNTEVycm9yIGFzIGU6CiAgICBwcmludChmIiAgU3RpbGwgaW52YWxpZCBhZnRlciBpbmRlbnQgZml4OiB7ZX0iKQo=' | base64 -d | python3 - "$PSIE_CONFIG"
ok "Indentation check done."

# ── Fix 2: memory_store.py unpack bug ────────────────────────────────────────
banner "=== Fix 2: memory_store.py — unpack bug ==="
cp "$MEMORY_STORE" "${MEMORY_STORE}.bak2"
info "Backup: ${MEMORY_STORE}.bak2"
echo 'CmltcG9ydCBzeXMKcGF0aCA9IHN5cy5hcmd2WzFdCnNyYyA9IG9wZW4ocGF0aCkucmVhZCgpCm9sZCA9ICdyYXcsIF90b2tlbnMgPSBnYXRld2F5LmNvbXBsZXRlKCJmYWN0X2V4dHJhY3QiLCBtZXNzYWdlcywganNvbl9tb2RlPVRydWUpJwpuZXcgPSAncmF3ID0gZ2F0ZXdheS5jb21wbGV0ZSgiZmFjdF9leHRyYWN0IiwgbWVzc2FnZXMsIGpzb25fbW9kZT1UcnVlKScKaWYgb2xkIG5vdCBpbiBzcmM6CiAgICBpZiBuZXcgaW4gc3JjOgogICAgICAgIHByaW50KCIgIEFscmVhZHkgcGF0Y2hlZCDigJQgc2tpcHBpbmcuIikKICAgIGVsc2U6CiAgICAgICAgcHJpbnQoIiAgRVJST1I6IGV4cGVjdGVkIHBhdHRlcm4gbm90IGZvdW5kLiBGaWxlIG1heSBkaWZmZXIuIikKICAgICAgICBzeXMuZXhpdCgxKQplbHNlOgogICAgb3BlbihwYXRoLCAidyIpLndyaXRlKHNyYy5yZXBsYWNlKG9sZCwgbmV3LCAxKSkKICAgIHByaW50KCIgIFVucGFjayBidWcgZml4ZWQuIikK' | base64 -d | python3 - "$MEMORY_STORE"
ok "Unpack patch done."

# ── Fix 3: raise timeouts, lower retries ─────────────────────────────────────
banner "=== Fix 3: config.yaml — timeouts & retries ==="
echo 'CmltcG9ydCBzeXMsIHJlLCB5YW1sCgpwYXRoID0gc3lzLmFyZ3ZbMV0Kc3JjID0gb3BlbihwYXRoKS5yZWFkKCkKCiMgUGFyc2UgdG8gY2hlY2sgY3VycmVudCBzdGF0ZSDigJQgd29yayBvbiByYXcgdGV4dCB0byBwcmVzZXJ2ZSBmb3JtYXR0aW5nCmlmICJ0YXNrX3RpbWVvdXRzIiBpbiBzcmM6CiAgICBwcmludCgiICB0YXNrX3RpbWVvdXRzIGFscmVhZHkgcHJlc2VudCDigJQgdXBkYXRpbmcgdmFsdWVzLiIpCiAgICAjIFVwZGF0ZSBleGlzdGluZyBmYWN0X2V4dHJhY3QgYW5kIGFnZW50X2FjdGlvbiB2YWx1ZXMKICAgIHNyYyA9IHJlLnN1YihyIih0YXNrX3RpbWVvdXRzOi4qPykoZmFjdF9leHRyYWN0OlxzKilcZCsiLAogICAgICAgICAgICAgICAgIGxhbWJkYSBtOiBtLmdyb3VwKDEpICsgbS5ncm91cCgyKSArICIxODAiLAogICAgICAgICAgICAgICAgIHNyYywgZmxhZ3M9cmUuRE9UQUxMKQogICAgc3JjID0gcmUuc3ViKHIiKHRhc2tfdGltZW91dHM6Lio/KShhZ2VudF9hY3Rpb246XHMqKVxkKyIsCiAgICAgICAgICAgICAgICAgbGFtYmRhIG06IG0uZ3JvdXAoMSkgKyBtLmdyb3VwKDIpICsgIjEyMCIsCiAgICAgICAgICAgICAgICAgc3JjLCBmbGFncz1yZS5ET1RBTEwpCmVsc2U6CiAgICAjIEluamVjdCB0YXNrX3RpbWVvdXRzIGJlZm9yZSByZXRyeV9hdHRlbXB0cyB1bmRlciBsbG06CiAgICBpZiAiICByZXRyeV9hdHRlbXB0czoiIGluIHNyYzoKICAgICAgICBzcmMgPSBzcmMucmVwbGFjZSgKICAgICAgICAgICAgIiAgcmV0cnlfYXR0ZW1wdHM6IiwKICAgICAgICAgICAgIiAgdGFza190aW1lb3V0czpcbiAgICBmYWN0X2V4dHJhY3Q6IDE4MFxuICAgIGFnZW50X2FjdGlvbjogMTIwXG4gIHJldHJ5X2F0dGVtcHRzOiIsCiAgICAgICAgICAgIDEKICAgICAgICApCiAgICAgICAgcHJpbnQoIiAgdGFza190aW1lb3V0cyBpbmplY3RlZC4iKQogICAgZWxzZToKICAgICAgICBwcmludCgiICBXQVJOSU5HOiBDb3VsZCBub3QgZmluZCBpbnNlcnRpb24gcG9pbnQuIEFkZCBtYW51YWxseToiKQogICAgICAgIHByaW50KCIgICAgVW5kZXIgbGxtOiBhZGQgdGFza190aW1lb3V0czogeyBmYWN0X2V4dHJhY3Q6IDE4MCwgYWdlbnRfYWN0aW9uOiAxMjAgfSIpCgojIEFsc28gc2V0IHJldHJ5X2F0dGVtcHRzOiAxIGFuZCByZXRyeV9kZWxheV9zOiAxLjAgdG8gcmVkdWNlIHdhaXQgdGltZSBvbiBmYWlsdXJlCnNyYyA9IHJlLnN1YihyInJldHJ5X2F0dGVtcHRzOlxzKlxkKyIsICJyZXRyeV9hdHRlbXB0czogMSIsIHNyYykKc3JjID0gcmUuc3ViKHIicmV0cnlfZGVsYXlfczpccypbXGQuXSsiLCAicmV0cnlfZGVsYXlfczogMS4wIiwgc3JjKQoKb3BlbihwYXRoLCAidyIpLndyaXRlKHNyYykKCiMgVmFsaWRhdGUgWUFNTAp0cnk6CiAgICB5YW1sLnNhZmVfbG9hZChvcGVuKHBhdGgpKQogICAgcHJpbnQoIiAgY29uZmlnLnlhbWwgdmFsaWQgYWZ0ZXIgdGltZW91dCBwYXRjaC4iKQpleGNlcHQgeWFtbC5ZQU1MRXJyb3IgYXMgZToKICAgIHByaW50KGYiICBXQVJOSU5HOiBZQU1MIGludmFsaWQgYWZ0ZXIgcGF0Y2g6IHtlfSIpCg==' | base64 -d | python3 - "$PSIE_CONFIG"
ok "Timeout/retry patch done."

# ── Fix 4: detect Ollama model and update config ──────────────────────────────
banner "=== Fix 4: Ollama model detection ==="
if ! command -v ollama &>/dev/null; then
    warn "ollama not found on PATH — skipping model detection."
elif ! ollama list &>/dev/null 2>&1; then
    warn "Ollama not responding — is 'ollama serve' running?"
    warn "Start it with: ollama serve &"
else
    set +e
    echo 'CmltcG9ydCBzdWJwcm9jZXNzLCBqc29uLCBzeXMKCmNvbmZpZ19wYXRoID0gc3lzLmFyZ3ZbMV0KCnRyeToKICAgIHJlc3VsdCA9IHN1YnByb2Nlc3MucnVuKAogICAgICAgIFsib2xsYW1hIiwgImxpc3QiXSwKICAgICAgICBjYXB0dXJlX291dHB1dD1UcnVlLCB0ZXh0PVRydWUsIHRpbWVvdXQ9MTAKICAgICkKICAgIGxpbmVzID0gW2wuc3RyaXAoKSBmb3IgbCBpbiByZXN1bHQuc3Rkb3V0LnN0cmlwKCkuc3BsaXRsaW5lcygpIGlmIGwuc3RyaXAoKV0KICAgICMgU2tpcCBoZWFkZXIgbGluZQogICAgbW9kZWxzID0gW2wuc3BsaXQoKVswXSBmb3IgbCBpbiBsaW5lc1sxOl0gaWYgbF0gaWYgbGVuKGxpbmVzKSA+IDEgZWxzZSBbXQpleGNlcHQgRXhjZXB0aW9uIGFzIGU6CiAgICBwcmludChmIiAgQ291bGQgbm90IHF1ZXJ5IE9sbGFtYToge2V9IikKICAgIG1vZGVscyA9IFtdCgppZiBub3QgbW9kZWxzOgogICAgcHJpbnQoIiAgTm8gT2xsYW1hIG1vZGVscyBmb3VuZC4gUHVsbGluZyBnZW1tYTM6MWIgKGZhc3Rlc3Qgb24gQ1BVKS4uLiIpCiAgICBwcmludCgiICBSdW46IG9sbGFtYSBwdWxsIGdlbW1hMzoxYiIpCiAgICBzeXMuZXhpdCgyKQoKcHJpbnQoZiIgIEF2YWlsYWJsZSBtb2RlbHM6IHttb2RlbHN9IikKCiMgUGljayBiZXN0IGF2YWlsYWJsZTogcHJlZmVyIDRiID4gMWIgPiBhbnl0aGluZyBlbHNlCnByZWZlcnJlZCA9IE5vbmUKZm9yIGNhbmRpZGF0ZSBpbiBbImdlbW1hMzo0YiIsICJnZW1tYTM6MWIiLCAibGxhbWEzLjI6M2IiLCAibGxhbWEzLjI6MWIiXToKICAgIGlmIGFueShjYW5kaWRhdGUgaW4gbSBmb3IgbSBpbiBtb2RlbHMpOgogICAgICAgIHByZWZlcnJlZCA9IGNhbmRpZGF0ZQogICAgICAgIGJyZWFrCmlmIG5vdCBwcmVmZXJyZWQ6CiAgICBwcmVmZXJyZWQgPSBtb2RlbHNbMF0uc3BsaXQoIjoiKVswXSArICI6IiArIG1vZGVsc1swXS5zcGxpdCgiOiIpWy0xXSBpZiAiOiIgaW4gbW9kZWxzWzBdIGVsc2UgbW9kZWxzWzBdCgpwcmludChmIiAgU2VsZWN0ZWQgbW9kZWw6IHtwcmVmZXJyZWR9IikKCiMgUGF0Y2ggY29uZmlnLnlhbWwgdG8gdXNlIHRoaXMgbW9kZWwgZXZlcnl3aGVyZSBpdCByZWZlcmVuY2VzIG9sbGFtYS8KaW1wb3J0IHJlCnNyYyA9IG9wZW4oY29uZmlnX3BhdGgpLnJlYWQoKQpwYXRjaGVkID0gcmUuc3ViKHIib2xsYW1hL1tcdy46Xy1dKyIsIGYib2xsYW1hL3twcmVmZXJyZWR9Iiwgc3JjKQppZiBwYXRjaGVkICE9IHNyYzoKICAgIG9wZW4oY29uZmlnX3BhdGgsICJ3Iikud3JpdGUocGF0Y2hlZCkKICAgIHByaW50KGYiICBjb25maWcueWFtbCB1cGRhdGVkIHRvIHVzZSBvbGxhbWEve3ByZWZlcnJlZH0iKQplbHNlOgogICAgcHJpbnQoZiIgIGNvbmZpZy55YW1sIGFscmVhZHkgcmVmZXJlbmNlcyBjb3JyZWN0IG1vZGVsIG9yIHVzZXMgbm8gb2xsYW1hLyByZWZlcmVuY2VzIikK' | base64 -d | python3 - "$PSIE_CONFIG"
    MODEL_EXIT=$?
    set -e
    if [[ $MODEL_EXIT -eq 2 ]]; then
        warn "No models pulled yet. Run one of:"
        warn "  ollama pull gemma3:1b    # ~800 MB — fastest on CPU"
        warn "  ollama pull gemma3:4b    # ~3 GB  — better quality"
        warn "Then re-run this script."
    fi
fi
ok "Model detection done."

# ── Verify all patches ────────────────────────────────────────────────────────
banner "=== Verification ==="
echo 'CmltcG9ydCBzeXMsIGltcG9ydGxpYi51dGlsLCByZQpzeXMucGF0aC5pbnNlcnQoMCwgc3lzLmFyZ3ZbMV0pCgplcnJvcnMgPSBbXQoKIyAxLiBDaGVjayB1bnBhY2sgZml4IGxhbmRlZAptc19wYXRoID0gc3lzLmFyZ3ZbMl0Kc3JjID0gb3Blbihtc19wYXRoKS5yZWFkKCkKaWYgJ3JhdywgX3Rva2VucyA9IGdhdGV3YXkuY29tcGxldGUoImZhY3RfZXh0cmFjdCInIGluIHNyYzoKICAgIGVycm9ycy5hcHBlbmQoIm1lbW9yeV9zdG9yZS5weTogdW5wYWNrIGJ1ZyBzdGlsbCBwcmVzZW50IikKZWxpZiAncmF3ID0gZ2F0ZXdheS5jb21wbGV0ZSgiZmFjdF9leHRyYWN0IicgaW4gc3JjOgogICAgcHJpbnQoIiAg4pyTICBtZW1vcnlfc3RvcmUucHk6IHVucGFjayBidWcgZml4ZWQiKQplbHNlOgogICAgZXJyb3JzLmFwcGVuZCgibWVtb3J5X3N0b3JlLnB5OiBjb3VsZCBub3QgdmVyaWZ5IGZhY3RfZXh0cmFjdCBsaW5lIikKCiMgMi4gQ2hlY2sgX2VzY2FwZV9saWtlIHByZXNlbnQKaWYgImRlZiBfZXNjYXBlX2xpa2UiIGluIHNyYzoKICAgIHByaW50KCIgIOKckyAgbWVtb3J5X3N0b3JlLnB5OiBfZXNjYXBlX2xpa2UgZGVmaW5lZCIpCmVsc2U6CiAgICBlcnJvcnMuYXBwZW5kKCJtZW1vcnlfc3RvcmUucHk6IF9lc2NhcGVfbGlrZSBtaXNzaW5nIikKCiMgMy4gSW1wb3J0cwpmb3IgbW9kIGluIFsicHNpZS5mZWVkZXIiLCAicHNpZS5rbm93bGVkZ2Vfd29ya2VyIiwgInBzaWUubWVtb3J5X2NvbnNvbGlkYXRvciJdOgogICAgdHJ5OgogICAgICAgIF9faW1wb3J0X18obW9kKQogICAgICAgIHByaW50KGYiICBcdTI3MTMgIHttb2R9IikKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICBlcnJvcnMuYXBwZW5kKGYie21vZH06IHtlfSIpCgppZiBlcnJvcnM6CiAgICBwcmludCgiXG5FcnJvcnMgZm91bmQ6IikKICAgIGZvciBlIGluIGVycm9yczoKICAgICAgICBwcmludChmIiAg4pyXICB7ZX0iKQogICAgc3lzLmV4aXQoMSkK' | base64 -d | python3 - "${PSIE_DIR}" "$MEMORY_STORE"
ok "All checks passed."

# ── Show effective config snapshot ───────────────────────────────────────────
banner "=== Config snapshot (llm section) ==="
python3 - "$PSIE_CONFIG" << 'PYEOF'
import sys, yaml
try:
    cfg = yaml.safe_load(open(sys.argv[1]))
    llm = cfg.get("llm", {})
    print(f"  retry_attempts : {llm.get('retry_attempts','(not set)')}")
    print(f"  retry_delay_s  : {llm.get('retry_delay_s','(not set)')}")
    print(f"  request_timeout: {llm.get('request_timeout','(not set)')}")
    tt = llm.get("task_timeouts", {})
    print(f"  task_timeouts  : fact_extract={tt.get('fact_extract','?')}s  agent_action={tt.get('agent_action','?')}s")
    routing = llm.get("routing", {})
    aa = routing.get("agent_action", {})
    fe = routing.get("fact_extract", {})
    print(f"  agent_action   : preferred={aa.get('preferred','?')}")
    print(f"  fact_extract   : preferred={fe.get('preferred','?')}")
except yaml.YAMLError as e:
    print(f"  config.yaml still invalid: {e}")
PYEOF

# ── Smoke test ────────────────────────────────────────────────────────────────
banner "=== Smoke test: psie learn --url '${TEST_URL}' --turns 3 ==="
info "LLM calls in progress — each turn may take 30-90s on CPU-only Ollama."
echo ""
set +e
psie learn --url "$TEST_URL" --turns 3
LEARN_EXIT=$?
set -e
echo ""

if [[ $LEARN_EXIT -eq 0 ]]; then
    ok "psie learn succeeded."
else
    warn "psie learn exited ${LEARN_EXIT}."
fi

banner "=== psie status ==="
psie status || true
echo ""
ok "All done."
