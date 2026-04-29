"""Local LLM integration (Ollama) and structured agent output parsing."""

from app.llm.ollama import ollama_chat
from app.llm.schema import AgentPlan

__all__ = ["AgentPlan", "ollama_chat"]
