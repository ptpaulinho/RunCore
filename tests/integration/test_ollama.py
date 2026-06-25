"""Integration tests for Ollama provider — local free models."""
import pytest
from tests.integration.conftest import requires_ollama
from runcore.providers.ollama import OllamaProvider
from runcore.providers.base import Message, ToolDefinition
import runcore
from benchmarks.tasks import SUPPORT_TASKS
from benchmarks.agents.base import BaseAgent


@requires_ollama
class TestOllamaProvider:
    def setup_method(self):
        self.provider = OllamaProvider(model="llama3.2")

    def test_is_available(self):
        assert self.provider.is_available()

    def test_simple_chat(self):
        msgs = [Message(role="user", content="Say exactly: hello world")]
        resp = self.provider.chat(msgs)
        assert resp.content
        assert resp.duration_ms > 0
        assert resp.cost_usd == 0.0

    def test_cost_always_zero(self):
        msgs = [Message(role="user", content="What is 2+2?")]
        resp = self.provider.chat(msgs)
        assert resp.cost_usd == 0.0

    def test_token_estimation(self):
        msgs = [Message(role="user", content="Write a short poem about AI.")]
        resp = self.provider.chat(msgs, max_tokens=200)
        # Tokens may be estimated
        assert resp.input_tokens >= 0
        assert resp.output_tokens >= 0

    def test_system_prompt(self):
        msgs = [
            Message(role="system", content="Always respond in JSON format with key 'answer'."),
            Message(role="user", content="What is 1+1?"),
        ]
        resp = self.provider.chat(msgs)
        assert resp.content


@requires_ollama
class TestOllamaBenchmarkTask:
    def test_support_task(self):
        task = SUPPORT_TASKS[0]
        provider = OllamaProvider()
        agent = BaseAgent(provider=provider)

        with runcore.capture(agent_name="test_ollama_support", task=task.user_message) as cap:
            run = agent.run(task, cap)

        assert run.task_id == "support_refund_1"
        assert run.provider == "ollama"
        # Ollama may or may not support tool use depending on model
        # Just verify the loop ran without crashing
        assert run.final_answer is not None or run.error is not None

        trace = cap.get_atir()
        assert trace.aggregates.llm_calls >= 1
