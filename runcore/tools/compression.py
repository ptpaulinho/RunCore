"""Schema compression utilities for RunCore - reduces token usage of tool schemas."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List

from runcore.core import ToolCategory
from runcore.tools.registry import ToolSchema, _estimate_tokens_for_schema

# Maximum description length after compression
_MAX_DESC_CHARS = 25
_MAX_PARAM_DESC_CHARS = 15

# Common verbose phrases to strip from descriptions
_VERBOSE_PATTERNS = [
    r"\bplease\b",
    r"\bkindly\b",
    r"\boptionally\b",
    r"\bNote that\b",
    r"\bNote:\s*",
    r"\bPlease note\b",
    r"\bThis (tool|function|parameter) (will|can|allows?|is used to|enables?)\b",
    r"\bYou (can|may|should|must)\b",
    r"\bIn order to\b",
    r"\bThe (tool|function|parameter)\b",
    r"\bIt is\b",
    r"\bThis is\b",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _VERBOSE_PATTERNS]


def _shorten_text(text: str, max_chars: int) -> str:
    """Strip verbose phrases and truncate text to max_chars."""
    for pat in _COMPILED_PATTERNS:
        text = pat.sub("", text)
    # Collapse multiple spaces
    text = re.sub(r"\s{2,}", " ", text).strip()
    if len(text) > max_chars:
        # Truncate at last word boundary before max_chars
        truncated = text[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > max_chars // 2:
            truncated = truncated[:last_space]
        text = truncated.rstrip(".,;:")
    return text


def _shorten_param_name(name: str) -> str:
    """Abbreviate long parameter names."""
    # Remove common verbose suffixes/prefixes
    name = re.sub(r"_?(string|value|input|output|param|parameter|argument|arg)$", "", name)
    name = re.sub(r"^(the_|a_|an_)", "", name)
    # If still long, keep first segment
    if len(name) > 20:
        parts = name.split("_")
        abbreviated = "_".join(p[:4] for p in parts)
        if len(abbreviated) < len(name):
            name = abbreviated
    return name or name  # never return empty


def _compress_properties(props: Dict[str, Any]) -> Dict[str, Any]:
    """Compress property definitions — keep only type and enum, drop all descriptions."""
    compressed: Dict[str, Any] = {}
    for pname, pdef in props.items():
        new_name = _shorten_param_name(pname)
        new_def: Dict[str, Any] = {}
        if "type" in pdef:
            new_def["type"] = pdef["type"][:3]  # e.g. "str" instead of "string"
        if "enum" in pdef:
            new_def["enum"] = pdef["enum"]
        if "items" in pdef:
            new_def["items"] = pdef["items"]
        # Descriptions, defaults, examples: all omitted for maximum compression
        compressed[new_name] = new_def
    return compressed


def _remap_required(required: List[str], props_original: Dict[str, Any]) -> List[str]:
    """Remap required field names to shortened names."""
    mapping = {name: _shorten_param_name(name) for name in props_original}
    return [mapping.get(r, r) for r in required]


def compress_schema(schema: ToolSchema) -> ToolSchema:
    """Return a compressed copy of the schema with reduced token usage.

    Compression strategy:
    - Truncate description to _MAX_DESC_CHARS
    - Shorten parameter names (remove verbose suffixes)
    - Replace 'description' key with 'd' in properties
    - Remove verbose schema-level keys (title, examples, $schema)
    - Strip parameter descriptions that are redundant with the param name
    """
    compressed_params = copy.deepcopy(schema.parameters)

    original_props: Dict[str, Any] = {}
    if isinstance(compressed_params, dict) and "properties" in compressed_params:
        original_props = compressed_params["properties"]
        compressed_params["properties"] = _compress_properties(original_props)
        # Remove verbose schema-level keys including redundant "type": "object"
        for key in ("title", "examples", "default", "$schema", "additionalProperties", "type"):
            compressed_params.pop(key, None)

    new_required = _remap_required(schema.required, original_props)
    new_description = _shorten_text(schema.description, _MAX_DESC_CHARS)

    new_schema = ToolSchema(
        name=schema.name,
        description=new_description,
        parameters=compressed_params,
        category=schema.category,
        required=new_required,
        token_count=0,  # will be recalculated in __post_init__
    )
    return new_schema


def compress_schemas(schemas: List[ToolSchema]) -> List[ToolSchema]:
    """Compress a list of tool schemas."""
    return [compress_schema(s) for s in schemas]


def measure_compression_ratio(
    original: List[ToolSchema], compressed: List[ToolSchema]
) -> float:
    """Return the fraction of tokens saved (0.0 to 1.0).

    A value of 0.4 means 40% token reduction.
    """
    original_tokens = sum(s.token_count for s in original)
    compressed_tokens = sum(s.token_count for s in compressed)
    if original_tokens == 0:
        return 0.0
    saved = original_tokens - compressed_tokens
    return round(saved / original_tokens, 4)
