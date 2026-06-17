"""RunCore — Agent trace optimization and benchmarking toolkit."""

from runcore.trace.collector import TraceCollector
from runcore.benchmark.runner import BenchmarkRunner
from runcore.reports.generator import ReportGenerator
from runcore.tools.optimizer import ToolOptimizer
from runcore.context.compiler import ContextCompiler
from runcore.loops.detector import LoopDetector
from runcore.core.config import RunCoreConfig
from runcore.core.enums import BlockType, LoopPolicy, OptimizationResult, RunStatus, ToolCategory
from runcore.core.exceptions import (
    BenchmarkError,
    OptimizationError,
    QualityThresholdError,
    RunCoreError,
    TraceError,
)
from runcore.core.models import (
    AgentTrace,
    BenchmarkResult,
    LLMCall,
    OptimizationConfig,
    ToolCall,
)

from runcore.sdk.capture import Capture
from runcore.sdk.instrument import instrument, auto_instrument, uninstrument, instrument_object
from runcore.sdk.guards import GuardConfig, SavingsReport, DuplicateToolCallError, LoopBreakError
from runcore.sdk.cloud import configure, get_config, is_configured, push_trace, reset as reset_cloud
from runcore.atir.spec import ATIRTrace, ATIRAggregates, LLMSpan, ToolSpan, ATIR_VERSION
import runcore.atir as atir
import runcore.sdk as sdk


def capture(
    agent_name: str,
    task: str = "",
    framework: str = "unknown",
    guards: "GuardConfig | None" = None,
) -> Capture:
    """Create a :class:`~runcore.sdk.capture.Capture` context manager.

    Pass ``guards=GuardConfig()`` to activate runtime optimization::

        with runcore.capture("my_agent", guards=GuardConfig()) as cap:
            cap.record_tool("search", {"q": "foo"}, result, True, 12.0)
        print(cap.savings_report().summary_line())
    """
    return Capture(agent_name=agent_name, task=task, framework=framework, guards=guards)


__version__ = "0.7.0"

__all__ = [
    "__version__",
    # SDK
    "capture", "Capture", "instrument", "auto_instrument", "uninstrument", "instrument_object",
    "GuardConfig", "SavingsReport", "DuplicateToolCallError", "LoopBreakError",
    # Cloud
    "configure", "get_config", "is_configured", "push_trace", "reset_cloud",
    # ATIR
    "ATIRTrace", "ATIRAggregates", "LLMSpan", "ToolSpan", "ATIR_VERSION",
    "atir", "sdk",
    # top-level
    "TraceCollector",
    "BenchmarkRunner",
    "ReportGenerator",
    "ToolOptimizer",
    "ContextCompiler",
    "LoopDetector",
    # config
    "RunCoreConfig",
    # enums
    "BlockType",
    "LoopPolicy",
    "OptimizationResult",
    "RunStatus",
    "ToolCategory",
    # exceptions
    "BenchmarkError",
    "OptimizationError",
    "QualityThresholdError",
    "RunCoreError",
    "TraceError",
    # models
    "AgentTrace",
    "BenchmarkResult",
    "LLMCall",
    "OptimizationConfig",
    "ToolCall",
]
