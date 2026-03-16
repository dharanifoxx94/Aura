# eidolon_vault/providers/ollama.py
import ollama
from typing import Dict, Any, Generator, Optional

class OllamaProvider:
    """Local-first provider using Ollama. Perfect for offline use on old hardware."""

    def __init__(self, model: str = "gemma3:4b"):
        self.model = model

    def generate(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.7) -> str:
        """Single response (non-streaming)"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = ollama.chat(
            model=self.model,
            messages=messages,
            options={"temperature": temperature}
        )
        return response['message']['content']

    def stream_generate(self, prompt: str, system_prompt: str = None) -> Generator[str, None, None]:
        """Streaming response (better UX for long agent runs)"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        stream = ollama.chat(
            model=self.model,
            messages=messages,
            stream=True,
            options={"temperature": 0.7}
        )
        for chunk in stream:
            yield chunk['message']['content']
