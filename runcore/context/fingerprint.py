from __future__ import annotations

import hashlib
import difflib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runcore.context.blocks import ContextBlock


def compute_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def are_similar(a: str, b: str, threshold: float = 0.85) -> bool:
    if a == b:
        return True
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold


def find_near_duplicates(
    blocks: list[ContextBlock],
) -> list[tuple[int, int, float]]:
    results: list[tuple[int, int, float]] = []
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            a = blocks[i].content
            b = blocks[j].content
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if ratio >= 0.85:
                results.append((i, j, ratio))
    return results
