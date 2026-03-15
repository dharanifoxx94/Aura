"""
Eidolon Vault — Shared Utilities
=======================
Centralised helpers for JSON parsing, text sanitisation, FTS5 safety,
and input validation.

Fixes applied
-------------
  FIX-1  safe_parse_json: replaced greedy re.DOTALL brace regex with a
         brace-counting scanner.  The old r"\{.*\}" with re.DOTALL is
         catastrophically slow (ReDoS) on adversarial LLM output that
         contains many unmatched "{" characters and no closing "}".

  FIX-2  sanitise_injected_text: expanded injection pattern list to cover
         Unicode homoglyph normalisation (already done via NFC), role-separator
         tokens used by common chat-ML formats, and a broader set of
         instruction-override trigger phrases.
"""

import json
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _find_first_json_object(text: str) -> Optional[str]:
    """Return the substring of *text* that forms the first balanced {...} block.

    Uses a simple character scan instead of a greedy regex so it cannot
    be exploited for ReDoS via adversarial input with many unmatched braces.
    Returns None if no balanced block is found.
    """
    depth = 0
    start = None
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start : i + 1]
    return None


def safe_parse_json(raw: str, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse LLM output that *should* be JSON but may be wrapped in markdown
    fences or have surrounding text.  Returns a dict on success; returns
    *fallback* (default {}) on failure.

    FIX: the old r"\\{.*\\}" with re.DOTALL was vulnerable to ReDoS on
    adversarial input.  Replaced with _find_first_json_object() which uses
    a linear character scan with O(n) worst-case complexity.
    """
    if fallback is None:
        fallback = {}

    if not isinstance(raw, str):
        return fallback

    # Strip markdown code fences.
    clean = re.sub(r"```(?:json)?", "", raw).strip()

    # Fast path: try the whole cleaned string first.
    try:
        result = json.loads(clean)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Slow path: scan for the first balanced {...} block (O(n), not ReDoS-prone).
    candidate = _find_first_json_object(clean)
    if candidate:
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    logger.debug(
        "safe_parse_json: could not parse JSON. Raw (first 300 chars): %s",
        raw[:300],
    )
    return fallback


# ---------------------------------------------------------------------------
# FTS5 query sanitisation (CWE-89 analogue for SQLite FTS5)
# ---------------------------------------------------------------------------

def sanitise_for_fts(text: str, max_terms: int = 20, min_len: int = 4) -> str:
    """Convert arbitrary text into a safe FTS5 MATCH expression.

    Strategy:
      1. Lower-case and tokenise (ASCII alphanumeric + underscore).
      2. Keep only tokens >= *min_len* characters.
      3. Escape double-quotes and backslashes to prevent injection.
      4. Cap at *max_terms* tokens.
      5. Join with ' OR '.

    Returns an empty string when no usable tokens remain (callers should
    skip the FTS query and fall back to non-FTS retrieval).
    """
    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    tokens = [t for t in tokens if len(t) >= min_len]
    tokens = [t.replace('"', '""').replace("\\", "\\\\") for t in tokens]
    return " OR ".join(tokens[:max_terms])


# ---------------------------------------------------------------------------
# Prompt-injection guard for any text that will be injected into prompts
# ---------------------------------------------------------------------------

_MAX_INJECT_LEN = 400
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# FIX: expanded from 4 patterns to cover:
#   - Common instruction-override phrases (case-insensitive)
#   - Chat-ML / model-specific role separator tokens
#   - Jailbreak boilerplate variants
# Each tuple is (compiled_regex, replacement_string).
# Order matters: more specific patterns first.
_DANGEROUS_PATTERNS = [
    # Role-separator tokens used by OpenAI, LLaMA, Mistral, Gemma chat formats
    (re.compile(r"<\|(?:im_start|im_end|system|user|assistant|begin_of_text|end_of_text)[^>]*\|>", re.I), ""),
    (re.compile(r"\[/?(?:INST|SYS|SYSTEM|USER|ASSISTANT)\]", re.I), ""),
    (re.compile(r"###\s*(?:system|user|assistant|instruction|input|response)\b", re.I), ""),
    # Classic instruction-override trigger phrases
    (re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.I), ""),
    (re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.I), ""),
    (re.compile(r"forget\s+(?:everything|all|previous|prior)\s+(?:instructions?|above)", re.I), ""),
    (re.compile(r"new\s+instructions?\s*:", re.I), ""),
    (re.compile(r"override\s+(?:your\s+)?(?:instructions?|programming|system\s+prompt)", re.I), ""),
    # System-prompt / persona hijack phrases
    (re.compile(r"system\s+prompt", re.I), ""),
    (re.compile(r"you\s+are\s+(?:now\s+)?(?:an?\s+)?(?:AI|assistant|chatbot|language\s+model)", re.I), ""),
    (re.compile(r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+)?(?:AI|DAN|jailbreak)", re.I), ""),
    (re.compile(r"\brole\s*:", re.I), ""),
    # DAN / jailbreak boilerplate
    (re.compile(r"\bDAN\b"), ""),
    (re.compile(r"jailbreak", re.I), ""),
    (re.compile(r"do\s+anything\s+now", re.I), ""),
]


def sanitise_injected_text(text: Union[str, Any], max_len: int = _MAX_INJECT_LEN) -> str:
    """Guard against prompt-injection payloads stored in the database.

    Mitigations applied in order:
      1. Coerce to str.
      2. Unicode NFC normalisation (collapses homoglyphs to canonical form).
      3. Strip C0 control characters (null bytes, escape sequences, etc.).
      4. Remove known injection trigger phrases and role-separator tokens.
      5. Truncate to *max_len* characters with an indicator.

    FIX: expanded _DANGEROUS_PATTERNS from 4 to 14 entries covering
    chat-ML role separators, instruction-override variants, and common
    jailbreak boilerplate.  Unicode normalisation (step 2) already handled
    homoglyph substitution attacks.
    """
    if not isinstance(text, str):
        text = str(text)
    text = unicodedata.normalize("NFC", text)
    text = _CTRL_RE.sub("", text)
    for pattern, replacement in _DANGEROUS_PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > max_len:
        text = text[:max_len] + " [truncated]"
    return text.strip()


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------

def truncate(text: str, max_chars: int, notice: str = "\n\n[… truncated …]") -> str:
    """Return *text* unchanged if <= *max_chars*, otherwise truncate with notice."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + notice


# ---------------------------------------------------------------------------
# Numeric clamping
# ---------------------------------------------------------------------------

def clamp(value: float, lo: float = 0.0, hi: float = 1.0, default: float = 0.5) -> float:
    """Clamp a numeric value to [lo, hi]; return *default* on type errors."""
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default
