"""Signed certificates and hidden rotating suites."""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("RUNCORE_DB_PATH", tempfile.mktemp(suffix=".db"))

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from runcore import attest

KEY = Ed25519PrivateKey.generate()
PUB = attest.public_key_b64(KEY)


def _att(**over):
    return attest.sign({"cert_id": "c1", "subject": {"company": "Acme"}, "grade": "A", "score": 84.0, **over}, KEY)


def test_sign_and_verify():
    res = attest.verify(_att(), PUB)
    assert res["valid"] and res["status"] == "valid" and res["payload"]["grade"] == "A"


def test_tampering_is_detected():
    att = _att()
    att["payload"]["grade"] = "A+"
    assert attest.verify(att, PUB)["status"] == "bad_signature"


def test_other_key_and_garbage():
    other = attest.public_key_b64(Ed25519PrivateKey.generate())
    assert attest.verify(_att(), other)["status"] == "wrong_key"
    assert attest.verify({"payload": {}}, PUB)["status"] == "malformed"


def test_expiry():
    later = datetime.now(timezone.utc) + timedelta(days=attest.VALIDITY_DAYS + 1)
    assert attest.verify(_att(), PUB, now=later)["status"] == "expired"


def test_issuer_key_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(attest, "_issuer", None)
    monkeypatch.delenv("RUNCORE_SIGNING_KEY", raising=False)
    monkeypatch.setenv("RUNCORE_DB_PATH", str(tmp_path / "x.db"))
    first = attest.public_key_b64()
    monkeypatch.setattr(attest, "_issuer", None)
    assert attest.public_key_b64() == first                  # same key after restart
    assert oct((tmp_path / "signing_key").stat().st_mode)[-3:] == "600"
    monkeypatch.setattr(attest, "_issuer", None)
    monkeypatch.setenv("RUNCORE_SIGNING_KEY", attest._b64e(b"\x01" * 32))
    assert attest.public_key_b64() != first                  # env key wins


# ---------------------------------------------------------------------------
# Hidden rotating suites
# ---------------------------------------------------------------------------

def test_rotation_changes_ids_consistently():
    from benchmarks.tasks import SUPPORT_TASKS, rotate_tasks, suite_version
    a = rotate_tasks(SUPPORT_TASKS, "s3cret", "2026-09")
    t = a[0]
    assert "ORD-5523" not in t.user_message and "joao@example.com" not in t.user_message
    new_order = t.user_message.split("order ")[1].split(")")[0]
    assert new_order in json.dumps(t.tool_responses)         # prompt and tool data still agree
    assert [x.user_message for x in rotate_tasks(SUPPORT_TASKS, "s3cret", "2026-09")] == [x.user_message for x in a]
    assert rotate_tasks(SUPPORT_TASKS, "s3cret", "2026-10")[0].user_message != t.user_message
    assert rotate_tasks(SUPPORT_TASKS, "other", "2026-09")[0].user_message != t.user_message
    assert SUPPORT_TASKS[0].user_message.count("ORD-5523") == 1    # originals untouched
    assert suite_version("s3cret", "2026-09") != suite_version("s3cret", "2026-10")


def test_certification_tasks_private_suite(tmp_path, monkeypatch):
    from benchmarks.tasks import SUPPORT_TASKS, certification_tasks
    tasks, ver = certification_tasks("support", secret="")
    assert ver == "public" and len(tasks) == len(SUPPORT_TASKS)
    priv = tmp_path / "private.json"
    priv.write_text(json.dumps([{"id": "support_private_1", "name": "p", "system_prompt": "s", "user_message": "u",
                                 "tools": [], "tool_responses": {}, "expected_tools_called": [],
                                 "success_keywords": ["x"]}]))
    monkeypatch.setenv("RUNCORE_PRIVATE_SUITE", str(priv))
    tasks, ver = certification_tasks("support", secret="s3cret", period="2026-09")
    assert ver != "public" and len(tasks) == len(SUPPORT_TASKS) + 1 and tasks[-1].id == "support_private_1"


def test_cli_verify(tmp_path):
    from typer.testing import CliRunner
    from runcore.cli.main import app
    f = tmp_path / "cert.json"
    f.write_text(json.dumps(_att()))
    ok = CliRunner().invoke(app, ["verify", str(f), "--key", PUB])
    assert ok.exit_code == 0 and "VALID" in ok.output
    bad = _att(); bad["payload"]["score"] = 99.0
    f.write_text(json.dumps(bad))
    assert CliRunner().invoke(app, ["verify", str(f), "--key", PUB]).exit_code == 1
