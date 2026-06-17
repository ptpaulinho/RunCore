"""
Deterministic replacement patterns for common LLM tool-call scenarios.

Each entry in DETERMINISTIC_PATTERNS describes a class of tool calls that
can be replaced by a plain Python implementation instead of an LLM roundtrip.
"""

from __future__ import annotations

from typing import Any

DETERMINISTIC_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "invoice_lookup",
        "description": (
            "Retrieve an invoice record by its ID from a data store. "
            "This is a pure key-based lookup with no reasoning required."
        ),
        "tool_names": [
            "get_invoice",
            "fetch_invoice",
            "lookup_invoice",
            "retrieve_invoice",
            "invoice_get",
        ],
        "pattern_type": "lookup",
        "code_template": '''\
import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Inline invoice lookup — replaces LLM tool call
# ---------------------------------------------------------------------------
# Assumes invoices are stored in a JSON file or dict; adapt as needed.

def get_invoice(invoice_id: str, store: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return the invoice record for *invoice_id*, or None if not found."""
    if store is not None:
        return store.get(invoice_id)

    # Fallback: load from a JSON file next to this module
    data_path = Path(__file__).with_name("invoices.json")
    if data_path.exists():
        with data_path.open() as fh:
            store = json.load(fh)
        return store.get(invoice_id)

    raise FileNotFoundError(
        "No invoice store provided and invoices.json not found. "
        "Pass a dict as the `store` argument."
    )
''',
    },
    {
        "name": "date_formatting",
        "description": (
            "Convert a date string from one format to another. "
            "Fully deterministic — uses Python's datetime.strftime/strptime."
        ),
        "tool_names": [
            "format_date",
            "convert_date",
            "reformat_date",
            "date_convert",
            "date_format",
            "parse_date",
        ],
        "pattern_type": "format",
        "code_template": '''\
from datetime import datetime

# ---------------------------------------------------------------------------
# Date format conversion — replaces LLM tool call
# ---------------------------------------------------------------------------

# Common format shortcuts
FORMAT_ALIASES: dict[str, str] = {
    "iso": "%Y-%m-%d",
    "us":  "%m/%d/%Y",
    "eu":  "%d/%m/%Y",
    "long": "%B %d, %Y",
    "timestamp": "%Y-%m-%dT%H:%M:%S",
}


def format_date(
    date_str: str,
    input_fmt: str = "%Y-%m-%d",
    output_fmt: str = "%d/%m/%Y",
) -> str:
    """
    Parse *date_str* with *input_fmt* and return it formatted as *output_fmt*.

    Both format strings may be strftime patterns or keys in FORMAT_ALIASES.
    """
    in_fmt  = FORMAT_ALIASES.get(input_fmt,  input_fmt)
    out_fmt = FORMAT_ALIASES.get(output_fmt, output_fmt)
    dt = datetime.strptime(date_str.strip(), in_fmt)
    return dt.strftime(out_fmt)
''',
    },
    {
        "name": "email_validation",
        "description": (
            "Validate whether a string is a syntactically valid e-mail address. "
            "Uses a regex — no LLM required."
        ),
        "tool_names": [
            "validate_email",
            "check_email",
            "is_valid_email",
            "email_validate",
            "verify_email_format",
        ],
        "pattern_type": "validate",
        "code_template": '''\
import re

# ---------------------------------------------------------------------------
# E-mail address validation — replaces LLM tool call
# ---------------------------------------------------------------------------

# RFC-5321-ish pattern; covers the vast majority of real addresses.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}$"
)


def validate_email(email: str) -> dict[str, object]:
    """
    Return a dict with keys:
      - valid (bool)   : True if the address passes the regex check.
      - email (str)    : The normalised (stripped, lowercased) address.
      - reason (str)   : Human-readable explanation when valid is False.
    """
    normalised = email.strip().lower()
    if _EMAIL_RE.match(normalised):
        return {"valid": True, "email": normalised, "reason": ""}
    return {
        "valid": False,
        "email": normalised,
        "reason": "Address does not match expected e-mail format.",
    }
''',
    },
    {
        "name": "currency_conversion",
        "description": (
            "Convert a monetary amount between currencies using a provided or "
            "cached exchange rate. Formula-based — deterministic given the rate."
        ),
        "tool_names": [
            "convert_currency",
            "currency_convert",
            "exchange_currency",
            "fx_convert",
            "convert_amount",
        ],
        "pattern_type": "transform",
        "code_template": '''\
from __future__ import annotations

# ---------------------------------------------------------------------------
# Currency conversion — replaces LLM tool call
# ---------------------------------------------------------------------------
# Provide your own rate source; the mapping below is a static fallback.

_FALLBACK_RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "JPY": 0.0067,
    "CAD": 0.74,
    "AUD": 0.65,
    "CHF": 1.12,
    "CNY": 0.14,
    "BRL": 0.20,
    "INR": 0.012,
}


def convert_currency(
    amount: float,
    from_currency: str,
    to_currency: str,
    rates: dict[str, float] | None = None,
) -> dict[str, object]:
    """
    Convert *amount* from *from_currency* to *to_currency*.

    *rates* must map currency codes to their value in USD.
    Falls back to _FALLBACK_RATES_TO_USD when not supplied.

    Returns a dict with keys:
      - original_amount (float)
      - from_currency   (str)
      - converted_amount(float)
      - to_currency     (str)
      - rate_used       (float)
    """
    r = rates if rates is not None else _FALLBACK_RATES_TO_USD

    src = from_currency.upper()
    dst = to_currency.upper()

    if src not in r:
        raise ValueError(f"Unknown source currency: {src!r}")
    if dst not in r:
        raise ValueError(f"Unknown target currency: {dst!r}")

    # Convert src -> USD -> dst
    amount_usd = amount * r[src]
    converted  = amount_usd / r[dst]
    rate_used  = r[src] / r[dst]

    return {
        "original_amount":  round(amount, 6),
        "from_currency":    src,
        "converted_amount": round(converted, 6),
        "to_currency":      dst,
        "rate_used":        round(rate_used, 8),
    }
''',
    },
    {
        "name": "string_formatting",
        "description": (
            "Apply a fixed template to produce a formatted string (e.g. "
            "greeting, report header, message body). Pure string interpolation."
        ),
        "tool_names": [
            "format_string",
            "render_template",
            "fill_template",
            "format_message",
            "apply_template",
        ],
        "pattern_type": "format",
        "code_template": '''\
from string import Template

# ---------------------------------------------------------------------------
# String / template formatting — replaces LLM tool call
# ---------------------------------------------------------------------------

def format_string(template: str, **kwargs: object) -> str:
    """
    Substitute *kwargs* into *template*.

    Supports both str.format-style placeholders ({name}) and
    string.Template-style ($name / ${name}).

    Raises KeyError / ValueError for missing or malformed placeholders.
    """
    if "$" in template or "${" in template:
        return Template(template).substitute(kwargs)
    return template.format(**kwargs)
''',
    },
    {
        "name": "numeric_range_check",
        "description": (
            "Validate that a numeric value falls within an expected range. "
            "Comparison-based — requires no LLM judgment."
        ),
        "tool_names": [
            "check_range",
            "validate_range",
            "is_in_range",
            "range_check",
            "numeric_validate",
        ],
        "pattern_type": "validate",
        "code_template": '''\
from __future__ import annotations

# ---------------------------------------------------------------------------
# Numeric range check — replaces LLM tool call
# ---------------------------------------------------------------------------

def check_range(
    value: float,
    min_value: float | None = None,
    max_value: float | None = None,
) -> dict[str, object]:
    """
    Check whether *value* lies within [*min_value*, *max_value*].

    Either bound may be None (unbounded on that side).

    Returns:
      - valid  (bool)
      - value  (float)
      - reason (str)   empty string when valid
    """
    if min_value is not None and value < min_value:
        return {
            "valid": False,
            "value": value,
            "reason": f"Value {value} is below minimum {min_value}.",
        }
    if max_value is not None and value > max_value:
        return {
            "valid": False,
            "value": value,
            "reason": f"Value {value} exceeds maximum {max_value}.",
        }
    return {"valid": True, "value": value, "reason": ""}
''',
    },
    {
        "name": "record_lookup_by_key",
        "description": (
            "Fetch a record from an in-memory dict or database by a single key. "
            "Generalised version of invoice_lookup for arbitrary entity types."
        ),
        "tool_names": [
            "get_record",
            "fetch_record",
            "lookup_record",
            "find_by_id",
            "get_by_id",
            "retrieve_record",
        ],
        "pattern_type": "lookup",
        "code_template": '''\
from __future__ import annotations
from typing import Any

# ---------------------------------------------------------------------------
# Generic record lookup by key — replaces LLM tool call
# ---------------------------------------------------------------------------

def get_record(
    key: str,
    store: dict[str, Any],
    key_field: str = "id",
) -> dict[str, Any] | None:
    """
    Look up a record from *store* by *key*.

    *store* can be:
      - a plain dict mapping key -> record
      - a list of dicts, each having a field named *key_field*

    Returns the record dict or None if not found.
    """
    if isinstance(store, dict):
        return store.get(key)

    if isinstance(store, list):
        for item in store:
            if isinstance(item, dict) and str(item.get(key_field)) == str(key):
                return item

    raise TypeError(f"store must be a dict or list, got {type(store).__name__!r}")
''',
    },
]
