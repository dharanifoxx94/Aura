"""
Eidolon Vault — Simulation Runner
=========================
Custom lightweight multi‑agent loop with skill injection, memory injection,
and persona anchoring.
"""

from __future__ import annotations

import logging
import re
import signal
import threading
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, List, Optional, Deque

from .models import AgentPersona, SimTurn, SimulationLog
from .skill_bank import SkillBank
from .memory_store import MemoryStore
from .utils import truncate
from .exceptions import SimulationError

if TYPE_CHECKING:
    from .llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

DEFAULT_PERSONA_ANCHOR_INTERVAL = 3
_MAX_INJECTED_ITEMS = 6

INITIAL_SITUATIONS: dict[str, str] = {
    "job_hunt":          "The hiring manager and candidate are meeting. The candidate wants the job; the manager must decide if the fit is right and negotiate compensation.",
    "business_decision": "The team is split on the key decision. Each person has a strong opinion and something to lose — the debate is about to begin.",
    "negotiation":       "The two parties are face-to-face. They have opposite interests and both know it. The negotiation starts NOW — open with your actual position.",
    "relationship":      "The key people are having the difficult conversation they have been avoiding. Tensions are real.",
    "general":           "The scenario is unfolding. Key actors have conflicting interests and must interact directly.",
}

class SimulationRunner:
    """
    Runs a multi‑agent simulation with skill/memory injection.
    """

    def __init__(
        self,
        gateway: "LLMGateway",
        skill_bank: SkillBank,
        memory_store: MemoryStore,
        cfg: dict,
    ) -> None:
        self.gateway = gateway
        self.skill_bank = skill_bank
        self.memory_store = memory_store
        self.max_agents: int = cfg["simulation"].get("max_agents", 12)
        self.max_turns: int = cfg["simulation"].get("max_turns", 15)
        self.anchor_interval: int = cfg["simulation"].get(
            "persona_anchor_interval", DEFAULT_PERSONA_ANCHOR_INTERVAL
        )
        self.max_injected_items: int = cfg["simulation"].get("max_injected_items", 6)
        self._interrupted = False
        self._original_sigint = None

    def _signal_handler(self, sig: int, frame: object) -> None:
        if self._interrupted:
            # Second Ctrl+C: restore default handler and kill immediately.
            # This escapes any blocking LLM call that ignored the first signal.
            logger.warning("Second interrupt received — forcing exit.")
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            sys.exit(130)
        logger.warning("Simulation interrupted — finishing current LLM call then stopping.")
        logger.warning("Press Ctrl+C again to force-quit immediately.")
        self._interrupted = True

    def run(
        self,
        personas: List[AgentPersona],
        scenario_title: str,
        scenario_hash: str,
        scenario_type: str = "general",
        num_turns: Optional[int] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> SimulationLog:
        """
        Run the simulation. Returns a completed (or partial) ``SimulationLog``.

        Ctrl+C behaviour:
          • First press  → sets _interrupted flag; loop exits after the current
                           LLM call returns (may still take a few seconds).
          • Second press → restores default SIGINT and exits immediately (sys.exit 130),
                           escaping any blocking litellm.completion() call.
        """
        if not personas:
            raise SimulationError("No personas provided for simulation.")

        self._interrupted = False

        # Only install the custom handler on the main thread; background threads
        # cannot call signal.signal() and will raise ValueError.
        _on_main_thread = (threading.current_thread() is threading.main_thread())
        if _on_main_thread:
            try:
                self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
            except ValueError:
                self._original_sigint = None
        else:
            self._original_sigint = None

        try:
            return self._run_impl(
                personas, scenario_title, scenario_hash,
                scenario_type, num_turns, progress_callback
            )
        finally:
            # Restore only if we installed ours (and it hasn't already been
            # replaced by the force-exit path in _signal_handler).
            if self._original_sigint is not None:
                try:
                    current = signal.getsignal(signal.SIGINT)
                    if current is self._signal_handler:
                        signal.signal(signal.SIGINT, self._original_sigint)
                except ValueError:
                    pass

    def _run_impl(
        self,
        personas: List[AgentPersona],
        scenario_title: str,
        scenario_hash: str,
        scenario_type: str,
        num_turns: Optional[int],
        progress_callback: Optional[Callable[[str], None]],
    ) -> SimulationLog:
        raw_turns   = num_turns if num_turns is not None else self.max_turns
        total_turns = max(1, min(raw_turns, self.max_turns))
        active_personas = personas[: self.max_agents]
        n_agents = len(active_personas)

        run_id = str(uuid.uuid4())[:12]
        sim_log = SimulationLog(
            run_id=run_id,
            scenario_title=scenario_title,
            scenario_hash=scenario_hash,
            agents=active_personas,
        )

        logger.info(
            "Starting simulation: run_id=%s, agents=%d, turns=%d",
            run_id, n_agents, total_turns,
        )

        history: Deque[dict] = deque(maxlen=20)
        situation = INITIAL_SITUATIONS.get(scenario_type, INITIAL_SITUATIONS["general"])
        cb = progress_callback or (lambda m: None)

        try:
            for turn_num in range(1, total_turns + 1):
                if self._interrupted:
                    logger.warning("Simulation interrupted by signal.")
                    cb("⚠ Simulation interrupted.")
                    break

                agent = active_personas[(turn_num - 1) % n_agents]

                # Inject skills and memories as per-turn local copies.
                # Never mutate the shared AgentPersona — prevents data races
                # if concurrent execution is added later.
                context_text = f"{scenario_title} {agent.role} {situation}"
                
                # Split total injection budget between skills and memories.
                # Default: half each.
                half_budget = self.max_injected_items // 2
                
                skills = self.skill_bank.get_skills_for(
                    archetype=agent.archetype,
                    scenario_type=scenario_type,
                    context_text=context_text,
                )
                turn_skills = [
                    s.instruction for s in skills[: half_budget]
                ]

                memories = self.memory_store.get_memories_for_agent(
                    agent_name=agent.name,
                    archetype=agent.archetype,
                    scenario_hash=scenario_hash,
                    context_text=context_text,
                )
                # Ensure we don't exceed the remaining budget if we wanted to be fancy,
                # but for now, sticking to the half-split logic is safer and simpler.
                turn_memories = memories[: half_budget]

                # Moderator prompt (template).
                mod_prompt = _moderator_prompt(
                    scenario_title=scenario_title,
                    situation=situation,
                    agent=agent,
                    turn_num=turn_num,
                    total_turns=total_turns,
                )

                # Build agent messages with per-turn copies (not agent.injected_*).
                anchor = (turn_num % self.anchor_interval == 1)
                agent_messages = _build_agent_messages(
                    agent=agent,
                    history=list(history),
                    mod_prompt=mod_prompt,
                    full_persona=anchor,
                    injected_skills=turn_skills,
                    injected_memories=turn_memories,
                )

                # LLM call.
                try:
                    response = self.gateway.complete(
                        "agent_action",
                        agent_messages,
                        max_tokens=300,
                        temperature=0.75,
                        progress_callback=cb,
                    )
                    backend_used = self.gateway.last_used_backend or "gateway"
                except Exception as exc:
                    logger.warning("Agent %s turn %d failed: %s", agent.name, turn_num, exc)
                    response = f"[{agent.name} pauses to consider the situation carefully…]"
                    backend_used = "error"

                # Update situation.
                situation = _derive_next_situation(response, situation, agent.name)

                # Log the turn.
                sim_turn = SimTurn(
                    turn_number=turn_num,
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                    prompt=mod_prompt,
                    response=response,
                    backend_used=backend_used,
                    tokens_used=max(1, len(response) // 4),
                )
                sim_log.turns.append(sim_turn)
                sim_log.total_tokens += sim_turn.tokens_used

                history.append({"role": "user", "content": f"[Moderator] {mod_prompt}"})
                # Strip prefix from the response before storing in history.
                # Do NOT re-add [Agent Name] — that is what causes subsequent turns
                # to echo the prefix (models copy what they see in history).
                clean_response = _strip_name_prefix(response, agent.name, agent.archetype)
                history.append({"role": "assistant", "content": clean_response})

                msg = f"Turn {turn_num}/{total_turns} — {agent.name}: {response[:80]}…"
                logger.info(msg)
                cb(msg)

        except KeyboardInterrupt:
            # This should not happen because we handle SIGINT, but keep as safety
            completed_turns = len(sim_log.turns)
            logger.warning(
                "Simulation interrupted by user after %d/%d turns.",
                completed_turns, total_turns,
            )
            cb(f"⚠ Simulation interrupted after {completed_turns} turn(s).")
            sim_log.scenario_title += " [PARTIAL]"

        sim_log.completed_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "Simulation complete: %d turns, ~%d tokens",
            len(sim_log.turns), sim_log.total_tokens,
        )
        return sim_log


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def _strip_name_prefix(response: str, agent_name: str, archetype: str = "") -> str:
    import re
    names = [re.escape(n) for n in [agent_name, archetype] if n and n.strip()]
    if names:
        alt = "|".join(names)
        p = re.compile(r"^(?:\[(?:" + alt + r")\]\s*|\((?:" + alt + r")\)\s*|(?:" + alt + r"):\s*)+", re.IGNORECASE)
        response = p.sub("", response).strip()
    response = re.sub(r"^(?:\[[^\]]{1,40}\]\s*|\([^)]{1,40}\)\s*)+", "", response).strip()
    return response


def _moderator_prompt(
    scenario_title: str,
    situation: str,
    agent: AgentPersona,
    turn_num: int,
    total_turns: int,
) -> str:
    urgency = ""
    if turn_num >= total_turns - 1:
        urgency = " This is nearly the final turn — make your position clear and decisive."
    elif turn_num > total_turns // 2:
        urgency = " Time is running short — push harder for what you need."

    goals_str = ""
    if agent.goals:
        goals_str = f" Your goals: {'; '.join(agent.goals[:2])}."

    return (
        f"[Turn {turn_num}/{total_turns} | {scenario_title}]\n\n"
        f"Situation: {situation}\n\n"
        f"You are {agent.name} ({agent.role}).{goals_str}\n"
        f"Respond ONLY as {agent.name} — do NOT start with your name or any tag like [{agent.name}].\n"
        f"Be direct, specific, and assertive about your interests.{urgency} "
        f"2 to 4 sentences."
    )


def _build_agent_messages(
    agent: AgentPersona,
    history: List[dict],
    mod_prompt: str,
    full_persona: bool,
    injected_skills: List[str] | None = None,
    injected_memories: List[str] | None = None,
) -> List[dict]:
    """Build the message list for one agent turn.
    injected_skills and injected_memories are per-turn copies — the shared
    AgentPersona object is never mutated.
    """
    import copy
    # Build a temporary persona with per-turn injections, leaving original clean
    if injected_skills is not None or injected_memories is not None:
        tmp = copy.copy(agent)
        tmp.injected_skills   = list(injected_skills   or [])
        tmp.injected_memories = list(injected_memories or [])
        persona_for_prompt = tmp
    else:
        persona_for_prompt = agent
    system_content = (
        persona_for_prompt.system_prompt() if full_persona
        else persona_for_prompt.brief_system_prompt()
    )
    messages: List[dict] = [{"role": "system", "content": system_content}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": mod_prompt})
    return messages


def _derive_next_situation(
    response: str, current_situation: str, agent_name: str
) -> str:
    sentences = [
        s.strip()
        for s in re.split(r"[.!?]", response)
        if len(s.strip()) > 20
    ]
    if sentences:
        last = truncate(sentences[-1], 200, notice="")
        return f"Following {agent_name}'s response: '{last}'"
    return current_situation
