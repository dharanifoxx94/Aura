# eidolon_vault/cli.py
import logging
import sys
from pathlib import Path
import click

import eidolon_vault as _eidolon_vault_pkg
from eidolon_vault.log import setup_logging
from eidolon_vault.exceptions import EidolonVaultError

BANNER = r"""
 ____  ____ ___ _____
|  _ \/ ___|_ _| ____|
| |_) \___ \| ||  _|
|  __/ ___) | || |___
|_|   |____/___|_____|

Persistent Scenario Intelligence Engine v{version}
""".format(version=_eidolon_vault_pkg.__version__)

SCENARIO_TYPES = ["job_hunt", "business_decision", "negotiation", "relationship", "general"]


@click.group()
@click.version_option(version=_eidolon_vault_pkg.__version__, prog_name="eidolon_vault")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option("--json-log", is_flag=True, help="Output logs in JSON format")
def cli(verbose: bool, json_log: bool) -> None:
    """Eidolon Vault — Predict outcomes through multi‑agent simulation."""
    setup_logging(verbose=verbose, json_output=json_log)


@cli.command()
@click.option("--text",      "-t", default=None, help="Scenario text (inline)")
@click.option("--file",      "-f", default=None, type=click.Path(exists=True), help="Path to .txt/.pdf/.docx")
@click.option("--url",       "-u", default=None, help="URL to fetch scenario from")
@click.option("--type",      "-s", "scenario_type", default="general",
              type=click.Choice(SCENARIO_TYPES), show_default=True,
              help="Scenario classification")
@click.option("--turns",           default=None, type=click.IntRange(min=1), help="Number of simulation turns")
@click.option("--agents",          default=None, type=click.IntRange(min=2, max=12), help="Max agents (2–12)")
@click.option("--title",           default="", help="Optional scenario title")
@click.option("--provider",        default=None, help="LLM provider (e.g. ollama, gemini, groq)")
@click.option("--model",           default=None, help="LLM model name")
@click.option("--sensitive",       is_flag=True, help="Force local‑only inference (privacy mode)")
@click.option("--config",          default=None, type=click.Path(), help="Path to config.yaml")
@click.option("--quiet",     "-q", is_flag=True, help="Suppress progress output")
@click.option("--allow-private-ip", is_flag=True, help="Allow fetching from private IP addresses (SSRF risk)")
def run(
    text: str | None,
    file: str | None,
    url: str | None,
    scenario_type: str,
    turns: int | None,
    agents: int | None,
    title: str,
    provider: str | None,
    model: str | None,
    sensitive: bool,
    config: str | None,
    quiet: bool,
    allow_private_ip: bool,
) -> None:
    """Run a scenario simulation and generate a prediction report."""
    if not text and not file and not url:
        click.echo(BANNER)
        click.echo("No input provided. Enter your scenario below (Ctrl+D to finish):\n")
        try:
            text = sys.stdin.read().strip()
        except KeyboardInterrupt:
            click.echo("\nCancelled.")
            sys.exit(0)
        if not text:
            click.echo("Error: No scenario text provided.")
            sys.exit(1)

    if not quiet:
        click.echo(BANNER)

    try:
        from eidolon_vault.config import get_config, ensure_dirs
        from eidolon_vault.engine import EidolonVaultEngine
    except ImportError as e:
        click.echo(f"Error importing Eidolon Vault: {e}\nRun: pip install -e .", err=True)
        sys.exit(1)

    try:
        cfg = get_config(config)
    except EidolonVaultError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    if provider:
        cfg["llm"]["provider"] = provider
    if model:
        cfg["llm"]["model"] = model

    if sensitive:
        cfg["simulation"]["sensitive_mode"] = True
        if not quiet:
            click.echo("🔒 Sensitive mode active — local inference only, no data leaves this machine.\n")

    engine = EidolonVaultEngine.from_config(cfg)

    def _progress(msg: str) -> None:
        if not quiet:
            click.echo(f"  {msg}")

    try:
        if file:
            report, sim_log = engine.run_from_file(
                file, scenario_type=scenario_type, num_turns=turns,
                max_agents=agents, progress_callback=_progress,
            )
        elif url:
            report, sim_log = engine.run_from_url(
                url, scenario_type=scenario_type, num_turns=turns,
                max_agents=agents, progress_callback=_progress,
                allow_private_ip=allow_private_ip,
            )
        else:
            report, sim_log = engine.run_from_text(
                text, title=title, scenario_type=scenario_type,
                num_turns=turns, max_agents=agents, progress_callback=_progress,
            )
    except KeyboardInterrupt:
        click.echo("\n⚠ Interrupted by user.", err=True)
        sys.exit(130)
    except EidolonVaultError as exc:
        click.echo(f"\n❌ Simulation failed: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"\n❌ Unexpected error: {exc}", err=True)
        sys.exit(1)

    rendered = engine.report_generator.render_text(report, sim_log)
    click.echo("\n" + rendered)


@cli.command()
@click.option("--limit",  default=20, help="Number of past runs to show")
@click.option("--config", default=None, type=click.Path())
def history(limit: int, config: str | None) -> None:
    """Show past simulation runs."""
    from eidolon_vault.config import get_config, ensure_dirs
    from eidolon_vault.memory_store import MemoryStore

    try:
        cfg = get_config(config)
    except EidolonVaultError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    ensure_dirs(cfg)
    store = MemoryStore(cfg)
    runs = store.list_runs(limit=limit)

    if not runs:
        click.echo("No simulation runs found yet. Run a simulation first.")
        return

    click.echo(f"\n{'RUN ID':<15} {'SCENARIO HASH':<18} {'TURNS':<8} LAST TURN")
    click.echo("─" * 65)
    for r in runs:
        last = r["last_turn"][:19] if r["last_turn"] else "unknown"
        click.echo(f"{r['run_id']:<15} {r['scenario_hash']:<18} {r['turns']:<8} {last}")


@cli.group()
def skills() -> None:
    """Manage the skill bank."""


@skills.command(name="list")
@click.option("--config", default=None, type=click.Path())
def skills_list(config: str | None) -> None:
    """List all learned skills."""
    from eidolon_vault.config import get_config, ensure_dirs
    from eidolon_vault.skill_bank import SkillBank

    try:
        cfg = get_config(config)
    except EidolonVaultError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    ensure_dirs(cfg)
    bank = SkillBank(cfg)
    all_skills = bank.list_all()

    if not all_skills:
        click.echo("No skills in bank yet. Run some simulations first.")
        return

    click.echo(f"\n{'ID':<5} {'NAME':<30} {'ARCHETYPE':<20} {'SUCCESS':<9} TRIGGER")
    click.echo("─" * 90)
    for s in all_skills:
        click.echo(
            f"{s.skill_id:<5} {s.name[:28]:<30} {s.archetype_filter[:18]:<20} "
            f"{s.success_count:<9} {s.trigger[:30]}"
        )


@skills.command(name="delete")
@click.argument("skill_id", type=int)
@click.option("--config", default=None, type=click.Path())
def skills_delete(skill_id: int, config: str | None) -> None:
    """Delete a skill by ID."""
    from eidolon_vault.config import get_config, ensure_dirs
    from eidolon_vault.skill_bank import SkillBank

    try:
        cfg = get_config(config)
    except EidolonVaultError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    ensure_dirs(cfg)
    bank = SkillBank(cfg)
    bank.delete(skill_id)
    click.echo(f"Skill {skill_id} deleted.")


@cli.command()
@click.option("--config", default=None, type=click.Path())
def cost(config: str | None) -> None:
    """Show recent LLM usage log."""
    from eidolon_vault.config import get_config
    from eidolon_vault.llm_gateway import LLMGateway

    try:
        cfg = get_config(config)
    except EidolonVaultError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    gw = LLMGateway(cfg)
    rows = gw.get_cost_summary()

    if not rows:
        click.echo("No usage logged yet.")
        return

    click.echo(f"\n{'TASK':<20} {'BACKEND':<35} {'TOKENS':<10} AT")
    click.echo("─" * 80)
    for r in rows:
        click.echo(f"{r['task']:<20} {r['backend']:<35} {r['tokens']:<10} {r['at'][:19]}")

    total = sum(r["tokens"] for r in rows)
    click.echo(f"\nTotal tokens logged: {total:,}")


@cli.command()
def init() -> None:
    """Create a default config file at ~/.eidolon_vault/config.yaml and set up directories."""
    config_dir = Path.home() / ".eidolon_vault"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"

    if config_path.exists():
        if not click.confirm(f"{config_path} already exists. Overwrite?"):
            return

    default_yaml = """\
# Eidolon Vault Configuration
llm:
  provider: ollama
  model: gemma3:4b
  providers:
    ollama:
      base_url: "http://localhost:11434"
    groq:
      api_key: ""
    gemini:
      api_key: ""
    openrouter:
      api_key: ""

  retry_attempts: 2
  retry_delay_s: 3.0
  request_timeout: 60

simulation:
  max_agents: 8
  max_turns: 12
  sensitive_mode: false

graph:
  max_entities: 15

memory:
  max_semantic_inject: 4
  max_total_episodes: 5000

skills:
  top_k_inject: 3

output:
  reports_dir: "~/eidolon_vault_reports"

input:
  max_file_bytes: 20971520   # 20 MB
  url_timeout_s: 20
  allow_private_ip_url: false
"""
    config_path.write_text(default_yaml, encoding="utf-8")
    click.echo(f"✓ Config created at {config_path}")

    from eidolon_vault.config import get_config, ensure_dirs
    cfg = get_config(str(config_path))
    ensure_dirs(cfg)
    click.echo("✓ Runtime directories created.")

    click.echo("\nNext steps:")
    click.echo("  1. Add your API keys to ~/.eidolon_vault/config.yaml")
    click.echo("  2. Pull an Ollama model: ollama pull gemma3:4b")
    click.echo("  3. Run: eidolon_vault run --text 'Your scenario here' --type job_hunt")


# Step 5: Add CLI command for the consciousness demo
@cli.group()
def demo() -> None:
    """Run built-in demos."""


@demo.command(name="consciousness")
@click.option("--days", default=10, help="Number of simulated days")
def demo_consciousness(days: int) -> None:
    """Run the consciousness debate demo."""
    # Importing inside the function as requested
    from demo.consciousness_debate import run_consciousness_debate
    click.echo(f"Starting consciousness debate demo for {days} days...")
    run_consciousness_debate(days=days)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
