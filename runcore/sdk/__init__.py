"""RunCore Universal SDK — 3-line integration for any AI agent.

Quick start::

    import runcore

    # Option 1 — context manager (manual recording)
    with runcore.capture("my_agent", task="classify intent") as tracer:
        tracer.record_llm(provider="anthropic", model="claude-haiku-4-5-20251001",
                          input_tokens=400, output_tokens=80, cost_usd=0.00015,
                          duration_ms=320)
        tracer.record_tool("get_invoice", {"id": "INV-1"}, {"amount": 99.99}, True, 12.0)

    trace = tracer.get_atir()   # ATIRTrace (ATIR v1)
    print(trace.aggregates.cost_per_successful_task)

    # Option 2 — auto-instrument LLM clients (zero-code)
    runcore.auto_instrument()

    with runcore.capture("my_agent") as tracer:
        response = anthropic.Anthropic().messages.create(...)  # captured automatically

    # Option 3 — decorator
    @runcore.instrument(agent_name="classifier")
    def classify(prompt):
        ...
"""
from runcore.sdk.capture import Capture
from runcore.sdk.instrument import instrument, auto_instrument, uninstrument, instrument_object
from runcore.sdk.guards import GuardConfig, GuardEngine, SavingsReport, DuplicateToolCallError, LoopBreakError
from runcore.sdk import context, adapters

# Re-export ATIR types for convenience
from runcore.atir.spec import ATIRTrace, ATIRAggregates, LLMSpan, ToolSpan, ATIR_VERSION
import runcore.atir as atir


def capture(
    agent_name: str,
    task: str = "",
    framework: str = "unknown",
    guards: "GuardConfig | None" = None,
) -> Capture:
    """Create a new :class:`Capture` context manager.

    Pass ``guards=GuardConfig()`` to activate runtime optimization guards::

        with runcore.capture("my_agent", task="process order", guards=GuardConfig()) as cap:
            cap.record_tool("search", {"q": "foo"}, result, True, 12.0)

        print(cap.savings_report().summary_line())
    """
    return Capture(agent_name=agent_name, task=task, framework=framework, guards=guards)


__all__ = [
    # Core
    "capture", "Capture",
    "instrument", "auto_instrument", "uninstrument", "instrument_object",
    # Guards
    "GuardConfig", "GuardEngine", "SavingsReport",
    "DuplicateToolCallError", "LoopBreakError",
    # ATIR types
    "ATIRTrace", "ATIRAggregates", "LLMSpan", "ToolSpan", "ATIR_VERSION",
    # Sub-modules
    "atir", "context", "adapters",
]
