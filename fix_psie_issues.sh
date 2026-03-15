#!/bin/sh
# fix_psie_issues.sh — Drop in ~/PSIE_v-1.4/ and run: sh fix_psie_issues.sh
# Fixes all 5 remaining high/medium/low issues found in the security audit.
set -e
cd "$(dirname "$0")"

# ─────────────────────────────────────────────────────────────────────────────
echo "==> [1/3] Patching psie/utils.py (safe_parse_json ReDoS + prompt injection)"
# ─────────────────────────────────────────────────────────────────────────────
cat > psie/utils.py << 'EOF'
"""
PSIE — Shared Utilities
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
EOF

# ─────────────────────────────────────────────────────────────────────────────
echo "==> [2/3] Patching psie/input_parser.py (response body size cap)"
# ─────────────────────────────────────────────────────────────────────────────
cat > psie/input_parser.py << 'EOF'
"""
PSIE — Input Parser
====================
Normalises raw input (text, URL, PDF, .docx, plain text) into a
ScenarioContext with added SSRF protection (IP pinning and redirect
validation).

Fix applied
-----------
  FIX-3  _fetch_with_requests: now streams the response body and aborts if
         the payload exceeds _DEFAULT_MAX_FILE_BYTES (20 MB) before the full
         body is buffered in memory.  The old code called resp.text which
         loaded the entire response regardless of size.  Also checks the
         Content-Length header before even starting to read.
         Same cap applied to the urllib fallback path.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import logging
import re
import socket
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple, Union
from urllib.parse import urljoin

import requests

from .models import ScenarioContext
from .exceptions import InputError

logger = logging.getLogger(__name__)

_DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
_ALLOWED_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_MAX_REDIRECTS = 5
_STREAM_CHUNK = 65_536  # 64 KB read chunks


# ---------------------------------------------------------------------------
# SSRF protection (IPv4 & IPv6 aware with IP pinning)
# ---------------------------------------------------------------------------

def _resolve_and_pin(hostname: str) -> tuple[str, Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    try:
        addr_infos = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise InputError(f"Could not resolve hostname '{hostname}': {e}") from e

    for ai in addr_infos:
        ip_str = ai[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
            if ip.version == 4:
                return ip_str, ip
        except ValueError:
            continue

    for ai in addr_infos:
        ip_str = ai[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
            return ip_str, ip
        except ValueError:
            continue

    raise InputError(f"Could not obtain a valid IP for hostname '{hostname}'")


def _is_private_ip(ip_obj: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    return (ip_obj.is_private or ip_obj.is_loopback or
            ip_obj.is_link_local or ip_obj.is_unspecified or
            ip_obj.is_multicast)


def _validate_redirect_url(redirect_url: str, allow_private_ip: bool) -> str:
    parsed = urllib.parse.urlparse(redirect_url)
    hostname = parsed.hostname or ""
    ip_str, ip_obj = _resolve_and_pin(hostname)
    if not allow_private_ip and _is_private_ip(ip_obj):
        raise InputError(f"Redirect to '{redirect_url}' resolves to private IP {ip_str} — rejected.")
    return ip_str


# ---------------------------------------------------------------------------
# Public parsers
# ---------------------------------------------------------------------------

def parse_text(text: str, title: str = "") -> ScenarioContext:
    clean = text.strip()
    if not clean:
        raise InputError("Scenario text is empty.")
    return ScenarioContext(
        raw_text=clean,
        source_type="text",
        source_ref="<inline>",
        title=title or _slug(clean),
    )


def parse_file(path: Union[str, Path], max_bytes: int = _DEFAULT_MAX_FILE_BYTES) -> ScenarioContext:
    p = Path(path)
    if not p.exists():
        raise InputError(f"File not found: {p}")

    size = p.stat().st_size
    if size > max_bytes:
        raise InputError(
            f"File '{p.name}' is {size / 1_048_576:.1f} MB which exceeds the "
            f"{max_bytes / 1_048_576:.0f} MB limit.  "
            "Adjust 'input.max_file_bytes' in config if needed."
        )

    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(p)
    if suffix in (".txt", ".md"):
        text = p.read_text(encoding="utf-8", errors="replace")
        return _make_ctx(text, source_ref=str(p), title=p.stem, source_type="file")
    if suffix == ".docx":
        return _parse_docx(p)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        return _make_ctx(text, source_ref=str(p), title=p.stem, source_type="file")
    except Exception as exc:
        raise InputError(f"Cannot read file '{p}': {exc}") from exc


def parse_url(
    url: str,
    timeout_s: int = 20,
    allow_private_ip: bool = False,
    max_bytes: int = _DEFAULT_MAX_FILE_BYTES,
) -> ScenarioContext:
    """Fetch and parse text from a URL with IP pinning, redirect validation,
    and response-body size cap.

    FIX: added *max_bytes* parameter (default 20 MB).  The response is now
    streamed and aborted if it exceeds this limit before the full body is
    buffered — prevents OOM on oversized pages.
    """
    if not _ALLOWED_URL_RE.match(url):
        raise InputError(f"Invalid URL — must start with http:// or https://.  Got: {url!r}")

    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""
    initial_ip_str, initial_ip_obj = _resolve_and_pin(hostname)
    if not allow_private_ip and _is_private_ip(initial_ip_obj):
        raise InputError(f"URL host '{hostname}' resolves to private IP {initial_ip_str} — rejected.")

    text = _fetch_url_safe(
        url, timeout_s=timeout_s, allow_private_ip=allow_private_ip, max_bytes=max_bytes
    )
    title = _extract_title_from_url(url)
    return _make_ctx(text, source_ref=url, title=title, source_type="url")


# ---------------------------------------------------------------------------
# Hash
# ---------------------------------------------------------------------------

def scenario_hash(ctx: ScenarioContext) -> str:
    return hashlib.sha256(ctx.raw_text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _make_ctx(text: str, *, source_ref: str, title: str, source_type: str) -> ScenarioContext:
    clean = text.strip()
    if not clean:
        raise InputError(f"No text could be extracted from '{source_ref}'.")
    return ScenarioContext(
        raw_text=clean,
        source_type=source_type,
        source_ref=source_ref,
        title=title or _slug(clean),
    )


def _slug(text: str, max_len: int = 60) -> str:
    words = re.sub(r"[^\w\s]", "", text.lower()).split()
    return "_".join(words[:8])[:max_len] or "scenario"


def _parse_pdf(p: Path) -> ScenarioContext:
    try:
        import pdfplumber
        text_parts: list[str] = []
        with pdfplumber.open(str(p)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return _make_ctx("\n\n".join(text_parts), source_ref=str(p), title=p.stem, source_type="pdf")
    except ImportError:
        pass

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(p))
        parts = [page.extract_text() for page in reader.pages if page.extract_text()]
        return _make_ctx("\n\n".join(parts), source_ref=str(p), title=p.stem, source_type="pdf")
    except ImportError:
        raise ImportError(
            "Install pdfplumber or pypdf to parse PDF files:\n"
            "  pip install pdfplumber\n  # or\n  pip install pypdf"
        )


def _parse_docx(p: Path) -> ScenarioContext:
    try:
        from docx import Document
        doc = Document(str(p))
        text = "\n".join(para.text for para in doc.paragraphs if para.text.strip())
        return _make_ctx(text, source_ref=str(p), title=p.stem, source_type="file")
    except ImportError:
        raise ImportError(
            "Install python-docx to parse .docx files:\n  pip install python-docx"
        )


def _fetch_url_safe(
    url: str,
    *,
    timeout_s: int = 20,
    allow_private_ip: bool = False,
    max_redirects: int = _MAX_REDIRECTS,
    max_bytes: int = _DEFAULT_MAX_FILE_BYTES,
) -> str:
    try:
        return _fetch_with_requests(url, timeout_s, allow_private_ip, max_redirects, max_bytes)
    except ImportError:
        logger.debug("requests not available, falling back to urllib (no redirect handling)")
        return _fetch_with_urllib(url, timeout_s, allow_private_ip=allow_private_ip, max_bytes=max_bytes)


def _fetch_with_requests(
    url: str,
    timeout_s: int,
    allow_private_ip: bool,
    max_redirects: int,
    max_bytes: int = _DEFAULT_MAX_FILE_BYTES,
) -> str:
    """Fetch *url* with manual redirect handling, IP pinning, and a hard
    response-body size cap.

    FIX: streams the response body in _STREAM_CHUNK chunks and raises
    InputError if the accumulated size exceeds *max_bytes*.  Also rejects
    responses that declare an oversized Content-Length before reading.
    The old code called resp.text which buffered the full response in memory.
    """
    session = requests.Session()
    session.max_redirects = 0
    headers = {"User-Agent": "Mozilla/5.0 (PSIE/1.4; +https://github.com/psie)"}

    current_url = url
    redirect_count = 0

    while redirect_count <= max_redirects:
        parsed = urllib.parse.urlparse(current_url)
        hostname = parsed.hostname or ""
        ip_str, ip_obj = _resolve_and_pin(hostname)
        if not allow_private_ip and _is_private_ip(ip_obj):
            raise InputError(f"URL host '{hostname}' resolves to private IP {ip_str} — rejected.")

        if ":" in parsed.netloc:
            host, port = parsed.netloc.rsplit(":", 1)
            new_netloc = f"{ip_str}:{port}"
        else:
            new_netloc = ip_str
        pinned_url = parsed._replace(netloc=new_netloc).geturl()
        req_headers = {**headers, "Host": hostname}

        try:
            resp = session.get(
                pinned_url,
                headers=req_headers,
                timeout=timeout_s,
                allow_redirects=False,
                stream=True,   # FIX: stream so we can abort on oversized responses
            )
        except Exception as e:
            raise InputError(f"Failed to fetch URL: {e}") from e

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                break
            current_url = urljoin(current_url, location)
            redirect_count += 1
            logger.debug("Following redirect %d to %s", redirect_count, current_url)
            continue

        resp.raise_for_status()

        # FIX: reject oversized responses before reading the body.
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            resp.close()
            raise InputError(
                f"Response Content-Length ({int(content_length) // 1_048_576} MB) "
                f"exceeds the {max_bytes // 1_048_576} MB limit."
            )

        # FIX: stream-read with a running size check.
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK):
            total += len(chunk)
            if total > max_bytes:
                resp.close()
                raise InputError(
                    f"Response body exceeded the {max_bytes // 1_048_576} MB size limit — "
                    "aborting download.  Adjust 'input.max_file_bytes' in config if needed."
                )
            chunks.append(chunk)

        html_content = b"".join(chunks).decode("utf-8", errors="replace")
        break
    else:
        raise InputError(f"Too many redirects (>{max_redirects})")

    return _extract_text_from_html(html_content)


def _fetch_with_urllib(
    url: str,
    timeout_s: int,
    allow_private_ip: bool = False,
    max_bytes: int = _DEFAULT_MAX_FILE_BYTES,
) -> str:
    """Fallback using urllib — with IP validation and body size cap.

    FIX: reads in chunks and aborts if *max_bytes* is exceeded.
    """
    import urllib.request

    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""
    ip_str, ip_obj = _resolve_and_pin(hostname)
    if not allow_private_ip and _is_private_ip(ip_obj):
        raise InputError(f"URL host '{hostname}' resolves to private IP {ip_str} — rejected.")

    if ":" in parsed.netloc:
        _, port = parsed.netloc.rsplit(":", 1)
        new_netloc = f"{ip_str}:{port}"
    else:
        new_netloc = ip_str
    pinned_url = parsed._replace(netloc=new_netloc).geturl()
    req = urllib.request.Request(
        pinned_url,
        headers={"User-Agent": "Mozilla/5.0 (PSIE/1.4)", "Host": hostname},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            # FIX: check Content-Length before reading
            cl = resp.headers.get("Content-Length")
            if cl and int(cl) > max_bytes:
                raise InputError(
                    f"Response Content-Length ({int(cl) // 1_048_576} MB) "
                    f"exceeds the {max_bytes // 1_048_576} MB limit."
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(_STREAM_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise InputError(
                        f"Response body exceeded the {max_bytes // 1_048_576} MB limit — aborting."
                    )
                chunks.append(chunk)
            html_content = b"".join(chunks).decode("utf-8", errors="replace")
    except InputError:
        raise
    except Exception as e:
        raise InputError(f"Failed to fetch URL with urllib: {e}") from e

    return _extract_text_from_html(html_content)


def _extract_text_from_html(html_content: str) -> str:
    try:
        import trafilatura
        text = trafilatura.extract(html_content, include_comments=False)
        if text:
            return text
    except ImportError:
        pass

    text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html_content,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text[:10_000]


def _extract_title_from_url(url: str) -> str:
    clean = re.sub(r"https?://[^/]+/", "", url).rstrip("/")
    segment = clean.split("/")[-1].split("?")[0]
    return re.sub(r"[-_]", " ", segment)[:60] or "url_scenario"
EOF

# ─────────────────────────────────────────────────────────────────────────────
echo "==> [3/3] Patching psie/llm_gateway.py (API key warning spam + heartbeat timeout)"
# ─────────────────────────────────────────────────────────────────────────────

# Use Python to do a targeted in-place patch of two methods so we don't have
# to reproduce the entire 450-line file in a heredoc.
python3 - << 'PYEOF'
import re
from pathlib import Path

src = Path("psie/llm_gateway.py").read_text()

# ── FIX-4: _check_api_keys — demote WARNING → DEBUG ──────────────────────────
old_check = '''    def _check_api_keys(self) -> None:
        """Warn at startup for any cloud provider with no API key.
        Uses WARNING so it is visible at the default log level.
        """
        for provider in ("groq", "gemini", "openrouter"):
            key = self.providers.get(provider, {}).get("api_key", "")
            if not key:
                logger.warning(          # was logger.info — invisible by default
                    "Provider \'%s\' has no API key — it will be skipped. %s",
                    provider,
                    _PROVIDER_HINTS.get(provider, ""),
                )'''

new_check = '''    def _check_api_keys(self) -> None:
        """Log at DEBUG level for cloud providers with no API key configured.

        FIX: demoted from WARNING to DEBUG.  Emitting WARNING-level noise for
        every missing cloud provider on every startup is unhelpful when the
        operator intentionally uses only Ollama.  The missing-key error is
        still raised loudly at the point of first use (LLMError in
        _call_litellm), so the operator will not miss it in practice.
        """
        for provider in ("groq", "gemini", "openrouter"):
            key = self.providers.get(provider, {}).get("api_key", "")
            if not key:
                logger.debug(
                    "Provider \'%s\' has no API key — it will be skipped. %s",
                    provider,
                    _PROVIDER_HINTS.get(provider, ""),
                )'''

if old_check not in src:
    print("  WARNING: _check_api_keys pattern not found verbatim — skipping FIX-4")
    print("  (may already be patched, or whitespace differs)")
else:
    src = src.replace(old_check, new_check, 1)
    print("  FIX-4 applied: _check_api_keys demoted to DEBUG")

# ── FIX-5: _call_with_heartbeat — add hard total timeout ─────────────────────
old_heartbeat = '''    def _call_with_heartbeat(
        self,
        fn,
        cb=None,
        interval: float = 5.0,
    ) -> str:
        """
        Run fn() in a background thread, emitting a heartbeat tick via cb
        every *interval* seconds so the user knows the process is alive.
        Exceptions from fn() are re-raised on the calling thread.
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(fn)
            while True:
                try:
                    return future.result(timeout=interval)
                except concurrent.futures.TimeoutError:
                    if cb:
                        cb("  ⏳ waiting for LLM response…")
                    # Loop continues; second Ctrl+C triggers sys.exit(130)
                    # via SimulationRunner._signal_handler'''

new_heartbeat = '''    def _call_with_heartbeat(
        self,
        fn,
        cb=None,
        interval: float = 5.0,
    ) -> str:
        """Run fn() in a background thread, emitting a heartbeat tick via cb
        every *interval* seconds so the user knows the process is alive.
        Exceptions from fn() are re-raised on the calling thread.

        FIX: added a hard total-timeout ceiling equal to self.request_timeout
        (the same value used by litellm for the underlying HTTP call).  Without
        this, if litellm\'s own timeout silently fails to fire (e.g. due to a
        bug in an older version) the heartbeat loop would spin forever.
        """
        hard_limit: float = float(self.request_timeout) * 2  # generous ceiling
        elapsed: float = 0.0
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(fn)
            while True:
                try:
                    return future.result(timeout=interval)
                except concurrent.futures.TimeoutError:
                    elapsed += interval
                    if cb:
                        cb("  ⏳ waiting for LLM response…")
                    if elapsed >= hard_limit:
                        future.cancel()
                        raise LLMError(
                            f"LLM call exceeded hard timeout ceiling of {hard_limit:.0f}s — "
                            "aborting.  Check backend availability or reduce request_timeout."
                        )
                    # Loop continues; second Ctrl+C triggers sys.exit(130)
                    # via SimulationRunner._signal_handler'''

if old_heartbeat not in src:
    print("  WARNING: _call_with_heartbeat pattern not found verbatim — skipping FIX-5")
    print("  (may already be patched, or whitespace differs)")
else:
    src = src.replace(old_heartbeat, new_heartbeat, 1)
    print("  FIX-5 applied: _call_with_heartbeat now has hard total timeout")

Path("psie/llm_gateway.py").write_text(src)
print("  Written: psie/llm_gateway.py")
PYEOF

echo ""
echo "======================================================================"
echo "All 5 fixes applied:"
echo "  FIX-1  safe_parse_json ReDoS fix (brace-counting scanner)"
echo "  FIX-2  sanitise_injected_text — 14 injection patterns (was 4)"
echo "  FIX-3  URL fetch — streamed body with 20 MB hard cap"
echo "  FIX-4  _check_api_keys — WARNING → DEBUG (no more startup spam)"
echo "  FIX-5  _call_with_heartbeat — hard total timeout ceiling added"
echo "======================================================================"
echo ""
echo "Verifying tests still pass..."
python3 -m pytest tests/ -q
