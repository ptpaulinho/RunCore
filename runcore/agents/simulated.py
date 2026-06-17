"""Factory for simulated and real agents."""
from __future__ import annotations

from runcore.agents.base import BaseAgent
from runcore.agents.support import SupportAgent
from runcore.agents.research import ResearchAgent
from runcore.agents.coding import CodingAgent


class SimulatedAgentFactory:
    _registry = {
        "support": SupportAgent,
        "research": ResearchAgent,
        "coding": CodingAgent,
    }

    def create(self, agent_type: str) -> BaseAgent:
        # "real_support" routes to the Anthropic SDK agent
        if agent_type == "real_support":
            from runcore.agents.real import RealSupportAgent
            return RealSupportAgent()
        cls = self._registry.get(agent_type)
        if cls is None:
            raise ValueError(
                f"Unknown agent type '{agent_type}'. "
                f"Choose from: {list(self._registry) + ['real_support']}"
            )
        return cls()

    def get_all_agents(self) -> list[BaseAgent]:
        return [cls() for cls in self._registry.values()]
