from runcore.context.blocks import BlockType, ContextBlock, classify_block, split_context_blocks
from runcore.context.cacheability import calculate_cacheability_score, estimate_cache_savings, reorder_for_cache
from runcore.context.compiler import ContextCompiler
from runcore.context.dedupe import calculate_dedup_savings, deduplicate_blocks, merge_similar_blocks
from runcore.context.fingerprint import are_similar, compute_fingerprint, find_near_duplicates

__all__ = [
    "BlockType",
    "ContextBlock",
    "classify_block",
    "split_context_blocks",
    "calculate_cacheability_score",
    "estimate_cache_savings",
    "reorder_for_cache",
    "ContextCompiler",
    "calculate_dedup_savings",
    "deduplicate_blocks",
    "merge_similar_blocks",
    "are_similar",
    "compute_fingerprint",
    "find_near_duplicates",
]
