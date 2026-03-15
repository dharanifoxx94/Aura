"""
Eidolon Vault — Content Feeder
======================
Ingests content from URLs, RSS feeds, and raw text, normalising everything
into a ``ScenarioContext`` ready for the simulation runner.

Supports three ingestion modes:
  • "text"  — wrap an inline string as a ScenarioContext
  • "url"   — delegate to input_parser.parse_url (with SSRF protection)
  • "rss"   — parse an RSS/Atom feed via feedparser, optionally condense
               multiple items into a single scenario string via LLMGateway

The gateway is optional.  Without it the feeder operates in pass-through mode:
RSS items are concatenated rather than summarised.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from .input_parser import parse_text, parse_url, scenario_hash
from .models import ScenarioContext
from .exceptions import InputError

if TYPE_CHECKING:
    from .llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

_RSS_CONDENSE_SYSTEM = """\
You are a scenario-synthesis assistant. Given a list of recent news/article summaries,
produce a single concise scenario description (150-250 words) that captures the key
actors, tensions, and open questions. Write it as a neutral briefing, not a headline.
Return ONLY the scenario text — no preamble or metadata."""

_RSS_CONDENSE_USER = """\
Synthesise the following {n} feed items into one scenario description:

{items}

Scenario:"""


class ContentFeeder:
    """Normalise external content (URL, RSS, text) into a ScenarioContext."""

    def __init__(
        self,
        gateway: Optional["LLMGateway"] = None,
        cfg: Optional[dict] = None,
    ) -> None:
        self.gateway = gateway
        self._url_timeout: int = (cfg or {}).get("input", {}).get("url_timeout_s", 20)
        self._max_bytes: int = (cfg or {}).get("input", {}).get(
            "max_file_bytes", 20 * 1024 * 1024
        )
        self._allow_private_ip: bool = (cfg or {}).get("input", {}).get(
            "allow_private_ip_url", False
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest(
        self,
        source: str,
        *,
        source_type: str = "auto",
        title: str = "",
    ) -> ScenarioContext:
        """
        Ingest *source* and return a ``ScenarioContext``.

        Parameters
        ----------
        source:
            Raw text, a URL (http/https), or an RSS/Atom feed URL.
        source_type:
            ``"auto"`` (default) | ``"text"`` | ``"url"`` | ``"rss"``
        title:
            Optional override for the scenario title.
        """
        effective_type = source_type if source_type != "auto" else _detect_type(source)

        if effective_type == "rss":
            return self.ingest_rss(source, title=title)
        if effective_type == "url":
            return self._ingest_url(source, title=title)
        return self._ingest_text(source, title=title)

    def ingest_rss(
        self,
        feed_url: str,
        *,
        max_items: int = 6,
        title: str = "",
    ) -> ScenarioContext:
        """
        Fetch an RSS/Atom feed and condense its entries into a ScenarioContext.

        If a ``LLMGateway`` was supplied at construction time the entries are
        summarised by the model; otherwise they are concatenated verbatim.
        """
        try:
            import feedparser  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "feedparser is required for RSS ingestion.\n"
                "  pip install feedparser"
            ) from exc

        logger.info("Fetching RSS feed: %s", feed_url)
        feed = feedparser.parse(feed_url)

        if feed.bozo and not feed.entries:
            raise InputError(
                f"Failed to parse RSS feed '{feed_url}': {feed.bozo_exception}"
            )

        entries = feed.entries[:max_items]
        if not entries:
            raise InputError(f"RSS feed '{feed_url}' contains no entries.")

        feed_title = title or getattr(feed.feed, "title", None) or _label_from_url(feed_url)
        item_texts = _format_entries(entries)

        if self.gateway:
            scenario_text = self._condense_via_llm(item_texts, feed_title)
        else:
            # Fallback: join entries without LLM summarisation.
            scenario_text = "\n\n".join(item_texts)

        ctx = parse_text(scenario_text, title=feed_title)
        logger.info(
            "RSS feed ingested: %d items → scenario '%s' (%d chars)",
            len(entries), ctx.title, len(ctx.raw_text),
        )
        return ctx

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def make_scenario_string(self, ctx: ScenarioContext) -> str:
        """Return the raw text of a ScenarioContext (thin helper for callers)."""
        return ctx.raw_text

    def hash_for(self, ctx: ScenarioContext) -> str:
        """Return the deterministic 16-char SHA-256 prefix for *ctx*."""
        return scenario_hash(ctx)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _ingest_url(self, url: str, title: str) -> ScenarioContext:
        """Fetch *url* with SSRF protection that is SSL-compatible.

        ``input_parser.parse_url`` replaces the hostname with the resolved IP
        in the request URL (IP pinning).  That breaks SNI: the server returns
        a cert for the hostname, but the TLS handshake sees the IP, so Python
        raises ``SSLCertVerificationError: IP address mismatch``.

        This method instead:
          1. Resolves the hostname and checks the IP is not private (SSRF guard).
          2. Fetches using the *original* URL (hostname intact) so SSL/SNI works.
        """
        logger.info("Fetching URL: %s", url)
        raw_html = _fetch_url_ssrf_safe(
            url,
            timeout_s=self._url_timeout,
            allow_private_ip=self._allow_private_ip,
            max_bytes=self._max_bytes,
        )
        text = _extract_text(raw_html)
        title_final = title or _extract_title_from_html(raw_html) or _label_from_url(url)
        ctx = parse_text(text, title=title_final)
        ctx.source_type = "url"
        ctx.source_ref  = url
        logger.info("URL ingested: '%s' (%d chars)", ctx.title, len(ctx.raw_text))
        return ctx

    def _ingest_text(self, text: str, title: str) -> ScenarioContext:
        ctx = parse_text(text, title=title)
        logger.debug("Text ingested: '%s' (%d chars)", ctx.title, len(ctx.raw_text))
        return ctx

    def _condense_via_llm(self, item_texts: List[str], feed_title: str) -> str:
        """Use the gateway to produce a coherent scenario from a list of feed items."""
        assert self.gateway is not None
        items_block = "\n\n".join(
            f"[Item {i + 1}]\n{t}" for i, t in enumerate(item_texts)
        )
        messages = [
            {"role": "system", "content": _RSS_CONDENSE_SYSTEM},
            {
                "role": "user",
                "content": _RSS_CONDENSE_USER.format(
                    n=len(item_texts), items=items_block
                ),
            },
        ]
        try:
            result = self.gateway.complete(
                "summarise",
                messages,
                max_tokens=400,
                temperature=0.4,
            )
            # gateway.complete() returns a plain string
            text = result.strip() if isinstance(result, str) else result[0].strip()
            logger.debug("LLM condensed %d items into %d chars", len(item_texts), len(text))
            return text or "\n\n".join(item_texts)
        except Exception as exc:
            logger.warning("LLM condensation failed (%s) — falling back to concat", exc)
            return "\n\n".join(item_texts)


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

def ingest(
    source: str,
    *,
    source_type: str = "auto",
    title: str = "",
    gateway: Optional["LLMGateway"] = None,
    cfg: Optional[dict] = None,
) -> ScenarioContext:
    """
    One-shot helper: create a feeder, ingest *source*, return ScenarioContext.

    Useful for scripting and tests without explicitly constructing a feeder.
    """
    feeder = ContentFeeder(gateway=gateway, cfg=cfg)
    return feeder.ingest(source, source_type=source_type, title=title)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _detect_type(source: str) -> str:
    """Heuristically decide whether *source* is a URL, RSS feed, or plain text."""
    stripped = source.strip()
    if not stripped.startswith(("http://", "https://")):
        return "text"
    # Simple heuristic: if the path ends in known feed extensions treat as RSS.
    lower = stripped.lower().split("?")[0]
    if any(lower.endswith(ext) for ext in ("/feed", "/rss", "/atom", ".rss", ".xml", ".atom")):
        return "rss"
    return "url"


def _label_from_url(url: str) -> str:
    import re
    host = url.split("/")[2] if "//" in url else url
    return re.sub(r"^www\.", "", host)[:60]


def _format_entries(entries: list) -> List[str]:
    """Extract title + summary text from feedparser entry objects."""
    parts: List[str] = []
    for e in entries:
        title = getattr(e, "title", "")
        summary = (
            getattr(e, "summary", "")
            or getattr(e, "description", "")
            or ""
        )
        # Strip HTML tags crudely (trafilatura not guaranteed here)
        import re
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()
        text = f"{title}\n{summary}".strip() if title else summary
        if text:
            parts.append(text[:800])
    return parts

def _fetch_url_ssrf_safe(
    url: str,
    *,
    timeout_s: int,
    allow_private_ip: bool,
    max_bytes: int,
) -> str:
    """
    Fetch *url* with SSRF protection that does NOT break SSL.

    The fix vs input_parser._fetch_with_requests:
      • We resolve the hostname and validate the IP (SSRF guard — same as before).
      • We then make the HTTP request to the ORIGINAL URL (hostname intact).
        This keeps SNI working so the TLS cert check succeeds.

    Connecting to the IP directly is what caused::

        SSLCertVerificationError: IP address mismatch,
        certificate is not valid for '151.101.210.132'

    because the cert is issued for the hostname, not the raw IP.
    """
    import ipaddress
    import socket
    import urllib.parse

    import requests

    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""

    if not hostname:
        raise InputError(f"Cannot parse hostname from URL: {url!r}")

    # ── SSRF guard: resolve → check IP ──────────────────────────────────
    try:
        addr_infos = socket.getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise InputError(f"Could not resolve hostname '{hostname}': {exc}") from exc

    checked = False
    for ai in addr_infos:
        ip_str = ai[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        checked = True
        if not allow_private_ip and (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
            or ip.is_multicast
        ):
            raise InputError(
                f"URL host '{hostname}' resolves to private/reserved IP "
                f"{ip_str} — rejected.  Pass allow_private_ip=True to override."
            )

    if not checked:
        raise InputError(f"Could not obtain a valid IP for hostname '{hostname}'")

    # ── Fetch using the ORIGINAL URL so SSL/SNI works ────────────────────
    _CHUNK = 65_536
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0 (Eidolon Vault/1.4; +https://github.com/eidolon_vault)"}

    try:
        resp = session.get(url, headers=headers, timeout=timeout_s, stream=True)
        resp.raise_for_status()
    except requests.exceptions.SSLError as exc:
        raise InputError(f"SSL error fetching '{url}': {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise InputError(f"Failed to fetch URL: {exc}") from exc

    cl = resp.headers.get("Content-Length")
    if cl and int(cl) > max_bytes:
        resp.close()
        raise InputError(
            f"Response Content-Length ({int(cl) // 1_048_576} MB) exceeds limit."
        )

    chunks: list = []
    total = 0
    for chunk in resp.iter_content(chunk_size=_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            resp.close()
            raise InputError("Response body exceeded size limit — aborting.")
        chunks.append(chunk)

    return b"".join(chunks).decode("utf-8", errors="replace")


def _extract_text(html_content: str) -> str:
    """Extract readable text from HTML, using trafilatura if available."""
    try:
        import trafilatura
        text = trafilatura.extract(html_content, include_comments=False)
        if text:
            return text
    except ImportError:
        pass

    import re, html as _html
    text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ",
                  html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text[:10_000]


def _extract_title_from_html(html_content: str) -> str:
    """Pull the <title> tag value from raw HTML, or return empty string."""
    import re
    m = re.search(r"<title[^>]*>([^<]{1,120})</title>", html_content, re.IGNORECASE)
    return m.group(1).strip() if m else ""
