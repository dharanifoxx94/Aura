"""
PSIE — Persona Generator
=========================
Turns knowledge‑graph entities into fully‑fleshed ``AgentPersona`` objects.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, List

import networkx as nx

from .models import AgentPersona, GraphEntity
from .utils import safe_parse_json, clamp, sanitise_injected_text
from .exceptions import PSIEError

if TYPE_CHECKING:
    from .llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

PERSONA_SYSTEM = """\
You are a behavioural psychologist and scenario designer.
Given an entity extracted from a scenario knowledge graph, generate a detailed
agent persona for use in a multi‑agent simulation.

RESPOND ONLY WITH VALID JSON:
{
  "role": "one-line role title",
  "archetype": "snake_case_archetype_label",
  "description": "3-5 sentence background",
  "openness": 0.0,
  "conscientiousness": 0.0,
  "extraversion": 0.0,
  "agreeableness": 0.0,
  "neuroticism": 0.0,
  "biases": ["list", "of", "cognitive", "biases"],
  "goals": ["primary goal WITH SPECIFIC NUMBERS from the scenario", "secondary goal WITH SPECIFIC NUMBERS"]
}

Archetype examples: hiring_manager, candidate, investor, negotiator,
regulator, customer, founder, advisor, peer, competitor

CRITICAL for goals: use the EXACT numbers from the scenario summary.
  BAD:  "Secure a significant equity stake"
  GOOD: "Secure exactly 30% equity stake for $500k investment"
  BAD:  "Get good funding terms"
  GOOD: "Maintain 70% equity while raising $500k at $5M valuation"

Big Five values must be floats between 0.0 and 1.0.
biases and goals must each contain 2-4 items.
Return ONLY the JSON object — no markdown, no explanation.\
"""

PERSONA_USER_TEMPLATE = """\
Generate a persona for this entity:

Name: {name}
Type: {entity_type}
Description: {description}
Scenario context: {scenario_summary}
Graph neighbourhood:
{graph_context}\
"""


class PersonaGenerator:
    """Generates ``AgentPersona`` objects from graph entities via LLM."""

    def __init__(self, gateway: "LLMGateway") -> None:
        self.gateway = gateway

    def generate(
        self,
        entity: GraphEntity,
        G: nx.DiGraph,
        scenario_summary: str,
        graph_context: str = "",
    ) -> AgentPersona:
        """Generate a persona for a single entity."""
        messages = [
            {"role": "system", "content": PERSONA_SYSTEM},
            {"role": "user", "content": PERSONA_USER_TEMPLATE.format(
                name=entity.name,
                entity_type=entity.entity_type,
                description=entity.description or "No description available",
                scenario_summary=scenario_summary[:300],
                graph_context=graph_context[:500] or "No graph context",
            )},
        ]

        try:
            raw = self.gateway.complete("persona_generate", messages, json_mode=True)
        except Exception as e:
            raise PSIEError(f"Persona generation failed for {entity.name}: {e}") from e

        data = safe_parse_json(raw)

        # Sanitise text fields before storing.
        role = sanitise_injected_text(str(data.get("role", entity.entity_type.title())))
        archetype = _sanitise_archetype(data.get("archetype", "general"))
        description = sanitise_injected_text(str(data.get("description", entity.description or "")))
        biases = [sanitise_injected_text(str(b)) for b in data.get("biases", []) if str(b).strip()][:4]
        goals = [sanitise_injected_text(str(g)) for g in data.get("goals", []) if str(g).strip()][:4]

        return AgentPersona(
            agent_id=str(uuid.uuid4())[:8],
            name=entity.name,
            role=role,
            archetype=archetype,
            description=description,
            openness=clamp(data.get("openness", 0.5)),
            conscientiousness=clamp(data.get("conscientiousness", 0.5)),
            extraversion=clamp(data.get("extraversion", 0.5)),
            agreeableness=clamp(data.get("agreeableness", 0.5)),
            neuroticism=clamp(data.get("neuroticism", 0.5)),
            biases=biases,
            goals=goals,
        )

    def generate_all(
        self,
        entities: List[GraphEntity],
        G: nx.DiGraph,
        scenario_summary: str,
        max_agents: int = 12,
        max_workers: int = 4,
    ) -> List[AgentPersona]:
        """
        Generate personas for the top-N entities (by graph degree), concurrently.
        Prioritises PERSON and ROLE entity types.

        Args:
            max_workers: Thread-pool size. Use 1 for serial behaviour (debugging).
                         4 is safe for Ollama; raise to 8 for cloud providers.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from typing import Optional

        def score(e: GraphEntity) -> float:
            type_bonus = 2.0 if e.entity_type in ("PERSON", "ROLE") else 0.0
            degree = int(G.degree(e.name)) if e.name in G else 0
            return type_bonus + degree

        def _generate_one(indexed_entity: tuple) -> tuple:
            """Return (original_index, AgentPersona) so ordering is preserved."""
            idx, entity = indexed_entity
            try:
                graph_ctx = _entity_neighbourhood_text(G, entity.name)
                persona = self.generate(entity, G, scenario_summary, graph_ctx)
                logger.info("  ✓ Persona: %s (%s)", persona.name, persona.archetype)
                return idx, persona
            except Exception as exc:
                logger.warning(
                    "Failed to generate persona for '%s' (%s) — using fallback. Error: %s",
                    entity.name, entity.entity_type, exc,
                )
                return idx, _fallback_persona(entity)

        valid_entities = [e for e in entities if e.name.strip()]
        sorted_entities = sorted(valid_entities, key=score, reverse=True)[:max_agents]

        # Pre-allocate slots so graph-degree order is preserved regardless of
        # which persona finishes first.
        slots: List[Optional[AgentPersona]] = [None] * len(sorted_entities)

        effective_workers = min(max_workers, len(sorted_entities) or 1)
        logger.info("Generating %d personas with %d worker(s)…", len(sorted_entities), effective_workers)

        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = {
                pool.submit(_generate_one, (i, e)): i
                for i, e in enumerate(sorted_entities)
            }
            for future in as_completed(futures):
                idx, persona = future.result()
                slots[idx] = persona

        return [p for p in slots if p is not None]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitise_archetype(text: object) -> str:
    """Convert arbitrary text into a snake_case archetype identifier."""
    s = re.sub(r"[^\w]", "_", str(text).lower().strip())
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")[:40]
    return s or "general"


def _entity_neighbourhood_text(G: nx.DiGraph, name: str) -> str:
    if name not in G:
        return ""
    seen: set[tuple] = set()
    lines: List[str] = []
    for src, tgt, edata in G.edges(data=True):
        if src == name or tgt == name:
            edge_key = (src, tgt)
            if edge_key not in seen:
                seen.add(edge_key)
                lines.append(f"[{src}] --{edata.get('relation', '?')}→ [{tgt}]")
    return "\n".join(lines[:10])


def _fallback_persona(entity: GraphEntity) -> AgentPersona:
    return AgentPersona(
        agent_id=str(uuid.uuid4())[:8],
        name=entity.name,
        role=entity.entity_type.title(),
        archetype="general",
        description=sanitise_injected_text(entity.description or f"A {entity.entity_type.lower()} in the scenario."),
        goals=["Achieve their primary objective"],
        biases=["Status quo bias"],
    )
