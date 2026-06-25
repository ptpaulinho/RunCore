"""RunCore Cloud — Stripe billing integration.

Wire up with real keys via environment variables:
  STRIPE_SECRET_KEY      — sk_live_... or sk_test_...
  STRIPE_WEBHOOK_SECRET  — whsec_...
  RUNCORE_BASE_URL       — https://your-runcore.onrender.com

Without keys the module degrades gracefully: checkout returns a placeholder URL
and webhook verification is skipped (dev mode).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

_STRIPE_SECRET_KEY    = os.environ.get("STRIPE_SECRET_KEY", "")
_STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
_BASE_URL             = os.environ.get("RUNCORE_BASE_URL", "http://localhost:8000")

# Stripe Price IDs — set these after creating products in Stripe Dashboard
_PRICE_IDS: dict[str, str] = {
    "team":       os.environ.get("STRIPE_PRICE_TEAM", "price_team_placeholder"),
    "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE", "price_enterprise_placeholder"),
}

_STRIPE_AVAILABLE = bool(_STRIPE_SECRET_KEY)


def _stripe():
    """Lazy import stripe so the module loads without the package."""
    try:
        import stripe as _s
        _s.api_key = _STRIPE_SECRET_KEY
        return _s
    except ImportError as exc:
        raise ImportError("Install stripe: pip install stripe>=7.0.0") from exc


def create_checkout_session(tenant_id: str, plan: str, email: str = "") -> dict:
    """Create a Stripe Checkout Session for upgrading to *plan*.

    Returns a dict with ``url`` (redirect the user there) and ``session_id``.
    In dev mode (no STRIPE_SECRET_KEY) returns a placeholder.
    """
    if not _STRIPE_AVAILABLE:
        return {
            "url": f"{_BASE_URL}/cloud/billing/dev-checkout?plan={plan}&tenant={tenant_id}",
            "session_id": "dev_session",
            "dev_mode": True,
        }

    price_id = _PRICE_IDS.get(plan)
    if not price_id or price_id.endswith("_placeholder"):
        return {
            "url": f"{_BASE_URL}/cloud/billing/dev-checkout?plan={plan}&tenant={tenant_id}",
            "session_id": "no_price_id",
            "dev_mode": True,
        }

    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{_BASE_URL}/cloud/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{_BASE_URL}/cloud/billing/cancel",
        client_reference_id=tenant_id,
        customer_email=email or None,
        metadata={"tenant_id": tenant_id, "plan": plan},
    )
    return {"url": session.url, "session_id": session.id, "dev_mode": False}


def create_portal_session(stripe_customer_id: str) -> str:
    """Create a Stripe Customer Portal session URL for managing subscriptions."""
    if not _STRIPE_AVAILABLE:
        return f"{_BASE_URL}/cloud/billing/dev-portal"

    stripe = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=f"{_BASE_URL}/cloud/dashboard",
    )
    return session.url


def verify_webhook(payload: bytes, sig_header: str) -> dict | None:
    """Verify and parse a Stripe webhook event.

    Returns the event dict or None if verification fails.
    In dev mode (no webhook secret) returns the raw parsed JSON.
    """
    if not _STRIPE_WEBHOOK_SECRET:
        try:
            return json.loads(payload)
        except Exception:
            return None

    if not _STRIPE_AVAILABLE:
        return None

    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, _STRIPE_WEBHOOK_SECRET)
        return dict(event)
    except Exception:
        return None


def handle_webhook_event(event: dict, storage_module: Any) -> str:
    """Process a verified Stripe webhook event. Returns a string describing the action taken."""
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        tenant_id = data.get("client_reference_id") or (data.get("metadata") or {}).get("tenant_id")
        plan = (data.get("metadata") or {}).get("plan", "team")
        stripe_customer_id = data.get("customer")
        subscription_id = data.get("subscription")
        if tenant_id:
            storage_module.upgrade_tenant_plan(
                tenant_id=tenant_id,
                plan=plan,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=subscription_id,
            )
            return f"upgraded tenant {tenant_id} to {plan}"

    elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer_id = data.get("customer")
        if customer_id:
            storage_module.downgrade_tenant_by_customer(customer_id, plan="free")
            return f"downgraded customer {customer_id} to free"

    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        status = data.get("status")
        if customer_id and status == "active":
            # Determine new plan from price metadata if available
            items = data.get("items", {}).get("data", [])
            price_id = items[0]["price"]["id"] if items else ""
            plan = next(
                (p for p, pid in _PRICE_IDS.items() if pid == price_id),
                None,
            )
            if plan and customer_id:
                storage_module.downgrade_tenant_by_customer(customer_id, plan=plan)
                return f"updated customer {customer_id} plan to {plan}"

    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer")
        if customer_id:
            storage_module.downgrade_tenant_by_customer(customer_id, plan="free")
            return f"payment failed for customer {customer_id} — downgraded to free"
        return "payment failed — no customer id in event"

    return f"unhandled event type: {event_type}"
