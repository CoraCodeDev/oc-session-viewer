"""Basic route + response shape tests for the OC Session Viewer."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

# Ensure app module is importable
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402


def _make_jsonl(tmpdir, agent, sid, lines):
    """Write a test JSONL session file and return its path."""
    agent_dir = os.path.join(tmpdir, agent)
    os.makedirs(agent_dir, exist_ok=True)
    path = os.path.join(agent_dir, f"{sid}.jsonl")
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path


def _sample_msg(role, content, timestamp="2025-01-01T00:00:00", tool_calls=None):
    return {
        "timestamp": timestamp,
        "message": {
            "role": role,
            "content": content,
            "model": "test-model",
            **({"tool_calls": tool_calls} if tool_calls else {}),
        },
    }


class TestRoutes(unittest.TestCase):
    """Test HTTP routes exist and return 200."""

    def setUp(self):
        self.app = app.test_client()

    def test_health(self):
        r = self.app.get("/health")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("sessions", data)

    def test_index(self):
        r = self.app.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Sessions", r.data)

    def test_api_sessions(self):
        r = self.app.get("/api/sessions")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), list)

    def test_api_agents(self):
        r = self.app.get("/api/agents")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), list)

    def test_metrics(self):
        r = self.app.get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"text/plain", r.headers.get("Content-Type", "").encode())

    def test_session_not_found(self):
        r = self.app.get("/s/nonexistent")
        self.assertEqual(r.status_code, 404)

    def test_api_session_not_found(self):
        r = self.app.get("/api/s/nonexistent")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"], "not found")


class TestResponseShapes(unittest.TestCase):
    """Test that JSON responses have the expected keys/types."""

    def setUp(self):
        self.app = app.test_client()
        self.tmpdir = tempfile.mkdtemp()
        self._sample_session()
        self._patcher = patch("app.AGENTS_DIR", self.tmpdir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _sample_session(self):
        agent_dir = os.path.join(self.tmpdir, "test-agent")
        os.makedirs(agent_dir, exist_ok=True)
        path = os.path.join(agent_dir, "abc123.jsonl")
        lines = [
            _sample_msg("user", "Hello there"),
            _sample_msg("assistant", "Hi! How can I help?", tool_calls=[
                {"function": {"name": "exec", "arguments": '{"cmd": "echo hi"}'}}
            ]),
        ]
        with open(path, "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def test_api_sessions_shape(self):
        r = self.app.get("/api/sessions")
        sessions = r.get_json()
        self.assertGreater(len(sessions), 0)
        s = sessions[0]
        for key in ("id", "agent", "agent_name", "path", "size", "size_h", "mtime", "mtime_h"):
            self.assertIn(key, s, f"Missing key: {key}")

    def test_api_session_shape(self):
        r = self.app.get("/api/s/abc123")
        data = r.get_json()
        for key in ("messages", "page", "total_pages", "total",
                    "has_prev", "has_next", "model", "tool_calls", "user_msgs"):
            self.assertIn(key, data, f"Missing key: {key}")
        self.assertEqual(data["user_msgs"], 1)
        self.assertEqual(data["tool_calls"], 1)

    def test_api_agents_shape(self):
        r = self.app.get("/api/agents")
        agents = r.get_json()
        self.assertGreater(len(agents), 0)
        a = agents[0]
        for key in ("agent", "sessions", "total_size", "total_size_h",
                    "total_lines", "total_messages", "total_tool_calls",
                    "latest_mtime", "latest"):
            self.assertIn(key, a, f"Missing key: {key}")

    def test_metrics_shape(self):
        r = self.app.get("/metrics")
        text = r.get_data(as_text=True)
        # Should contain standard Prometheus HELP/TYPE lines
        self.assertIn("oc_sessions_total", text)
        self.assertIn("# HELP", text)
        self.assertIn("# TYPE", text)


class TestCaching(unittest.TestCase):
    """Verify that /api/agents and /metrics use caching."""

    def setUp(self):
        self.app = app.test_client()
        # Clear any previous cache
        if hasattr(app.view_functions.get("api_agents"), "_cache"):
            delattr(app.view_functions["api_agents"], "_cache")
        if hasattr(app.view_functions.get("metrics_endpoint"), "_cache"):
            delattr(app.view_functions["metrics_endpoint"], "_cache")

    def test_api_agents_returns_200(self):
        """Basic sanity that the endpoint works (caching shouldn't break it)."""
        r = self.app.get("/api/agents")
        self.assertEqual(r.status_code, 200)
        # Second call should also work (cache hit)
        r2 = self.app.get("/api/agents")
        self.assertEqual(r2.status_code, 200)

    def test_metrics_returns_200(self):
        """Basic sanity that /metrics works with caching."""
        r = self.app.get("/metrics")
        self.assertEqual(r.status_code, 200)
        r2 = self.app.get("/metrics")
        self.assertEqual(r2.status_code, 200)


if __name__ == "__main__":
    unittest.main()
