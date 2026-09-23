"""Signed certificates — anyone can verify a RunCore certificate, offline.

A certificate is an *attestation*: a JSON payload (who, what, grade, when, which
hidden suite version) plus an Ed25519 signature by the RunCore issuer key.
Editing a single character of the payload breaks the signature.

    runcore verify certificate.json            # fetches the issuer key from runcore.onrender.com
    runcore verify certificate.json --key <base64 public key>

The issuer publishes its public key at ``/.well-known/runcore-signing-key``.
Signing happens only on the RunCore server (see ``issuer_key()``).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ALG = "Ed25519"
VALIDITY_DAYS = 90   # certificates expire — the agent must keep passing, not pass once


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def key_id(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Issuer (server side)
# ---------------------------------------------------------------------------

_issuer = None


def issuer_key():
    """The server's Ed25519 private key.

    RUNCORE_SIGNING_KEY (base64url 32-byte seed) in production — it must survive
    redeploys or every issued certificate stops verifying. Otherwise a key is
    generated once and kept next to the database.
    """
    global _issuer
    if _issuer is not None:
        return _issuer
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    seed = os.environ.get("RUNCORE_SIGNING_KEY", "").strip()
    if seed:
        _issuer = Ed25519PrivateKey.from_private_bytes(_b64d(seed))
        return _issuer
    db = os.environ.get("RUNCORE_DB_PATH", "")
    path = (Path(db).parent if db else Path.home() / ".runcore") / "signing_key"
    if path.exists():
        _issuer = Ed25519PrivateKey.from_private_bytes(_b64d(path.read_text().strip()))
    else:
        from cryptography.hazmat.primitives import serialization
        _issuer = Ed25519PrivateKey.generate()
        raw = _issuer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_b64e(raw))
        path.chmod(0o600)
    return _issuer


def public_key_b64(private=None) -> str:
    from cryptography.hazmat.primitives import serialization
    pub = (private or issuer_key()).public_key().public_bytes(serialization.Encoding.Raw,
                                                               serialization.PublicFormat.Raw)
    return _b64e(pub)


def sign(payload: dict, private=None) -> dict:
    """Return an attestation {payload, alg, kid, signature}. Adds issued/expiry if absent."""
    key = private or issuer_key()
    pub_b64 = public_key_b64(key)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {"issuer": "RunCore", "v": 1,
               "issued_at": now.isoformat(),
               "expires_at": (now + timedelta(days=VALIDITY_DAYS)).isoformat(),
               **payload}
    payload["kid"] = key_id(_b64d(pub_b64))
    return {"payload": payload, "alg": ALG, "kid": payload["kid"],
            "signature": _b64e(key.sign(canonical(payload)))}


# ---------------------------------------------------------------------------
# Verification (anyone)
# ---------------------------------------------------------------------------

def verify(attestation: dict, public_key: str, *, now: datetime | None = None) -> dict:
    """Check signature and expiry. Returns {valid, status, reason, payload}.

    status: valid | expired | bad_signature | wrong_key | malformed
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        payload, sig = attestation["payload"], _b64d(attestation["signature"])
        pub_raw = _b64d(public_key)
        pub = Ed25519PublicKey.from_public_bytes(pub_raw)
    except (KeyError, TypeError, ValueError) as exc:
        return {"valid": False, "status": "malformed", "reason": str(exc), "payload": None}
    if payload.get("kid") != key_id(pub_raw):
        return {"valid": False, "status": "wrong_key",
                "reason": "Signed by a different key than the one given.", "payload": payload}
    try:
        pub.verify(sig, canonical(payload))
    except InvalidSignature:
        return {"valid": False, "status": "bad_signature",
                "reason": "The certificate was modified after it was issued.", "payload": payload}
    now = now or datetime.now(timezone.utc)
    if datetime.fromisoformat(payload["expires_at"]) < now:
        return {"valid": False, "status": "expired",
                "reason": f"Expired on {payload['expires_at'][:10]} — the agent must be re-certified.",
                "payload": payload}
    return {"valid": True, "status": "valid", "reason": "Signature valid, not expired.", "payload": payload}


def fetch_public_key(base_url: str = "https://runcore.onrender.com", timeout: float = 10.0) -> str:
    import urllib.request
    with urllib.request.urlopen(base_url.rstrip("/") + "/.well-known/runcore-signing-key", timeout=timeout) as r:
        return json.loads(r.read())["public_key"]
