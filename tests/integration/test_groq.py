"""Integration tests for Groq provider — real LLM calls with free llama-3.1-8b."""
import pytest
from tests.integration.conftest import requires_groq
from runcore.providers.groq import GroqProvider
from runcore.providers.base import Message, ToolDefinition
import runcore
from benchmarks.tasks import SUPPORT_TASKS
from benchmarks.agents.base import BaseAgent


@requires_groq
class TestGroqProvider:
    def setup_method(self):
        self.provider = GroqProvider(model="llama-3.1-8b-instant")

    def test_is_available(self):
        assert self.provider.is_available()

    def test_simple_chat(self):
        msgs = [Message(role="user", content="Say exactly: hello world")]
        resp = self.provider.chat(msgs)
        assert resp.content
        assert resp.input_tokens > 0
        assert resp.output_tokens > 0
        assert resp.duration_ms > 0
        assert resp.model == "llama-3.1-8b-instant"

    def test_chat_with_tool(self):
        tool = ToolDefinition(
            name="get_weather",
            description="Get the weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        msgs = [Message(role="user", content="What's the weather in Lisbon?")]
        resp = self.provider.chat(msgs, tools=[tool])
        # LLM should call the tool
        assert resp.tool_calls or resp.content
        if resp.tool_calls:
            assert resp.tool_calls[0]["name"] == "get_weather"

    def test_cost_is_zero(self):
        msgs = [Message(role="user", content="Hi")]
        resp = self.provider.chat(msgs)
        # Groq free tier — cost should be 0
        assert resp.cost_usd == 0.0

    def test_stop_reason(self):
        msgs = [Message(role="user", content="Count to 3")]
        resp = self.provider.chat(msgs, max_tokens=100)
        assert resp.stop_reason in ("end_turn", "max_tokens", "tool_use")


@requires_groq
class TestGroqBenchmarkTask:
    def test_support_refund_task(self):
        task = SUPPORT_TASKS[0]  # support_refund_1
        provider = GroqProvider()
        agent = BaseAgent(provider=provider)

        with runcore.capture(agent_name="test_groq_support", task=task.user_message) as cap:
            run = agent.run(task, cap)

        assert run.task_id == "support_refund_1"
        assert run.provider == "groq"
        assert len(run.tool_calls_made) > 0
        # Should call lookup_customer and/or lookup_order
        assert any(t in run.tool_calls_made for t in ["lookup_customer", "lookup_order", "issue_refund"])

        trace = cap.get_atir()
        assert trace.aggregates.total_tokens > 0
        assert trace.aggregates.llm_calls >= 1

    def test_guarded_blocks_duplicates(self):
        task = SUPPORT_TASKS[0]
        provider = GroqProvider()
        guards = runcore.GuardConfig(dedup_enabled=True, loop_break_enabled=True)
        agent = BaseAgent(provider=provider, guards=guards)

        with runcore.capture(agent_name="test_groq_guarded", task=task.user_message, guards=guards) as cap:
            run = agent.run(task, cap)

        trace = cap.get_atir()
        # Guarded run should have 0 or fewer duplicate calls than baseline
        assert trace.aggregates.duplicate_tool_calls == 0
