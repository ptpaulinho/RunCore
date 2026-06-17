from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

from runcore.context.blocks import ContextBlock
from runcore.context.fingerprint import find_near_duplicates

if TYPE_CHECKING:
    pass


def deduplicate_blocks(blocks: list[ContextBlock]) -> list[ContextBlock]:
    seen_fingerprints: set[str] = set()
    deduped: list[ContextBlock] = []
    for block in blocks:
        if block.fingerprint not in seen_fingerprints:
            seen_fingerprints.add(block.fingerprint)
            deduped.append(block)
    return deduped


def merge_similar_blocks(
    blocks: list[ContextBlock], threshold: float = 0.9
) -> list[ContextBlock]:
    if not blocks:
        return blocks

    near_dupes = find_near_duplicates(blocks)
    indices_to_remove: set[int] = set()

    for i, j, ratio in near_dupes:
        if ratio >= threshold and j not in indices_to_remove:
            # Keep the longer/higher-priority block
            if blocks[i].tokens >= blocks[j].tokens:
                indices_to_remove.add(j)
            else:
                indices_to_remove.add(i)

    return [b for idx, b in enumerate(blocks) if idx not in indices_to_remove]


def calculate_dedup_savings(
    original: list[ContextBlock], deduped: list[ContextBlock]
) -> dict:
    original_tokens = sum(b.tokens for b in original)
    deduped_tokens = sum(b.tokens for b in deduped)
    removed_blocks = len(original) - len(deduped)
    saved_tokens = original_tokens - deduped_tokens
    reduction_pct = (saved_tokens / original_tokens * 100) if original_tokens > 0 else 0.0

    return {
        "original_blocks": len(original),
        "deduped_blocks": len(deduped),
        "removed_blocks": removed_blocks,
        "original_tokens": original_tokens,
        "deduped_tokens": deduped_tokens,
        "saved_tokens": saved_tokens,
        "reduction_percent": round(reduction_pct, 2),
    }
