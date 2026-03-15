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
        """
        # Retrieve recent memories for contextual continuity
        memories = self.persistence.get_recent_memories(limit=5)
        context = "\n".join(memories)
        
        system_prompt = f"You are {self.name}, a {self.role}. Previous memory:\n{context}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # Call the LLM (using agent_action as the task type)
        response = self.gateway.complete("agent_action", messages)
        
        # Step 3: Save memory using persistence
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
