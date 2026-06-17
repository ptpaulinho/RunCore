from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from runcore.core.enums import LoopPolicy


class RunCoreConfig(BaseModel):
    """Top-level configuration for the RunCore toolkit."""

    # --- Tracing ---
    trace_dir: str = Field(default="./traces", description="Directory where trace files are stored")
    trace_format: str = Field(default="json", description="Serialization format for traces: 'json' or 'jsonl'")

    # --- Optimization ---
    max_context_tokens: int = Field(
        default=100_000, ge=1_000, description="Hard limit on context tokens before compression is triggered"
    )
    loop_detection_window: int = Field(
        default=5, ge=1, description="Number of recent tool calls to inspect for repeated patterns"
    )
    loop_policy: LoopPolicy = Field(
        default=LoopPolicy.WARN, description="Action to take when a tool-call loop is detected"
    )
    cost_budget_usd: float = Field(
        default=1.0, ge=0.0, description="Maximum spend per run in USD before the agent is halted"
    )

    # --- Benchmarking ---
    benchmark_runs: int = Field(default=3, ge=1, description="Number of repetitions per benchmark evaluation")
    benchmark_output_dir: str = Field(default="./reports", description="Directory for benchmark report output")
    quality_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Minimum quality score for a benchmark run to be considered passing"
    )

    # --- Anthropic / LLM ---
    default_model: str = Field(default="claude-sonnet-4-5", description="Default Anthropic model to use")
    anthropic_api_key: Optional[str] = Field(
        default=None, description="Anthropic API key (falls back to ANTHROPIC_API_KEY env var if None)"
    )
    request_timeout_s: float = Field(default=60.0, ge=1.0, description="HTTP timeout for LLM requests in seconds")

    # --- Logging ---
    log_level: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL")
    log_file: Optional[str] = Field(default=None, description="Optional path to write log output to a file")

    @field_validator("trace_format")
    @classmethod
    def validate_trace_format(cls, v: str) -> str:
        allowed = {"json", "jsonl"}
        if v not in allowed:
            raise ValueError(f"trace_format must be one of {allowed}, got {v!r}")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return upper

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Serialize config to a JSON file at *path*."""
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> RunCoreConfig:
        """Deserialize config from a JSON file at *path*."""
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"Config file not found: {src}")
        raw: dict[str, Any] = json.loads(src.read_text(encoding="utf-8"))
        return cls.model_validate(raw)

    @classmethod
    def load_or_default(cls, path: str | Path) -> RunCoreConfig:
        """Return config from *path* if it exists, otherwise return defaults."""
        try:
            return cls.load(path)
        except FileNotFoundError:
            return cls()

    model_config = {"frozen": False}
