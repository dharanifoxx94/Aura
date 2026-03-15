# eidolon_vault/core.py
from typing import Optional, List
from .persistence import EidolonMemory
from .llm_gateway import LLMGateway
from .config import get_config

class Agent:
    def __init__(self, name: str, role: str = "Assistant", persistence: Optional[EidolonMemory] = None):
        self.name = name
        self.role = role
        self.persistence = persistence or EidolonMemory(name)
        cfg = get_config()
        self.gateway = LLMGateway(cfg)

    def think(self, prompt: str):
        # Retrieve recent memories to provide context
        memories = self.persistence.get_recent_memories(limit=5)
        context = "\n".join(memories)
        
        system_prompt = f"You are {self.name}, a {self.role}. Previous thoughts:\n{context}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # Use the LLM gateway to generate a response
        # We'll use 'agent_action' as the task type
        response = self.gateway.complete("agent_action", messages)
        
        # Save this thought/response to persistence
        self.persistence.save_memory(f"Thought: {prompt}\nResponse: {response}")
        
        print(f"[{self.name}] {response}")
        return response

    def generate_trajectory_report(self) -> str:
        memories = self.persistence.get_recent_memories(limit=100)
        report = f"# Trajectory Report for {self.name}\n\n"
        report += "## Recent Thoughts and Actions\n\n"
        for mem in reversed(memories):
            report += f"- {mem}\n\n"
        return report
