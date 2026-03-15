# eidolon_vault/core.py
from typing import Optional, List
from .persistence import EidolonMemory
from .llm_gateway import LLMGateway
from .config import get_config

class Agent:
    """
    Core agent class for Eidolon-Vault. 
    Wired with a persistent memory layer to remember across runs.
    """

    def __init__(self, name: str, role: str = "Assistant", persistence: Optional[EidolonMemory] = None):
        self.name = name
        self.role = role
        # Step 3: Wire persistence into the agent
        self.persistence = persistence or EidolonMemory(self.name)
        cfg = get_config()
        self.gateway = LLMGateway(cfg)

    def think(self, prompt: str):
        """
        Process a thought or interaction using the LLM and memory context.
        Uses hybrid retrieval: Recent (SQLite) + Semantic (ChromaDB).
        """
        # Retrieve context for continuity
        recent_memories = self.persistence.get_recent_memories(limit=3)
        semantic_memories = self.persistence.search_memories(prompt, n_results=3)
        
        # Combine and deduplicate if necessary (simple union for now)
        all_context = list(dict.fromkeys(recent_memories + semantic_memories))
        context_str = "\n".join(all_context)
        
        system_prompt = (
            f"You are {self.name}, a {self.role}.\n"
            f"Relevant Context from Vault:\n{context_str}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # Call the LLM
        response = self.gateway.complete("agent_action", messages)
        
        # Save memory
        self.persistence.save_memory(f"Thought: {prompt}\nResponse: {response}")
        
        print(f"[{self.name}] {response}")
        return response

    def generate_trajectory_report(self) -> str:
        """
        Produce a trajectory report based on accumulated memories.
        """
        memories = self.persistence.get_recent_memories(limit=100)
        report = f"# Trajectory Report for {self.name}\n\n"
        report += "## Historical Log of Interactions\n\n"
        for mem in reversed(memories):
            report += f"- {mem}\n\n"
        return report
