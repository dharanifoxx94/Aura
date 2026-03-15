"""
PSIE CLI Extension — Knowledge Ingestion Commands
===================================================
Adds three commands to the existing ``psie`` Click group:

  psie learn        — learn from a URL, file, or inline text
  psie learn-feed   — learn from an RSS/Atom feed URL
  psie status       — show knowledge-pipeline statistics

Import this module alongside ``psie.cli`` to register the commands::

    # In your entry-point or conftest:
    import cli_extension          # noqa: F401  (registers commands as side-effect)
    from psie.cli import cli

Or, if you want a single binary, add this module to your pyproject.toml
entry_points alongside the existing ``psie`` entry.

Example
-------
::

    $ psie learn --url https://example.com/article
    $ psie learn --text "Two departments are fighting over the Q4 budget."
    $ psie learn-feed --feed-url https://hnrss.org/frontpage --max-items 8
    $ psie status
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from psie.cli import cli, SCENARIO_TYPES
from psie.exceptions import PSIEError
from psie.log import setup_logging


# ──────────────────────────────────────────────────────────────
# psie learn
# ──────────────────────────────────────────────────────────────

@cli.command(name="learn")
@click.option("--text",    "-t", default=None, help="Inline scenario text to learn from")
@click.option("--file",    "-f", default=None, type=click.Path(exists=True),
              help="Path to a .txt / .pdf / .docx file")
@click.option("--url",     "-u", default=None, help="URL to fetch and learn from")
@click.option("--title",         default="",   help="Optional scenario title override")
@click.option(
    "--type", "-s", "scenario_type", default="general",
    type=click.Choice(SCENARIO_TYPES), show_default=True,
    help="Scenario classification for simulation",
)
@click.option("--turns",   default=None, type=click.IntRange(min=1),
              help="Number of verification discussion turns")
@click.option("--config",  default=None, type=click.Path(), help="Path to config.yaml")
@click.option("--quiet",   "-q", is_flag=True, help="Suppress progress output")
@click.option("--allow-private-ip", is_flag=True,
              help="Allow fetching from private/local IPs (SSRF risk)")
def learn(
    text: str | None,
    file: str | None,
    url: str | None,
    title: str,
    scenario_type: str,
    turns: int | None,
    config: str | None,
    quiet: bool,
    allow_private_ip: bool,
) -> None:
    """Learn from a URL, file, or inline text via multi-agent verification."""
    if not text and not file and not url:
        click.echo("No input provided. Enter text (Ctrl+D to finish):\n")
        try:
            text = sys.stdin.read().strip()
        except KeyboardInterrupt:
            click.echo("\nCancelled.")
            sys.exit(0)
        if not text:
            click.echo("Error: no input provided.", err=True)
            sys.exit(1)

    try:
        from psie.config import get_config
        from psie.engine import PSIEEngine
        from psie.knowledge_worker import KnowledgeWorker

        cfg = get_config(config)
    except PSIEError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    engine = PSIEEngine.from_config(cfg)
    worker = KnowledgeWorker(engine)

    def _cb(msg: str) -> None:
        if not quiet:
            click.echo(f"  {msg}")

    # Propagate --allow-private-ip into the config so ContentFeeder honours it.
    if allow_private_ip:
        cfg.setdefault("input", {})["allow_private_ip_url"] = True

    try:
        if file:
            from psie.feeder import ingest
            ctx = ingest(file, source_type="text", title=title, cfg=cfg)
        elif url:
            from psie.feeder import ingest
            ctx = ingest(url, source_type="url", title=title, gateway=engine.gateway, cfg=cfg)
        else:
            from psie.feeder import ingest
            ctx = ingest(text, source_type="text", title=title, cfg=cfg)

        result = worker.learn_from_context(
            ctx,
            scenario_type=scenario_type,
            num_turns=turns,
            progress_callback=_cb,
        )
    except KeyboardInterrupt:
        click.echo("\n⚠ Interrupted.", err=True)
        sys.exit(130)
    except PSIEError as exc:
        click.echo(f"\n❌ Learn failed: {exc}", err=True)
        sys.exit(1)

    _print_learn_result(result)


# ──────────────────────────────────────────────────────────────
# psie learn:url (shortcut)
# ──────────────────────────────────────────────────────────────

@cli.command(name="learn:url")
@click.argument("url")
@click.option("--title",         default="",   help="Optional scenario title override")
@click.option(
    "--type", "-s", "scenario_type", default="general",
    type=click.Choice(SCENARIO_TYPES), show_default=True,
    help="Scenario classification for simulation",
)
@click.option("--turns",   default=None, type=click.IntRange(min=1),
              help="Number of verification discussion turns")
@click.option("--config",  default=None, type=click.Path(), help="Path to config.yaml")
@click.option("--quiet",   "-q", is_flag=True, help="Suppress progress output")
@click.option("--allow-private-ip", is_flag=True,
              help="Allow fetching from private/local IPs (SSRF risk)")
def learn_url(
    url: str,
    title: str,
    scenario_type: str,
    turns: int | None,
    config: str | None,
    quiet: bool,
    allow_private_ip: bool,
) -> None:
    """Shortcut to learn from a URL via multi-agent verification."""
    click.get_current_context().invoke(
        learn,
        text=None,
        file=None,
        url=url,
        title=title,
        scenario_type=scenario_type,
        turns=turns,
        config=config,
        quiet=quiet,
        allow_private_ip=allow_private_ip
    )


# ──────────────────────────────────────────────────────────────
# psie learn-feed
# ──────────────────────────────────────────────────────────────

@cli.command(name="learn-feed")
@click.option("--feed-url",  "-u", required=True, help="RSS or Atom feed URL")
@click.option("--max-items", "-n", default=6, show_default=True,
              type=click.IntRange(min=1, max=20),
              help="Maximum feed items to include")
@click.option("--title",           default="",   help="Optional scenario title override")
@click.option(
    "--type", "-s", "scenario_type", default="general",
    type=click.Choice(SCENARIO_TYPES), show_default=True,
)
@click.option("--turns",   default=None, type=click.IntRange(min=1))
@click.option("--config",  default=None, type=click.Path())
@click.option("--quiet",   "-q", is_flag=True)
def learn_feed(
    feed_url: str,
    max_items: int,
    title: str,
    scenario_type: str,
    turns: int | None,
    config: str | None,
    quiet: bool,
) -> None:
    """Learn from an RSS/Atom feed — items are synthesised into one scenario."""
    try:
        from psie.config import get_config
        from psie.engine import PSIEEngine
        from psie.knowledge_worker import KnowledgeWorker
        from psie.feeder import ContentFeeder

        cfg = get_config(config)
    except PSIEError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    engine = PSIEEngine.from_config(cfg)
    feeder = ContentFeeder(gateway=engine.gateway, cfg=cfg)
    worker = KnowledgeWorker(engine)

    def _cb(msg: str) -> None:
        if not quiet:
            click.echo(f"  {msg}")

    try:
        _cb(f"📡 Fetching feed: {feed_url}")
        ctx = feeder.ingest_rss(feed_url, max_items=max_items, title=title)
        result = worker.learn_from_context(
            ctx,
            scenario_type=scenario_type,
            num_turns=turns,
            progress_callback=_cb,
        )
    except KeyboardInterrupt:
        click.echo("\n⚠ Interrupted.", err=True)
        sys.exit(130)
    except ImportError as exc:
        click.echo(f"\n❌ Missing dependency: {exc}", err=True)
        sys.exit(1)
    except PSIEError as exc:
        click.echo(f"\n❌ Learn-feed failed: {exc}", err=True)
        sys.exit(1)

    _print_learn_result(result)


# ──────────────────────────────────────────────────────────────
# psie status
# ──────────────────────────────────────────────────────────────

@cli.command(name="status")
@click.option("--scenario-hash", default="",
              help="Restrict stats to a specific scenario hash")
@click.option("--consolidate",   is_flag=True,
              help="Run contradiction detection and show suggestions")
@click.option("--prune",         is_flag=True,
              help="Apply suggested pruning (requires --consolidate)")
@click.option("--config",        default=None, type=click.Path())
def status(
    scenario_hash: str,
    consolidate: bool,
    prune: bool,
    config: str | None,
) -> None:
    """Show knowledge-pipeline statistics and optionally consolidate memory."""
    try:
        from psie.config import get_config, ensure_dirs
        from psie.memory_store import MemoryStore
        from psie.llm_gateway import LLMGateway
        from psie.memory_consolidator import MemoryConsolidator

        cfg = get_config(config)
    except PSIEError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    ensure_dirs(cfg)
    store = MemoryStore(cfg)

    # --- Episode stats ---
    runs = store.list_runs(limit=5)
    click.echo("\n📦 Recent simulation runs:")
    if runs:
        click.echo(f"  {'RUN ID':<15} {'HASH':<18} {'TURNS':<7} LAST TURN")
        click.echo("  " + "─" * 58)
        for r in runs:
            last = (r["last_turn"] or "")[:19]
            click.echo(f"  {r['run_id']:<15} {r['scenario_hash']:<18} {r['turns']:<7} {last}")
    else:
        click.echo("  (none yet)")

    # --- Fact stats ---
    gw = LLMGateway(cfg)
    mc = MemoryConsolidator(cfg, gw)
    stats = mc.summary(scenario_hash)
    click.echo(f"\n🧠 Facts stored: {stats['total_facts']}  "
               f"Distinct subjects: {stats['distinct_subjects']}")
    if "scenario_hashes" in stats:
        click.echo(f"   Scenario hashes: {stats['scenario_hashes']}")

    # --- Contradiction check ---
    if consolidate:
        click.echo("\n🔍 Scanning for contradictions …")
        dry = not prune
        suggestions = mc.find_contradictions(
            scenario_hash=scenario_hash, dry_run=dry
        )
        deletions = [s for s in suggestions if s["action"] == "delete"]

        if not deletions:
            click.echo("  ✅ No contradictions found.")
        else:
            click.echo(f"  ⚠  {len(deletions)} entry/entries flagged:\n")
            for s in deletions:
                click.echo(
                    f"  id={s['id']}  "
                    f"{s['subject']} {s['predicate']} \"{s['object']}\"  "
                    f"(confidence={s['confidence']:.2f})"
                )
                click.echo(f"          → {s['reason']}")
            if prune:
                click.echo(f"\n  🗑  {len(deletions)} fact(s) pruned.")
            else:
                click.echo(
                    "\n  Re-run with --consolidate --prune to apply deletions."
                )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _print_learn_result(result: dict) -> None:
    click.echo(
        f"\n✅  Learned  |  run_id={result['run_id']}  "
        f"turns={result['turns']}  facts={result['facts_stored']}"
        f"{'  [PARTIAL]' if result.get('interrupted') else ''}"
    )
