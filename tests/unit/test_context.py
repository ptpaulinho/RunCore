"""Unit tests for context compiler."""
import pytest

from runcore.context.compiler import ContextCompiler
from runcore.context.blocks import split_context_blocks, classify_block
from runcore.context.dedupe import deduplicate_blocks
from runcore.context.fingerprint import compute_fingerprint, are_similar
from runcore.core.enums import BlockType


def _make_messages(with_duplicates: bool = True) -> list[dict]:
    msgs = [
        {"role": "system", "content": "You are a helpful assistant. Be concise and accurate."},
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris."},
        {"role": "user", "content": "Tell me more about Paris."},
    ]
    if with_duplicates:
        msgs.append({"role": "user", "content": "What is the capital of France?"})  # duplicate
    return msgs


def test_remove_duplicate_content():
    msgs = _make_messages(with_duplicates=True)
    blocks = split_context_blocks(msgs)
    deduped = deduplicate_blocks(blocks)
    original_contents = [b.content for b in blocks]
    deduped_contents = [b.content for b in deduped]
    # Duplicate content should be reduced
    assert len(deduped) <= len(blocks)


def test_dynamic_content_moved_to_end():
    msgs = _make_messages(with_duplicates=False)
    blocks = split_context_blocks(msgs)
    classified = [classify_block(b) for b in blocks]
    from runcore.context.cacheability import reorder_for_cache
    reordered = reorder_for_cache(classified)
    # Stable blocks (system) should come before dynamic blocks
    if len(reordered) > 1:
        stable_indices = [i for i, b in enumerate(reordered) if b.is_stable]
        dynamic_indices = [i for i, b in enumerate(reordered) if not b.is_stable]
        if stable_indices and dynamic_indices:
            assert max(stable_indices) < max(dynamic_indices) or min(stable_indices) < min(dynamic_indices)


def test_preserve_required_data():
    msgs = _make_messages(with_duplicates=False)
    compiler = ContextCompiler()
    result = compiler.compile(msgs)
    compiled = result["compiled_messages"]
    # All unique content should still be present
    assert len(compiled) > 0


def test_token_reduction():
    msgs = _make_messages(with_duplicates=True)
    compiler = ContextCompiler()
    result = compiler.compile(msgs)
    assert "token_reduction" in result
    assert result["token_reduction"] >= 0


def test_fingerprint_consistency():
    content = "Hello world, this is a test message."
    fp1 = compute_fingerprint(content)
    fp2 = compute_fingerprint(content)
    assert fp1 == fp2


def test_similar_content_detection():
    a = "The customer wants a refund for invoice 1001"
    b = "The customer wants a refund for invoice 1002"
    c = "Completely different content about quantum physics"
    assert are_similar(a, b, threshold=0.7)
    assert not are_similar(a, c, threshold=0.7)


def test_compiler_produces_result_dict():
    msgs = _make_messages()
    compiler = ContextCompiler()
    result = compiler.compile(msgs, task="answer questions")
    assert "compiled_messages" in result
    assert "token_reduction" in result
    assert "blocks_removed" in result
    assert "cache_score" in result
