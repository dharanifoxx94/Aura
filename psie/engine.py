"""
PSIE — Engine
==============
Top‑level orchestrator that wires all components together.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

from .config import get_config, ensure_dirs
from .models import PredictionReport, SimulationLog
from .input_parser import parse_text, parse_file, parse_url, scenario_hash
from .graph_builder import GraphBuilder
from .persona_generator import PersonaGenerator
from .llm_gateway import LLMGateway
from .simulation_runner import SimulationRunner
from .skill_bank import SkillBank
from .memory_store import MemoryStore
from .report_generator import ReportGenerator
from .constants import ALLOWED_SCENARIO_TYPES
from .exceptions import PSIEError, InputError

logger = logging.getLogger(__name__)


class PSIEEngine:
    """
    Persistent Scenario Intelligence Engine — main entry point.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self.cfg = get_config(config_path)
        self._init_components()

    @classmethod
    def from_config(cls, cfg: dict) -> "PSIEEngine":
        instance = object.__new__(cls)
        instance.cfg = cfg
        instance._init_components()
        return instance

    def _init_components(self) -> None:
        ensure_dirs(self.cfg)
        self.gateway          = LLMGateway(self.cfg)
        self.graph_builder    = GraphBuilder(self.gateway, self.cfg)
        self.persona_generator = PersonaGenerator(self.gateway)
        self.skill_bank       = SkillBank(self.cfg)
        self.memory_store     = MemoryStore(self.cfg)
        self.simulation_runner = SimulationRunner(
            self.gateway, self.skill_bank, self.memory_store, self.cfg
        )
        self.report_generator  = ReportGenerator(self.gateway, self.cfg)

    # ------------------------------------------------------------------
    # High‑level run methods
    # ------------------------------------------------------------------

    def run_from_text(
        self,
        text: str,
        *,
        title: str = "",
        scenario_type: str = "general",
        num_turns: Optional[int] = None,
        max_agents: Optional[int] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[PredictionReport, SimulationLog]:
        self._validate_scenario_type(scenario_type)
        ctx = parse_text(text, title=title)
        return self._run(
            ctx,
            scenario_type=scenario_type,
            num_turns=num_turns,
            max_agents=max_agents,
            progress_callback=progress_callback,
        )

    def run_from_file(
        self,
        path: str,
        *,
        scenario_type: str = "general",
        num_turns: Optional[int] = None,
        max_agents: Optional[int] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[PredictionReport, SimulationLog]:
        self._validate_scenario_type(scenario_type)
        max_bytes = self.cfg.get("input", {}).get("max_file_bytes", 20 * 1024 * 1024)
        ctx = parse_file(path, max_bytes=max_bytes)
        return self._run(
            ctx,
            scenario_type=scenario_type,
            num_turns=num_turns,
            max_agents=max_agents,
            progress_callback=progress_callback,
        )

    def run_from_url(
        self,
        url: str,
        *,
        scenario_type: str = "general",
        num_turns: Optional[int] = None,
        max_agents: Optional[int] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        allow_private_ip: bool = False,
    ) -> Tuple[PredictionReport, SimulationLog]:
        self._validate_scenario_type(scenario_type)
        timeout_s = self.cfg.get("input", {}).get("url_timeout_s", 20)
        max_bytes = self.cfg.get("input", {}).get("max_file_bytes", 20 * 1024 * 1024)
        ctx = parse_url(url, timeout_s=timeout_s, allow_private_ip=allow_private_ip,
                        max_bytes=max_bytes)
        return self._run(
            ctx,
            scenario_type=scenario_type,
            num_turns=num_turns,
            max_agents=max_agents,
            progress_callback=progress_callback,
        )

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def _run(
        self,
        ctx,
        *,
        scenario_type: str,
        num_turns: Optional[int],
        max_agents: Optional[int],
        progress_callback: Optional[Callable[[str], None]],
    ) -> Tuple[PredictionReport, SimulationLog]:
        cb = progress_callback or (lambda m: None)

        cb("📖 Parsing scenario…")
        s_hash = scenario_hash(ctx)

        cb("🔍 Building knowledge graph…")
        G, entities, relations = self.graph_builder.build(ctx)
        logger.info("Graph: %d entities, %d relations", len(entities), len(relations))

        effective_max = max_agents or self.cfg["simulation"]["max_agents"]

        # Prefer PERSON and ROLE entities as agents — concepts and events
        # make poor simulation participants. Fall back to all entities only
        # when there are not enough people/roles to meet min_agents.
        people_entities = [
            e for e in entities
            if e.entity_type in ("PERSON", "ROLE")
        ]
        min_agents = 2
        candidate_entities = (
            people_entities
            if len(people_entities) >= min_agents
            else entities   # fallback: use everything if not enough people
        )
        if len(people_entities) < len(entities):
            logger.info(
                "Filtered %d concept/event entities — using %d PERSON/ROLE entities as agents.",
                len(entities) - len(people_entities), len(people_entities),
            )

        agent_count = min(effective_max, len(candidate_entities)) if candidate_entities else 0
        cb(f"🧠 Generating {agent_count} agent persona(s)…")
        personas = self.persona_generator.generate_all(
            entities=candidate_entities,
            G=G,
            scenario_summary=ctx.raw_text[:400],
            max_agents=effective_max,
        )

        if not personas:
            raise PSIEError(
                "No personas could be generated. "
                "Check your scenario text has identifiable stakeholders, "
                "and verify LLM connectivity."
            )

        cb(f"🎭 Running simulation with {len(personas)} agent(s)…")
        try:
            sim_log = self.simulation_runner.run(
                personas=personas,
                scenario_title=ctx.title or "Unnamed Scenario",
                scenario_hash=s_hash,
                scenario_type=scenario_type,
                num_turns=num_turns,
                progress_callback=cb,
            )
        except KeyboardInterrupt:
            # This should be caught by simulation_runner's signal handler, but just in case
            sim_log = SimulationLog(
                run_id="interrupted",
                scenario_title=ctx.title or "Unnamed Scenario",
                scenario_hash=s_hash,
            )
            cb("⚠ Simulation interrupted.")
            # Return early – no learning.
            report = PredictionReport(
                run_id=sim_log.run_id,
                scenario_title=sim_log.scenario_title,
                summary="Simulation was interrupted.",
                key_findings=[],
                predictions=[],
                recommended_actions=[],
                risks=[],
                confidence_overall=0.0,
            )
            return report, sim_log

        interrupted = sim_log.scenario_title.endswith("[PARTIAL]")

        if not interrupted:
            cb("💾 Storing episodic memories…")
            self.memory_store.store_simulation(sim_log, s_hash)

            cb("🧬 Extracting semantic facts…")
            self.memory_store.extract_and_store_facts(sim_log, s_hash, self.gateway)

            cb("📚 Mining behavioural skills…")
            self.skill_bank.extract_from_log(sim_log, self.gateway, scenario_type=scenario_type)
        else:
            cb("⚠ Skipping learning steps (simulation was interrupted).")

        cb("📊 Generating prediction report…")
        report = self.report_generator.generate(sim_log)

        cb(f"✅ Done! Run ID: {sim_log.run_id}")
        return report, sim_log

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_scenario_type(self, scenario_type: str) -> None:
        if scenario_type not in ALLOWED_SCENARIO_TYPES:
            raise InputError(f"Invalid scenario_type '{scenario_type}'. Allowed: {sorted(ALLOWED_SCENARIO_TYPES)}")
