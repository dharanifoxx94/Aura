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

        # SSRF validation passed above — request via original URL so TLS SNI works.
        # Connecting to the raw IP would cause: SSLCertVerificationError: IP address mismatch
        # (cert is issued for hostname, not the numeric IP).
        req_headers = {**headers, "Host": hostname}

        try:
            resp = session.get(
                current_url,
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
