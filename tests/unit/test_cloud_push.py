"""Tests for RunCore SDK auto-push (F14)."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

import runcore
from runcore.sdk import cloud as _cloud


# ---------------------------------------------------------------------------
# Fixtures — reset config between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cloud():
    """Ensure cloud config is reset before and after every test."""
    _cloud.reset()
    yield
    _cloud.reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_capture():
    with runcore.capture("test_agent", task="unit test") as cap:
        cap.record_tool("search", {"q": "foo"}, {"result": "bar"}, True, 10.0)
    return cap


# ===========================================================================
# configure() API
# ===========================================================================

class TestConfigure:
    def test_configure_sets_auto_push(self):
        runcore.configure(api_key="rc_testkey123")
        assert runcore.is_configured() is True

    def test_configure_requires_rc_prefix(self):
        with pytest.raises(ValueError, match="rc_"):
            runcore.configure(api_key="bad_key")

    def test_configure_rejects_empty_key(self):
        with pytest.raises(ValueError, match="empty"):
            runcore.configure(api_key="")

    def test_configure_rejects_invalid_on_error(self):
        with pytest.raises(ValueError, match="on_error"):
            runcore.configure(api_key="rc_x", on_error="explode")

    def test_configure_stores_endpoint(self):
        runcore.configure(api_key="rc_key", endpoint="https://example.com")
        cfg = runcore.get_config()
        assert cfg["endpoint"] == "https://example.com"

    def test_configure_strips_trailing_slash(self):
        runcore.configure(api_key="rc_key", endpoint="https://example.com/")
        assert runcore.get_config()["endpoint"] == "https://example.com"

    def test_not_configured_by_default(self):
        assert runcore.is_configured() is False

    def test_reset_clears_config(self):
        runcore.configure(api_key="rc_key")
        runcore.reset_cloud()
        assert runcore.is_configured() is False

    def test_configure_custom_timeout(self):
        runcore.configure(api_key="rc_key", timeout_s=10.0)
        assert runcore.get_config()["timeout_s"] == 10.0

    def test_configure_on_error_silent(self):
        runcore.configure(api_key="rc_key", on_error="silent")
        assert runcore.get_config()["on_error"] == "silent"

    def test_configure_on_error_raise(self):
        runcore.configure(api_key="rc_key", on_error="raise")
        assert runcore.get_config()["on_error"] == "raise"


# ===========================================================================
# push_trace() — mock HTTP
# ===========================================================================

class TestPushTrace:
    def test_push_not_called_when_not_configured(self):
        with patch("runcore.sdk.cloud._push_sync") as mock_push:
            runcore.push_trace(MagicMock())
            mock_push.assert_not_called()

    def test_push_called_when_configured(self):
        runcore.configure(api_key="rc_testkey")
        with patch("runcore.sdk.cloud._push_sync") as mock_push:
            trace = MagicMock()
            runcore.push_trace(trace, block=True)
            mock_push.assert_called_once()

    def test_push_increments_stats(self):
        runcore.configure(api_key="rc_testkey")
        with patch("runcore.sdk.cloud._push_sync"):
            runcore.push_trace(MagicMock(), block=True)
        assert _cloud.push_stats()["pushed"] == 1
        assert _cloud.push_stats()["errors"] == 0

    def test_push_error_increments_error_stat_warn_mode(self):
        runcore.configure(api_key="rc_testkey", on_error="warn")
        with patch("runcore.sdk.cloud._push_sync", side_effect=RuntimeError("network down")):
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                runcore.push_trace(MagicMock(), block=True)
            assert _cloud.push_stats()["errors"] == 1
            assert any("network down" in str(warning.message) for warning in w)

    def test_push_error_raises_in_raise_mode(self):
        runcore.configure(api_key="rc_testkey", on_error="raise")
        with patch("runcore.sdk.cloud._push_sync", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                runcore.push_trace(MagicMock(), block=True)

    def test_push_error_silent_mode(self):
        runcore.configure(api_key="rc_testkey", on_error="silent")
        with patch("runcore.sdk.cloud._push_sync", side_effect=RuntimeError("ignored")):
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                runcore.push_trace(MagicMock(), block=True)
            assert _cloud.push_stats()["errors"] == 1
            assert not any("ignored" in str(warning.message) for warning in w)

    def test_push_stats_reset_with_reset_cloud(self):
        runcore.configure(api_key="rc_testkey")
        with patch("runcore.sdk.cloud._push_sync"):
            runcore.push_trace(MagicMock(), block=True)
        assert _cloud.push_stats()["pushed"] == 1
        runcore.reset_cloud()
        assert _cloud.push_stats()["pushed"] == 0


# ===========================================================================
# Capture auto-push integration
# ===========================================================================

class TestCaptureAutoPush:
    def test_capture_does_not_push_when_not_configured(self):
        with patch("runcore.sdk.cloud.push_trace") as mock_push:
            with runcore.capture("agent") as cap:
                pass
            mock_push.assert_not_called()

    def test_capture_pushes_on_exit_when_configured(self):
        runcore.configure(api_key="rc_testkey")
        with patch("runcore.sdk.cloud._push_sync") as mock_push:
            with runcore.capture("agent", task="test") as cap:
                cap.record_tool("ping", {}, "pong", True, 5.0)
        # push runs on daemon thread — wait briefly
        time.sleep(0.1)
        mock_push.assert_called_once()

    def test_capture_pushes_even_on_exception(self):
        runcore.configure(api_key="rc_testkey")
        pushed_traces = []

        def _fake_push(trace, cfg):
            pushed_traces.append(trace)

        with patch("runcore.sdk.cloud._push_sync", side_effect=_fake_push):
            try:
                with runcore.capture("agent", task="fail test") as cap:
                    raise ValueError("intentional error")
            except ValueError:
                pass
        time.sleep(0.1)
        assert len(pushed_traces) == 1
        # trace should be marked as failed
        assert pushed_traces[0].success is False

    def test_capture_push_receives_correct_agent_name(self):
        runcore.configure(api_key="rc_testkey")
        received = []

        def _fake_push(trace, cfg):
            received.append(trace.agent_name)

        with patch("runcore.sdk.cloud._push_sync", side_effect=_fake_push):
            with runcore.capture("my_pipeline", task="classify"):
                pass
        time.sleep(0.1)
        assert received == ["my_pipeline"]

    def test_multiple_captures_push_each(self):
        runcore.configure(api_key="rc_testkey")
        count = []

        def _fake_push(trace, cfg):
            count.append(1)

        with patch("runcore.sdk.cloud._push_sync", side_effect=_fake_push):
            for i in range(3):
                with runcore.capture(f"agent_{i}"):
                    pass
        time.sleep(0.2)
        assert len(count) == 3


# ===========================================================================
# _push_sync HTTP format test (no real network)
# ===========================================================================

class TestPushSyncPayload:
    def test_push_sync_sends_correct_payload(self):
        """Verify _push_sync sends the right JSON structure via a local HTTP server."""
        received_requests: list[dict] = []
        server_ready = threading.Event()

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received_requests.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization", ""),
                    "body": json.loads(body),
                })
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ingested":1,"trace_ids":["t1"],"errors":[]}')

            def log_message(self, *args):
                pass  # suppress test output

        httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        port = httpd.server_address[1]

        def _serve():
            server_ready.set()
            httpd.handle_request()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        server_ready.wait()

        cfg = {
            "api_key": "rc_testkey456",
            "endpoint": f"http://127.0.0.1:{port}",
            "timeout_s": 3.0,
        }

        from runcore.atir.spec import ATIRTrace
        from datetime import datetime, timezone
        trace = ATIRTrace(
            trace_id="t-payload-test",
            agent_name="payload_agent",
            task="verify payload",
            started_at=datetime.now(timezone.utc),
            success=True,
            framework="test",
            spans=[],
        ).finalize()

        _cloud._push_sync(trace, cfg)
        t.join(timeout=2)

        assert len(received_requests) == 1
        req = received_requests[0]
        assert req["path"] == "/cloud/ingest"
        assert req["auth"] == "Bearer rc_testkey456"
        body = req["body"]
        assert "traces" in body
        assert len(body["traces"]) == 1
        assert body["traces"][0]["agent_name"] == "payload_agent"
        assert body["traces"][0]["trace_id"] == "t-payload-test"

    def test_push_sync_raises_on_non_200(self):
        """_push_sync raises RuntimeError on non-200 HTTP response."""
        server_ready = threading.Event()

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail":"limit exceeded"}')

            def log_message(self, *args):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        port = httpd.server_address[1]

        def _serve():
            server_ready.set()
            httpd.handle_request()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        server_ready.wait()

        cfg = {
            "api_key": "rc_key",
            "endpoint": f"http://127.0.0.1:{port}",
            "timeout_s": 3.0,
        }

        from runcore.atir.spec import ATIRTrace
        from datetime import datetime, timezone
        trace = ATIRTrace(
            trace_id="t-err",
            agent_name="err_agent",
            task="",
            started_at=datetime.now(timezone.utc),
            success=True,
            framework="test",
            spans=[],
        ).finalize()

        with pytest.raises(RuntimeError, match="429"):
            _cloud._push_sync(trace, cfg)

        t.join(timeout=2)
