"""Prescription types — one per optimization opportunity.

Each prescription represents an actionable recommendation with estimated
dollar savings, effort level, and implementation hints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PrescriptionType(str, Enum):
    DEDUP_TOOL_CALLS       = "dedup_tool_calls"
    CONTEXT_COMPRESSION    = "context_compression"
    SCHEMA_SLIM            = "schema_slim"
    REPLACEMENT_CANDIDATE  = "replacement_candidate"
    LOOP_BREAK             = "loop_break"
    CACHE_WARM             = "cache_warm"


class Effort(str, Enum):
    LOW    = "low"     # < 1 hour to implement
    MEDIUM = "medium"  # half a day
    HIGH   = "high"    # multi-day


@dataclass
class Prescription:
    """A single ranked optimization recommendation."""
    type: PrescriptionType
    title: str
    description: str
    estimated_savings_pct: float    # 0–100
    estimated_savings_usd: float    # absolute $ per run
    confidence: float               # 0–1
    effort: Effort
    evidence: list[str] = field(default_factory=list)   # human-readable bullets
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def priority_score(self) -> float:
        """Higher is more worth acting on: savings × confidence / effort_factor."""
        effort_factor = {"low": 1.0, "medium": 2.0, "high": 4.0}[self.effort.value]
        return (self.estimated_savings_pct * self.confidence) / effort_factor

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "title": self.title,
            "description": self.description,
            "estimated_savings_pct": round(self.estimated_savings_pct, 2),
            "estimated_savings_usd": round(self.estimated_savings_usd, 6),
            "confidence": round(self.confidence, 3),
            "effort": self.effort.value,
            "priority_score": round(self.priority_score, 3),
            "evidence": self.evidence,
            "metadata": self.metadata,
        }


@dataclass
class OptimizationReport:
    """Full analysis output from the OptimizationAdvisor."""
    agent_name: str
    traces_analyzed: int
    total_cost_usd: float
    avg_cost_per_run: float
    avg_loop_risk: float
    prescriptions: list[Prescription] = field(default_factory=list)
    summary: str = ""

    def total_estimated_savings_pct(self) -> float:
        if not self.prescriptions:
            return 0.0
        # Prescriptions overlap so use the largest single saving as floor,
        # then add diminishing returns from subsequent ones.
        sorted_p = sorted(self.prescriptions, key=lambda p: p.estimated_savings_pct, reverse=True)
        result = 0.0
        remaining = 1.0
        for p in sorted_p:
            contribution = (p.estimated_savings_pct / 100.0) * remaining
            result += contribution
            remaining *= (1.0 - p.estimated_savings_pct / 100.0)
        return min(result * 100, 99.0)

    def total_estimated_savings_usd(self) -> float:
        return sum(p.estimated_savings_usd for p in self.prescriptions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "traces_analyzed": self.traces_analyzed,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_cost_per_run": round(self.avg_cost_per_run, 6),
            "avg_loop_risk": round(self.avg_loop_risk, 4),
            "total_estimated_savings_pct": round(self.total_estimated_savings_pct(), 2),
            "total_estimated_savings_usd": round(self.total_estimated_savings_usd(), 6),
            "summary": self.summary,
            "prescriptions": [p.to_dict() for p in self.prescriptions],
        }
