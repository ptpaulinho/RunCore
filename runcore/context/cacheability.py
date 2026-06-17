from __future__ import annotations

from runcore.context.blocks import BlockType, ContextBlock

# Minimum token count considered "large enough" to benefit from caching
_LARGE_BLOCK_THRESHOLD = 100


def calculate_cacheability_score(block: ContextBlock) -> float:
    score = 0.0

    # Stability contributes 50%
    if block.is_stable:
        score += 0.5

    # Block type bonus
    type_bonus = {
        BlockType.SYSTEM: 0.3,
        BlockType.TOOL_RESULT: 0.15,
        BlockType.ASSISTANT: 0.05,
        BlockType.USER: 0.0,
        BlockType.TOOL_CALL: 0.05,
        BlockType.UNKNOWN: 0.0,
    }
    score += type_bonus.get(block.block_type, 0.0)

    # Size bonus (up to 0.2) — larger blocks benefit more from caching
    size_score = min(block.tokens / (_LARGE_BLOCK_THRESHOLD * 5), 0.2)
    score += size_score

    return min(score, 1.0)


def reorder_for_cache(blocks: list[ContextBlock]) -> list[ContextBlock]:
    return sorted(blocks, key=lambda b: calculate_cacheability_score(b), reverse=True)


def estimate_cache_savings(blocks: list[ContextBlock]) -> float:
    if not blocks:
        return 0.0
    total_tokens = sum(b.tokens for b in blocks)
    cacheable_tokens = sum(
        b.tokens for b in blocks if calculate_cacheability_score(b) >= 0.5
    )
    return cacheable_tokens / total_tokens if total_tokens > 0 else 0.0
