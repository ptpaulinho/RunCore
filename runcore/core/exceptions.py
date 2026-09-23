from __future__ import annotations

from typing import Optional


class RunCoreError(Exception):
    """Base exception for all RunCore errors."""

    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, details={self.details!r})"


class TraceError(RunCoreError):
    """Raised when an agent trace cannot be parsed, validated, or processed."""


class OptimizationError(RunCoreError):
    """Raised when an optimization step fails or produces an invalid result."""


class BenchmarkError(RunCoreError):
    """Raised when a benchmark run encounters an unrecoverable error."""


class QualityThresholdError(RunCoreError):
    """Raised when an optimized trace falls below the required quality threshold."""

    def __init__(self, message: str, score: float, threshold: float, details: Optional[dict] = None) -> None:
        super().__init__(message, details)
        self.score = score
        self.threshold = threshold

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"score={self.score}, "
            f"threshold={self.threshold}, "
            f"details={self.details!r})"
        )
