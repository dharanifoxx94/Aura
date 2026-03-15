"""
Eidolon Vault — Graph Builder
=====================
Extracts entities and relationships from scenario text via LLM, builds a
NetworkX ``DiGraph``, and serialises it to GraphML for persistence.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple

import networkx as nx

from .models import GraphEntity, GraphRelation, ScenarioContext
from .input_parser import scenario_hash
from .utils import safe_parse_json, truncate
from .exceptions import GraphBuildError

if TYPE_CHECKING:
    from .llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

_ATTR_MAX = 500

EXTRACTION_SYSTEM = """\
You are a knowledge graph extraction assistant.
Given a scenario description, extract ALL relevant entities (people, organisations,
roles, concepts, events) and the relationships between them.

RESPOND ONLY WITH VALID JSON in this exact format:
{
  "entities": [
    {
      "name": "string",
      "entity_type": "PERSON|ORG|ROLE|CONCEPT|EVENT",
      "description": "one sentence"
    }
  ],
  "relations": [
    {
      "source": "entity name",
      "target": "entity name",
      "relation": "verb phrase",
      "weight": 1.0,
      "description": "one sentence"
    }
  ]
}

Rules:
- entity names must be consistent (same name = same entity)
- extract at most 20 entities — focus on the most important ones
- every entity should appear in at least one relation
- relations must use names that match entity names exactly
- weight is optional (default 1.0); use higher values for stronger relationships
- do NOT include markdown, only the JSON object\
"""

EXTRACTION_USER_TEMPLATE = """\
Extract the knowledge graph from this scenario:

---
{text}
---

Return only the JSON object.\
"""


class GraphBuilder:
    """Builds and persists knowledge graphs from scenario contexts."""

    def __init__(self, gateway: "LLMGateway", cfg: dict) -> None:
        self.gateway = gateway
        self.storage_dir = Path(os.path.expanduser(cfg["graph"]["storage_dir"]))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_entities: int = cfg["graph"].get("max_entities", 20)

    def build(
        self, ctx: ScenarioContext
    ) -> Tuple[nx.DiGraph, List[GraphEntity], List[GraphRelation]]:
        """
        Build a directed graph from a *ScenarioContext*.

        Loads from the GraphML cache when available; calls the LLM otherwise.
        Returns ``(graph, entities, relations)``.
        """
        h = scenario_hash(ctx)
        # Include max_entities in key — prevents stale cache when limit changes
        cache_path = self.storage_dir / f"{h}_e{self.max_entities}.graphml"

        if cache_path.exists():
            try:
                logger.info("Loading cached graph: %s", cache_path)
                G = nx.read_graphml(str(cache_path))
                return G, _graph_to_entities(G), _graph_to_relations(G)
            except Exception as e:
                logger.warning("Failed to load cached graph: %s — rebuilding", e)
                # Fall through to rebuild.

        logger.info("Extracting graph from scenario (first time — may take a moment)…")
        text = truncate(ctx.raw_text, 3000)

        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user",   "content": EXTRACTION_USER_TEMPLATE.format(text=text)},
        ]

        try:
            raw = self.gateway.complete("graph_build", messages, json_mode=True)
        except Exception as e:
            raise GraphBuildError(f"LLM call failed: {e}") from e

        data = safe_parse_json(raw, fallback={"entities": [], "relations": []})

        entities: List[GraphEntity] = [
            GraphEntity(
                name=str(e.get("name", "Unknown")).strip()[:100],
                entity_type=str(e.get("entity_type", "CONCEPT")).strip().upper()[:20],
                description=str(e.get("description", "")).strip()[:_ATTR_MAX],
            )
            for e in data.get("entities", [])[: self.max_entities]
            if e.get("name")
        ]

        relations: List[GraphRelation] = [
            GraphRelation(
                source=str(r.get("source", "")).strip()[:100],
                target=str(r.get("target", "")).strip()[:100],
                relation=str(r.get("relation", "related_to")).strip()[:100],
                weight=float(r.get("weight", 1.0)),
                description=str(r.get("description", "")).strip()[:_ATTR_MAX],
            )
            for r in data.get("relations", [])
            if r.get("source") and r.get("target")
        ]

        G = _build_nx_graph(entities, relations)

        if G.number_of_nodes() > 0:
            try:
                nx.write_graphml(G, str(cache_path))
                logger.info(
                    "Graph saved: %d nodes, %d edges → %s",
                    G.number_of_nodes(), G.number_of_edges(), cache_path,
                )
            except Exception as e:
                logger.warning("Failed to write graph cache: %s", e)
        else:
            logger.warning("LLM returned an empty graph — skipping cache write.")

        return G, entities, relations

    # Backward‑compat alias.
    def load_or_build(
        self, ctx: ScenarioContext
    ) -> Tuple[nx.DiGraph, List[GraphEntity], List[GraphRelation]]:
        return self.build(ctx)

    def get_stakeholders(self, G: nx.DiGraph) -> List[str]:
        """
        Return PERSON/ROLE node names sorted by total degree (most connected first).
        Falls back to all nodes if no PERSON/ROLE nodes are present.
        """
        person_nodes = [
            n for n, d in G.nodes(data=True)
            if d.get("entity_type", "") in ("PERSON", "ROLE")
        ]
        if not person_nodes:
            person_nodes = list(G.nodes())
        return sorted(person_nodes, key=lambda n: int(G.degree(n)), reverse=True)

    def get_context_for_entity(self, G: nx.DiGraph, entity_name: str) -> str:
        """Return a text summary of an entity's graph neighbourhood."""
        if entity_name not in G:
            return f"{entity_name}: no graph data"
        node_data = dict(G.nodes[entity_name])
        lines = [
            f"{entity_name} ({node_data.get('entity_type', 'UNKNOWN')}): "
            f"{node_data.get('description', '')}"
        ]
        for src, tgt, edata in G.edges(data=True):
            if src == entity_name or tgt == entity_name:
                lines.append(f"  [{src}] --{edata.get('relation', 'relates')}→ [{tgt}]")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_nx_graph(
    entities: List[GraphEntity], relations: List[GraphRelation]
) -> nx.DiGraph:
    G: nx.DiGraph = nx.DiGraph()
    entity_names = {e.name for e in entities}

    for e in entities:
        G.add_node(
            e.name,
            entity_type=e.entity_type,
            description=e.description[:_ATTR_MAX],
        )

    for r in relations:
        for node_name in (r.source, r.target):
            if node_name and node_name not in entity_names:
                G.add_node(node_name, entity_type="UNKNOWN", description="")
        if r.source and r.target:
            G.add_edge(
                r.source, r.target,
                relation=r.relation,
                description=r.description[:_ATTR_MAX],
                weight=r.weight,
            )
    return G


def _graph_to_entities(G: nx.DiGraph) -> List[GraphEntity]:
    return [
        GraphEntity(
            name=n,
            entity_type=str(d.get("entity_type", "UNKNOWN")),
            description=str(d.get("description", "")),
        )
        for n, d in G.nodes(data=True)
    ]


def _graph_to_relations(G: nx.DiGraph) -> List[GraphRelation]:
    return [
        GraphRelation(
            source=src,
            target=tgt,
            relation=str(edata.get("relation", "related")),
            weight=float(edata.get("weight", 1.0)),
            description=str(edata.get("description", "")),
        )
        for src, tgt, edata in G.edges(data=True)
    ]
