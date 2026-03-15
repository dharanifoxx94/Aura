"""
Eidolon Vault — Knowledge Worker
========================
Top-level orchestrator for the Knowledge Ingestion Pipeline.

Wires together:
  ContentFeeder  →  SimulationRunner  →  MemoryStore

Usage::

    from eidolon_vault.engine import EidolonVaultEngine
    from eidolon_vault.knowledge_worker import KnowledgeWorker

    engine = EidolonVaultEngine()
    worker = KnowledgeWorker(engine)
    result = worker.learn_from_source("https://example.com/article")
    print(result)

The worker uses *static* knowledge-verification personas loaded from
``agent_personas.yaml`` instead of the engine's dynamic persona generator.
This keeps the epistemics stable across ingestion runs and avoids spending
LLM tokens on persona synthesis for content-learning tasks.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .engine import EidolonVaultEngine
from .feeder import ContentFeeder
from .input_parser import scenario_hash
from .models import AgentPersona, ScenarioContext, SimulationLog
from .exceptions import EidolonVaultError

logger = logging.getLogger(__name__)

# Default path for the static personas file (co-located with this module).
_DEFAULT_PERSONAS_PATH = Path(__file__).parent / "agent_personas.yaml"


class KnowledgeWorker:
    """
    Orchestrates the full ingest → simulate → remember pipeline.

    Parameters
    ----------
    engine:
        A fully initialised ``EidolonVaultEngine`` instance that provides the
        gateway, simulation runner, memory store, and config.
    personas_path:
        Path to the YAML file that defines static verification personas.
        Defaults to ``eidolon_vault/agent_personas.yaml``.
    """

    def __init__(
        self,
        engine: EidolonVaultEngine,
        personas_path: Optional[str] = None,
    ) -> None:
        self.engine = engine
        self._personas_path = Path(personas_path or _DEFAULT_PERSONAS_PATH)
        self._feeder = ContentFeeder(
            gateway=engine.gateway,
            cfg=engine.cfg,
        )
        self._personas: Optional[List[AgentPersona]] = None  # lazy-loaded

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def learn_from_source(
        self,
        source: str,
        *,
        source_type: str = "auto",
        title: str = "",
        scenario_type: str = "general",
        num_turns: Optional[int] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Full pipeline: ingest *source*, run multi-agent verification discussion,
        store episodic memories, and extract semantic facts.

        Returns a summary dict with ``run_id``, ``facts_stored``,
        ``turns``, and ``scenario_title``.
        """
        cb = progress_callback or _noop

        cb(f"📥 Ingesting source ({source_type}) …")
        ctx = self._feeder.ingest(source, source_type=source_type, title=title)

        return self._run_pipeline(
            ctx,
            scenario_type=scenario_type,
            num_turns=num_turns,
            progress_callback=cb,
        )

    def learn_from_context(
        self,
        ctx: ScenarioContext,
        *,
        scenario_type: str = "general",
        num_turns: Optional[int] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Run the pipeline on an already-parsed ``ScenarioContext``.

        Useful when the caller wants fine-grained control over parsing
        (e.g. already called ``feeder.ingest_rss`` directly).
        """
        return self._run_pipeline(
            ctx,
            scenario_type=scenario_type,
            num_turns=num_turns,
            progress_callback=progress_callback or _noop,
        )

    def load_personas(self) -> List[AgentPersona]:
        """
        Load and cache static verification personas from YAML.

        Returns the cached list on subsequent calls.
        """
        if self._personas is None:
            self._personas = _load_personas_from_yaml(self._personas_path)
            logger.info(
                "Loaded %d static personas from %s",
                len(self._personas), self._personas_path,
            )
        return self._personas

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _run_pipeline(
        self,
        ctx: ScenarioContext,
        *,
        scenario_type: str,
        num_turns: Optional[int],
        progress_callback: Callable[[str], None],
    ) -> Dict[str, Any]:
        cb = progress_callback
        s_hash = scenario_hash(ctx)

        cb(f"🏷  Scenario: '{ctx.title}' (hash={s_hash})")

        personas = self.load_personas()
        if not personas:
            raise EidolonVaultError(
                f"No personas loaded from '{self._personas_path}'. "
                "Check that agent_personas.yaml exists and is well-formed."
            )

        effective_turns = (
            num_turns
            if num_turns is not None
            else self.engine.cfg["simulation"].get("max_turns", 10)
        )

        cb(f"🎭 Running {len(personas)}-agent verification discussion ({effective_turns} turns) …")
        sim_log: SimulationLog = self.engine.simulation_runner.run(
            personas=personas,
            scenario_title=ctx.title or "Knowledge Ingestion",
            scenario_hash=s_hash,
            scenario_type=scenario_type,
            num_turns=effective_turns,
            progress_callback=cb,
        )

        interrupted = sim_log.scenario_title.endswith("[PARTIAL]")

        if not interrupted:
            cb("💾 Storing episodic memories …")
            self.engine.memory_store.store_simulation(sim_log, s_hash)

            cb("🧬 Extracting semantic facts …")
            n_facts = self.engine.memory_store.extract_and_store_facts(
                sim_log, s_hash, self.engine.gateway
            )
        else:
            n_facts = 0
            cb("⚠  Skipping memory steps (simulation was interrupted).")

        result: Dict[str, Any] = {
            "run_id": sim_log.run_id,
            "scenario_title": sim_log.scenario_title,
            "scenario_hash": s_hash,
            "turns": len(sim_log.turns),
            "facts_stored": n_facts,
            "interrupted": interrupted,
        }

        cb(
            f"✅ Done. run_id={result['run_id']}  "
            f"turns={result['turns']}  facts={result['facts_stored']}"
        )
        logger.info("Knowledge ingestion complete: %s", result)
        return result


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def _load_personas_from_yaml(path: Path) -> List[AgentPersona]:
    """Parse ``agent_personas.yaml`` and return a list of ``AgentPersona`` objects."""
    if not path.exists():
        raise FileNotFoundError(
            f"Static personas file not found: {path}\n"
            "Copy eidolon_vault/agent_personas.yaml to the expected location."
        )

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    raw_list = (data or {}).get("personas", [])
    if not isinstance(raw_list, list) or not raw_list:
        raise ValueError(
            f"'personas' key missing or empty in {path}. "
            "Expected a list of persona dicts."
        )

    personas: List[AgentPersona] = []
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            logger.warning("Skipping non-dict persona entry #%d", i)
            continue
        try:
            persona = AgentPersona(
                agent_id=f"kw-{i:02d}-{uuid.uuid4().hex[:6]}",
                name=str(raw["name"]),
                role=str(raw.get("role", raw["name"])),
                archetype=str(raw.get("archetype", "analyst")),
                description=str(raw.get("description", "")),
                openness=float(raw.get("openness", 0.5)),
                conscientiousness=float(raw.get("conscientiousness", 0.5)),
                extraversion=float(raw.get("extraversion", 0.5)),
                agreeableness=float(raw.get("agreeableness", 0.5)),
                neuroticism=float(raw.get("neuroticism", 0.5)),
                biases=list(raw.get("biases", [])),
                goals=list(raw.get("goals", [])),
            )
            personas.append(persona)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping invalid persona entry #%d: %s", i, exc)

    return personas


def _noop(msg: str) -> None:  # pragma: no cover
    pass
