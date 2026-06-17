"""RunCore Cloud — Billing tiers, limits, and usage enforcement."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Plan = Literal["free", "team", "enterprise"]


@dataclass(frozen=True)
class TierLimits:
    plan: Plan
    traces_per_month: int        # -1 = unlimited
    retention_days: int
    seats: int                   # -1 = unlimited
    price_usd_month: float
    features: tuple[str, ...]


TIERS: dict[str, TierLimits] = {
    "free": TierLimits(
        plan="free",
        traces_per_month=500,
        retention_days=7,
        seats=1,
        price_usd_month=0.0,
        features=("basic_dashboard", "json_export"),
    ),
    "team": TierLimits(
        plan="team",
        traces_per_month=10_000,
        retention_days=30,
        seats=10,
        price_usd_month=49.0,
        features=("basic_dashboard", "json_export", "advisor", "alerts", "csv_export"),
    ),
    "enterprise": TierLimits(
        plan="enterprise",
        traces_per_month=-1,
        retention_days=365,
        seats=-1,
        price_usd_month=299.0,
        features=(
            "basic_dashboard", "json_export", "advisor", "alerts",
            "csv_export", "sso", "audit_log", "custom_retention", "priority_support",
        ),
    ),
}


def get_limits(plan: str) -> TierLimits:
    """Return TierLimits for a plan name; falls back to 'free' for unknown plans."""
    return TIERS.get(plan, TIERS["free"])


def check_ingest_allowed(plan: str, traces_this_month: int, batch_size: int = 1) -> tuple[bool, str]:
    """Return (allowed, reason). 'allowed' is False if adding batch_size traces would exceed the limit."""
    limits = get_limits(plan)
    if limits.traces_per_month == -1:
        return True, ""
    remaining = limits.traces_per_month - traces_this_month
    if remaining <= 0:
        return False, (
            f"Monthly trace limit reached ({limits.traces_per_month} traces). "
            f"Upgrade to Team or Enterprise for more."
        )
    if batch_size > remaining:
        return False, (
            f"Batch of {batch_size} would exceed monthly limit "
            f"({traces_this_month}/{limits.traces_per_month}). "
            f"Only {remaining} traces remaining this month."
        )
    return True, ""


def has_feature(plan: str, feature: str) -> bool:
    return feature in get_limits(plan).features


TIER_COMPARISON = [
    {
        "plan": "free",
        "price": "$0/mo",
        "traces": "500/mo",
        "retention": "7 days",
        "seats": "1",
        "features": ["Basic dashboard", "JSON export"],
    },
    {
        "plan": "team",
        "price": "$49/mo",
        "traces": "10,000/mo",
        "retention": "30 days",
        "seats": "10",
        "features": ["Everything in Free", "OptimizationAdvisor", "Alerts", "CSV export"],
    },
    {
        "plan": "enterprise",
        "price": "$299/mo",
        "traces": "Unlimited",
        "retention": "365 days",
        "seats": "Unlimited",
        "features": ["Everything in Team", "SSO", "Audit log", "Priority support"],
    },
]
