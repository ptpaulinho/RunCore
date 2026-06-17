from __future__ import annotations

from runcore.context.blocks import BlockType, ContextBlock, classify_block, split_context_blocks
from runcore.context.cacheability import calculate_cacheability_score, estimate_cache_savings, reorder_for_cache
from runcore.context.dedupe import calculate_dedup_savings, deduplicate_blocks, merge_similar_blocks
from runcore.trace.tokens import count_tokens


class ContextCompiler:
    def __init__(self, dedup_threshold: float = 0.85, merge_threshold: float = 0.80):
        # Lower thresholds = more aggressive semantic dedup
        self.dedup_threshold = dedup_threshold
        self.merge_threshold = merge_threshold

    def compile(self, messages: list[dict], task: str = "") -> dict:
        # 1. Split into blocks
        blocks = split_context_blocks(messages)
        original_blocks = list(blocks)
        original_tokens = sum(b.tokens for b in original_blocks)

        # 2. Classify (stable vs dynamic)
        blocks = [classify_block(b) for b in blocks]

        # 3. Deduplicate exact duplicates
        blocks = deduplicate_blocks(blocks)

        # 4. Semantic near-duplicate merging (lower threshold = catches paraphrases)
        blocks = merge_similar_blocks(blocks, threshold=self.merge_threshold)

        # 5. Semantic compression — remove blocks whose content is a subset of another
        blocks = self._semantic_compress(blocks)

        # 6. Structural compress (noise removal + long tool result truncation)
        blocks = self._compress(blocks)

        # 7. Reorder for cache efficiency (stable blocks first)
        blocks = reorder_for_cache(blocks)

        # Recount with tiktoken for accuracy
        final_tokens = sum(count_tokens(b.content) for b in blocks)
        original_tokens_retok = sum(count_tokens(b.content) for b in original_blocks)

        blocks_removed = len(original_blocks) - len(blocks)
        token_reduction = original_tokens_retok - final_tokens
        cache_score = estimate_cache_savings(blocks)

        compiled_messages = self._blocks_to_messages(blocks)

        return {
            "compiled_messages": compiled_messages,
            "token_reduction": token_reduction,
            "blocks_removed": blocks_removed,
            "cache_score": round(cache_score, 4),
            "original_tokens": original_tokens_retok,
            "final_tokens": final_tokens,
        }

    def _semantic_compress(self, blocks: list[ContextBlock]) -> list[ContextBlock]:
        """Remove blocks whose content is mostly contained in a larger block."""
        import difflib
        keep = set(range(len(blocks)))
        for i in range(len(blocks)):
            if i not in keep:
                continue
            for j in range(len(blocks)):
                if i == j or j not in keep:
                    continue
                bi, bj = blocks[i], blocks[j]
                # Skip system blocks — always keep
                if bi.block_type == BlockType.SYSTEM or bj.block_type == BlockType.SYSTEM:
                    continue
                # If bj is much shorter and its content is substantially inside bi, drop bj
                if bj.tokens < bi.tokens * 0.6:
                    ratio = difflib.SequenceMatcher(None, bj.content, bi.content).ratio()
                    if ratio >= 0.88:
                        keep.discard(j)
        return [blocks[i] for i in sorted(keep)]

    def _compress(self, blocks: list[ContextBlock]) -> list[ContextBlock]:
        compressed: list[ContextBlock] = []
        for block in blocks:
            # Drop extremely small non-system blocks (likely noise)
            if block.block_type not in (BlockType.SYSTEM,) and block.tokens < 3:
                continue
            # Truncate very large tool results that are not stable
            if block.block_type == BlockType.TOOL_RESULT and not block.is_stable and block.tokens > 500:
                truncated = block.content[:1000] + "\n...[truncated]"
                block = ContextBlock(
                    content=truncated,
                    block_type=block.block_type,
                    is_stable=block.is_stable,
                    priority=block.priority,
                )
            compressed.append(block)
        return compressed

    def _blocks_to_messages(self, blocks: list[ContextBlock]) -> list[dict]:
        messages: list[dict] = []
        role_map = {
            BlockType.SYSTEM: "system",
            BlockType.USER: "user",
            BlockType.ASSISTANT: "assistant",
            BlockType.TOOL_CALL: "assistant",
            BlockType.TOOL_RESULT: "user",
            BlockType.UNKNOWN: "user",
        }
        for block in blocks:
            role = role_map.get(block.block_type, "user")
            messages.append({"role": role, "content": block.content})
        return messages
