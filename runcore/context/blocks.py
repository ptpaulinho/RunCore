from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class BlockType(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    UNKNOWN = "unknown"


@dataclass
class ContextBlock:
    content: str
    block_type: BlockType
    tokens: int = 0
    is_stable: bool = False
    priority: int = 0
    fingerprint: str = ""

    def __post_init__(self):
        if not self.tokens:
            self.tokens = max(1, len(self.content) // 4)
        if not self.fingerprint:
            self.fingerprint = hashlib.md5(self.content.encode()).hexdigest()


def split_context_blocks(messages: list[dict]) -> list[ContextBlock]:
    blocks: list[ContextBlock] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            block_type = BlockType.SYSTEM
        elif role == "user":
            block_type = BlockType.USER
        elif role == "assistant":
            block_type = BlockType.ASSISTANT
        else:
            block_type = BlockType.UNKNOWN

        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    part_type = part.get("type", "")
                    text = part.get("text") or part.get("content") or str(part)
                    if part_type == "tool_use":
                        bt = BlockType.TOOL_CALL
                    elif part_type == "tool_result":
                        bt = BlockType.TOOL_RESULT
                    else:
                        bt = block_type
                    blocks.append(ContextBlock(content=text, block_type=bt))
                else:
                    blocks.append(ContextBlock(content=str(part), block_type=block_type))
        else:
            blocks.append(ContextBlock(content=str(content), block_type=block_type))

    return blocks


def classify_block(block: ContextBlock) -> ContextBlock:
    stable_indicators = [
        block.block_type == BlockType.SYSTEM,
        block.tokens > 200,
        len(block.content) > 500,
        block.block_type == BlockType.TOOL_RESULT and block.tokens > 100,
    ]

    dynamic_indicators = [
        block.block_type == BlockType.USER,
        block.block_type == BlockType.ASSISTANT,
        block.tokens < 20,
    ]

    stability_score = sum(stable_indicators) - sum(dynamic_indicators)
    block.is_stable = stability_score > 0

    if block.block_type == BlockType.SYSTEM:
        block.priority = 10
    elif block.is_stable:
        block.priority = 5
    else:
        block.priority = 1

    return block
