"""Tests for OC Session Viewer — routes, response shapes, and caching."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app import app


def _make_session(agent="test-agent", session_id="abc123", content_lines=None, size=100, mtime=1700000000):
    """Return a find_sessions()-style session dict."""
    return {
        "id": session_id,
        "agent": agent,
        "agent_name": agent,
        "path": content_lines,  # reused below
        "size": size,
        "size_h": f"{size}B",
        "mtime": mtime,
        "mtime_h": "just now",
    }


def _fake_find_sessions(sessions=None):
    """Return a mock find_sessions that writes temp JSONL files."""
    if sessions is None:
        return []

    tmpdir = tempfile.mkdtemp()
    result = []
    for s in sessions:
        agent = s["agent"]
        sid = s["id"]
        content = s.get("content", "")
        path = os.path.join(tmpdir, f"{sid}.jsonl")
        with open(path, "w") as f:
            f.write(content)
        result.append({
            "id": sid,
            "agent": agent,
            "agent_name": agent,
            "path": path,
            "size": os.path.getsize(path),
            "size_h": f"{os.path.getsize(path)}B",
            "mtime": s.get("mtime", 1700000000),
            "mtime_h": "just now",
        })
    return result


class TestRoutes(unittest.TestCase):
    """Basic route existence and response shape tests."""

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        # Clear any function-level caches
        for fn in [app.view_functions.get("api_agents"), app.view_functions.get("metrics_endpoint")]:
            if fn and hasattr(fn, "_cache"):
                del fn._cache

    @patch("app.find_sessions", return_value=[])
    def test_health_returns_ok(self, _mock):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    @patch("app.find_sessions", return_value=[])
    def test_api_sessions_returns_list(self, _mock):
        resp = self.client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    @patch("app.find_sessions", return_value=[])
    def test_api_session_not_found(self, _mock):
        resp = self.client.get("/api/s/nonexistent")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data


class TestAgentsEndpoint(unittest.TestCase):
    """Tests for /api/agents."""

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        fn = app.view_functions.get("api_agents")
        if fn and hasattr(fn, "_cache"):
            del fn._cache
        fn = app.view_functions.get("metrics_endpoint")
        if fn and hasattr(fn, "_cache"):
            del fn._cache

    def _mock_find(self, sessions=None):
        fake = _fake_find_sessions(sessions)
        return patch("app.find_sessions", return_value=fake)

    def test_agents_returns_list(self):
        with self._mock_find():
            resp = self.client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_agents_aggregates_stats(self):
        sessions = [
            {
                "agent": "coracode",
                "id": "sess01",
                "mtime": 1700000000,
                "content": json.dumps({"message": {"role": "user", "content": "hi"}}) + "\n"
                           + json.dumps({"message": {"role": "assistant", "content": "hello"}}) + "\n",
            },
            {
                "agent": "coracode",
                "id": "sess02",
                "mtime": 1700001000,
                "content": json.dumps({"message": {"role": "user", "content": "again"}}) + "\n",
            },
        ]
        with self._mock_find(sessions):
            resp = self.client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        entry = data[0]
        assert entry["agent"] == "coracode"
        assert entry["sessions"] == 2
        assert entry["total_messages"] == 2  # two user messages
        assert entry["total_size_h"]  # human-readable size present

    def test_agents_sorted_by_latest(self):
        sessions = [
            {
                "agent": "alpha",
                "id": "a1",
                "mtime": 1700000000,
                "content": '{"message":{"role":"user","content":"x"}}\n',
            },
            {
                "agent": "beta",
                "id": "b1",
                "mtime": 1700010000,
                "content": '{"message":{"role":"user","content":"y"}}\n',
            },
        ]
        with self._mock_find(sessions):
            resp = self.client.get("/api/agents")
        data = resp.get_json()
        assert data[0]["agent"] == "beta"  # most recent first
        assert data[1]["agent"] == "alpha"

    def test_agents_caching(self):
        sessions = [{
            "agent": "test",
            "id": "s1",
            "mtime": 1700000000,
            "content": '{"message":{"role":"user","content":"x"}}\n',
        }]
        with self._mock_find(sessions):
            resp1 = self.client.get("/api/agents")
            resp2 = self.client.get("/api/agents")
        assert resp1.get_json() == resp2.get_json()


class TestMetricsEndpoint(unittest.TestCase):
    """Tests for /metrics."""

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        fn = app.view_functions.get("metrics_endpoint")
        if fn and hasattr(fn, "_cache"):
            del fn._cache
        fn = app.view_functions.get("api_agents")
        if fn and hasattr(fn, "_cache"):
            del fn._cache

    def _mock_find(self, sessions=None):
        fake = _fake_find_sessions(sessions)
        return patch("app.find_sessions", return_value=fake)

    def test_metrics_returns_text(self):
        with self._mock_find():
            resp = self.client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("Content-Type", "")

    def test_metrics_contains_help_type(self):
        sessions = [{
            "agent": "test",
            "id": "s1",
            "mtime": 1700000000,
            "content": '{"message":{"role":"user","content":"x"}}\n',
        }]
        with self._mock_find(sessions):
            resp = self.client.get("/metrics")
        body = resp.get_data(as_text=True)
        assert "# HELP" in body
        assert "# TYPE" in body
        assert "oc_sessions_total" in body

    def test_metrics_caching(self):
        sessions = [{
            "agent": "test",
            "id": "s1",
            "mtime": 1700000000,
            "content": '{"message":{"role":"user","content":"x"}}\n',
        }]
        with self._mock_find(sessions):
            resp1 = self.client.get("/metrics")
            resp2 = self.client.get("/metrics")
        assert resp1.get_data() == resp2.get_data()


if __name__ == "__main__":
    unittest.main()
