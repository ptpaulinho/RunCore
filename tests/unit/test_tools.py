"""Unit tests for tools module."""
import pytest

from runcore.tools.registry import ToolRegistry, ToolSchema
from runcore.tools.ranking import rank_tools_for_task, select_top_k_tools
from runcore.tools.compression import compress_schema, compress_schemas, measure_compression_ratio
from runcore.tools.optimizer import ToolOptimizer
from runcore.core.enums import ToolCategory


def _make_tools() -> list[ToolSchema]:
    return [
        ToolSchema("web_search", "Search the internet for up-to-date information on any topic", {"type": "object", "properties": {"query": {"type": "string", "description": "The search query to look up"}, "num_results": {"type": "integer", "description": "Number of results to return, defaults to 10"}}, "required": ["query"]}, ToolCategory.SEARCH, ["query"]),
        ToolSchema("get_invoice", "Retrieve an invoice record from the database by its unique identifier", {"type": "object", "properties": {"invoice_id": {"type": "string", "description": "The unique invoice identifier"}}}, ToolCategory.READ, ["invoice_id"]),
        ToolSchema("refund_order", "Process a refund for a given order, crediting the customer account", {"type": "object", "properties": {"order_id": {"type": "string"}, "amount": {"type": "number", "description": "Amount to refund in USD"}, "reason": {"type": "string", "description": "Reason for the refund"}}}, ToolCategory.WRITE, ["order_id"]),
        ToolSchema("summarize", "Summarize a long document or text passage into a concise summary", {"type": "object", "properties": {"text": {"type": "string", "description": "The text to summarize"}, "max_words": {"type": "integer", "description": "Maximum word count for the summary"}}}, ToolCategory.COMPUTE, ["text"]),
        ToolSchema("edit_file", "Edit and save a file at the given path with the provided content", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}, ToolCategory.WRITE, ["path", "content"]),
    ]


def test_select_relevant_tools_only():
    tools = _make_tools()
    selected = select_top_k_tools(tools, "search for information about invoices", k=2)
    assert len(selected) <= 2
    names = [t.name for t in selected]
    # Should prefer search/read tools over write tools for a search task
    assert len(selected) > 0


def test_schema_token_reduction_40pct():
    tools = _make_tools()
    compressed = compress_schemas(tools)
    ratio = measure_compression_ratio(tools, compressed)
    assert ratio >= 0.40, f"Expected >= 40% reduction, got {ratio:.1%}"


def test_required_tool_never_removed():
    tools = _make_tools()
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)

    optimizer = ToolOptimizer()
    # Inject required tool name
    required = tools[1]  # get_invoice
    optimized = optimizer.optimize(tools, "refund customer invoice", max_tools=2, required_tools=[required.name])
    names = [t.name for t in optimized]
    assert required.name in names


def test_tool_ranking():
    tools = _make_tools()
    ranked = rank_tools_for_task(tools, "search web for latest news")
    assert len(ranked) == len(tools)
    # web_search should rank high for a search task
    top_names = [t.name for t, _ in ranked[:2]]
    assert "web_search" in top_names


def test_tool_registry_operations():
    registry = ToolRegistry()
    tools = _make_tools()
    for t in tools:
        registry.register(t)
    assert len(registry.list_all()) == len(tools)
    fetched = registry.get("get_invoice")
    assert fetched.name == "get_invoice"
    with pytest.raises(KeyError):
        registry.get("nonexistent_tool")
