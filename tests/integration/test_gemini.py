"""Integration tests for Gemini provider — real LLM calls with gemini-1.5-flash-8b (free)."""
import pytest
from tests.integration.conftest import requires_gemini
from runcore.providers.gemini import GeminiProvider
from runcore.providers.base import Message, ToolDefinition
import runcore
from benchmarks.tasks import SUPPORT_TASKS, RESEARCH_TASKS
from benchmarks.agents.base import BaseAgent


@requires_gemini
class TestGeminiProvider:
    def setup_method(self):
        self.provider = GeminiProvider(model="gemini-1.5-flash-8b")

    def test_is_available(self):
        assert self.provider.is_available()

    def test_simple_chat(self):
        msgs = [Message(role="user", content="Say exactly: hello world")]
        resp = self.provider.chat(msgs)
        assert resp.content
        assert resp.input_tokens > 0
        assert resp.output_tokens > 0
        assert resp.duration_ms > 0

    def test_chat_with_tool(self):
        tool = ToolDefinition(
            name="lookup_price",
            description="Look up the price of a product",
            parameters={
                "type": "object",
                "properties": {"product": {"type": "string"}},
                "required": ["product"],
            },
        )
        msgs = [Message(role="user", content="What is the price of a laptop?")]
        resp = self.provider.chat(msgs, tools=[tool])
        assert resp.tool_calls or resp.content

    def test_system_prompt_included(self):
        msgs = [
            Message(role="system", content="You are a pirate. Always say arrr."),
            Message(role="user", content="Hello there!"),
        ]
        resp = self.provider.chat(msgs)
        assert resp.content

    def test_cost_is_zero_for_free_model(self):
        msgs = [Message(role="user", content="Hi")]
        resp = self.provider.chat(msgs)
        assert resp.cost_usd == 0.0


@requires_gemini
class TestGeminiBenchmarkTask:
    def test_support_status_task(self):
        task = SUPPORT_TASKS[1]  # support_status_1
        provider = GeminiProvider()
        agent = BaseAgent(provider=provider)

        with runcore.capture(agent_name="test_gemini_support", task=task.user_message) as cap:
            run = agent.run(task, cap)

        assert run.task_id == "support_status_1"
        assert run.provider == "gemini"
        assert len(run.tool_calls_made) > 0

        trace = cap.get_atir()
        assert trace.aggregates.total_tokens > 0

    def test_research_task(self):
        task = RESEARCH_TASKS[0]
        provider = GeminiProvider()
        agent = BaseAgent(provider=provider)

        with runcore.capture(agent_name="test_gemini_research", task=task.user_message) as cap:
            run = agent.run(task, cap)

        assert run.task_id == "research_llm_cost_1"
        assert "web_search" in run.tool_calls_made
