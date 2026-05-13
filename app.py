#!/usr/bin/env python3
"""
OpenClaw Session Viewer — Lightweight session viewer for OpenClaw.
Reads JSONL files server-side, renders conversation with pagination.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ── Configuration ────────────────────────────────────────────────────────────


def load_config() -> dict[str, Any]:
    """Load config from config.yaml, overridden by environment variables."""
    config: dict[str, Any] = {
        "agents_dir": os.path.expanduser("~/.openclaw/agents"),
        "port": 8888,
        "host": "0.0.0.0",
        "debug": False,
        "page_size": 100,
        "agent_names": {},
    }
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            file_config = yaml.safe_load(f) or {}
        for key in config:
            if key in file_config:
                config[key] = file_config[key]

    config["agents_dir"] = os.environ.get("OC_AGENTS_DIR", config["agents_dir"])
    config["agents_dir"] = os.path.expanduser(config["agents_dir"])
    config["port"] = int(os.environ.get("OC_PORT", config["port"]))
    config["host"] = os.environ.get("OC_HOST", config["host"])
    config["debug"] = os.environ.get("OC_DEBUG", str(config["debug"])).lower() == "true"
    config["page_size"] = int(os.environ.get("OC_PAGE_SIZE", config["page_size"]))
    return config


CONFIG = load_config()
AGENTS_DIR = CONFIG["agents_dir"]
PAGE_SIZE = CONFIG["page_size"]
AGENT_NAMES: dict[str, str] = CONFIG["agent_names"] or {}

# ── Helpers ──────────────────────────────────────────────────────────────────


def human_size(n: int) -> str:
    """Format byte count to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def time_ago(ts: float) -> str:
    """Format a timestamp as a relative time string."""
    delta = int(time.time() - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _extract_text(content: Any) -> str:
    """Extract plain text from message content (string, list of blocks, etc.)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") in ("image", "image_url"):
                    parts.append("[image]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _truncate(s: str, n: int) -> str:
    """Truncate string to n chars with ellipsis."""
    return s if len(s) <= n else s[:n] + "…"


def _ts(obj: dict) -> str:
    """Extract timestamp string from a JSONL object."""
    return obj.get("timestamp") or obj.get("time") or ""


def _parse_session_stats(path: str) -> dict[str, int]:
    """Parse a single JSONL file and return aggregate stats.

    Returns dict with keys: lines, messages, tool_calls.
    Shared by api_agents and metrics_endpoint to avoid duplicate logic.
    """
    stats: dict[str, int] = {"lines": 0, "messages": 0, "tool_calls": 0}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stats["lines"] += 1
                try:
                    obj = json.loads(line.strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                role = msg.get("role")
                if role == "user":
                    stats["messages"] += 1
                elif role == "assistant" and msg.get("tool_calls"):
                    stats["tool_calls"] += len(msg["tool_calls"])
                    stats["messages"] += 1
    except (IOError, OSError):
        pass
    return stats


def _compute_agent_stats() -> list[dict[str, Any]]:
    """Aggregate stats per agent. Returns a list sorted by latest activity."""
    sessions = find_sessions()
    newest_mtime = max((s["mtime"] for s in sessions), default=0)

    agents: dict[str, dict[str, Any]] = {}
    for s in sessions:
        aid = s["agent_name"]
        if aid not in agents:
            agents[aid] = {
                "agent": aid,
                "sessions": 0,
                "total_size": 0,
                "total_lines": 0,
                "total_messages": 0,
                "total_tool_calls": 0,
                "latest_mtime": s["mtime"],
            }
        a = agents[aid]
        a["sessions"] += 1
        a["total_size"] += s["size"]
        file_stats = _parse_session_stats(s["path"])
        a["total_lines"] += file_stats["lines"]
        a["total_messages"] += file_stats["messages"]
        a["total_tool_calls"] += file_stats["tool_calls"]
        if s["mtime"] > a["latest_mtime"]:
            a["latest_mtime"] = s["mtime"]

    result: list[dict[str, Any]] = []
    for aid, data in agents.items():
        result.append({
            "agent": aid,
            "sessions": data["sessions"],
            "total_size": data["total_size"],
            "total_size_h": human_size(data["total_size"]),
            "total_lines": data["total_lines"],
            "total_messages": data["total_messages"],
            "total_tool_calls": data["total_tool_calls"],
            "latest_mtime": data["latest_mtime"],
            "latest": time_ago(data["latest_mtime"]),
        })
    result.sort(key=lambda x: x["latest_mtime"], reverse=True)
    result.insert(0, {"_cache_mtime": newest_mtime})
    return result


# ── Session discovery ────────────────────────────────────────────────────────


def find_sessions() -> list[dict[str, Any]]:
    """Walk AGENTS_DIR and return session metadata sorted by mtime desc."""
    sessions: list[dict[str, Any]] = []
    if not os.path.isdir(AGENTS_DIR):
        return sessions
    for root, dirs, files in os.walk(AGENTS_DIR):
        dirs[:] = [d for d in dirs if d not in ("cron", "agents")]
        for f in files:
            if not f.endswith(".jsonl"):
                continue
            if "checkpoint" in f or ".trajectory" in f:
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(root, AGENTS_DIR)
            agent = rel.split(os.sep)[0] if rel else "unknown"
            sid = f.replace(".jsonl", "")
            stat = os.stat(path)
            sessions.append({
                "id": sid,
                "agent": agent,
                "agent_name": AGENT_NAMES.get(agent, agent),
                "path": path,
                "size": stat.st_size,
                "size_h": human_size(stat.st_size),
                "mtime": stat.st_mtime,
                "mtime_h": time_ago(stat.st_mtime),
            })
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


# ── Session parsing ──────────────────────────────────────────────────────────


def _build_page_numbers(page: int, total_pages: int) -> list[Any]:
    """Build a list of page numbers with ellipsis for pagination display."""
    if total_pages <= 7:
        return list(range(1, total_pages + 1))

    pages: list[Any] = [1]
    if page > 2:
        pages.append("...")
    for p in range(max(2, page), min(total_pages, page + 3) + 1):
        pages.append(p)
    if page < total_pages - 3:
        pages.append("...")
    pages.append(total_pages)
    return pages


def parse_session(path: str, page: int = 0) -> dict[str, Any]:
    """Parse a JSONL session file and return paginated messages."""
    messages: list[dict[str, Any]] = []
    model = "unknown"
    tool_calls = 0
    user_msgs = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                obj = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            if msg.get("model"):
                model = msg["model"]
            role = msg.get("role", "")
            if role == "user":
                content = _extract_text(msg.get("content"))
                if content:
                    messages.append({"role": "user", "content": content, "ts": _ts(obj)[:19]})
                    user_msgs += 1
            elif role == "assistant":
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    messages.append({
                        "role": "tool_call",
                        "tool": fn.get("name", "?"),
                        "args": _truncate(fn.get("arguments", ""), 400),
                        "ts": _ts(obj)[:19],
                    })
                    tool_calls += 1
                content = _extract_text(msg.get("content"))
                if content:
                    messages.append({"role": "assistant", "content": content, "ts": _ts(obj)[:19]})
            elif role in ("tool", "function"):
                content = _extract_text(msg.get("content"))
                if content:
                    messages.append({
                        "role": "tool_result",
                        "tool": msg.get("name", msg.get("tool_call_id", "?")),
                        "content": _truncate(content, 2000),
                        "ts": _ts(obj)[:19],
                    })

    total = len(messages)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    start = page * PAGE_SIZE
    return {
        "messages": messages[start : start + PAGE_SIZE],
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "has_prev": page > 0,
        "has_next": start + PAGE_SIZE < total,
        "page_numbers": _build_page_numbers(page, total_pages),
        "model": model,
        "tool_calls": tool_calls,
        "user_msgs": user_msgs,
    }


# ── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def index() -> str:
    """Session list page."""
    sessions = find_sessions()
    agents = sorted(set(s["agent_name"] for s in sessions))
    return render_template("index.html", sessions=sessions, agents=agents)


@app.route("/s/<sid>")
def view_session(sid: str) -> tuple[str, int] | str:
    """Session detail page with pagination."""
    sessions = find_sessions()
    sess = next((s for s in sessions if s["id"] == sid), None)
    if not sess:
        return "Not found", 404
    page = request.args.get("p", 0, type=int)
    data = parse_session(sess["path"], page=page)
    return render_template("session.html", session=sess, data=data)


@app.route("/api/sessions")
def api_sessions() -> Any:
    """List all sessions (JSON)."""
    return jsonify(find_sessions())


@app.route("/api/s/<sid>")
def api_session(sid: str) -> tuple[Any, int]:
    """Session data for a page (JSON)."""
    sessions = find_sessions()
    sess = next((s for s in sessions if s["id"] == sid), None)
    if not sess:
        return jsonify({"error": "not found"}), 404
    page = request.args.get("p", 0, type=int)
    return jsonify(parse_session(sess["path"], page=page))


@app.route("/api/agents")
def api_agents() -> Any:
    """Aggregate stats per agent, cached on newest session mtime."""
    stats = _compute_agent_stats()
    cache_mtime = stats[0].pop("_cache_mtime", 0)

    if hasattr(api_agents, "_cache") and api_agents._cache["mtime"] == cache_mtime:  # type: ignore[attr-defined]
        return jsonify(api_agents._cache["data"])  # type: ignore[attr-defined]

    api_agents._cache = {"mtime": cache_mtime, "data": stats}  # type: ignore[attr-defined]
    return jsonify(stats)


@app.route("/metrics")
def metrics_endpoint() -> tuple[str, int, dict[str, str]]:
    """Prometheus-compatible metrics endpoint, cached on newest session mtime."""
    stats = _compute_agent_stats()
    cache_mtime = stats[0].pop("_cache_mtime", 0)

    if hasattr(metrics_endpoint, "_cache") and metrics_endpoint._cache["mtime"] == cache_mtime:  # type: ignore[attr-defined]
        return (metrics_endpoint._cache["data"], 200, {"Content-Type": "text/plain; version=0.0.4"})  # type: ignore[attr-defined]

    lines = [
        "# HELP oc_sessions_total Total number of sessions per agent",
        "# TYPE oc_sessions_total gauge",
        "# HELP oc_session_size_bytes Total session file size per agent in bytes",
        "# TYPE oc_session_size_bytes gauge",
        "# HELP oc_session_messages_total Total messages across all sessions per agent",
        "# TYPE oc_session_messages_total gauge",
        "# HELP oc_session_tool_calls_total Total tool calls across all sessions per agent",
        "# TYPE oc_session_tool_calls_total gauge",
        "# HELP oc_session_lines_total Total JSONL lines across all sessions per agent",
        "# TYPE oc_session_lines_total gauge",
    ]

    for s in stats:
        safe = "".join(c if c.isalnum() else "_" for c in s["agent"]).lower()
        lines.append(f'oc_sessions_total{{agent="{safe}"}} {s["sessions"]}')
        lines.append(f'oc_session_size_bytes{{agent="{safe}"}} {s["total_size"]}')
        lines.append(f'oc_session_messages_total{{agent="{safe}"}} {s["total_messages"]}')
        lines.append(f'oc_session_tool_calls_total{{agent="{safe}"}} {s["total_tool_calls"]}')
        lines.append(f'oc_session_lines_total{{agent="{safe}"}} {s["total_lines"]}')

    result = "\n".join(lines) + "\n"
    metrics_endpoint._cache = {"mtime": cache_mtime, "data": result}  # type: ignore[attr-defined]
    return result, 200, {"Content-Type": "text/plain; version=0.0.4"}


@app.route("/health")
def health() -> Any:
    """Health check endpoint."""
    return jsonify({"status": "ok", "sessions": len(find_sessions())})


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger(__name__)
    logger.info("Starting OpenClaw Session Viewer on %s:%s", CONFIG["host"], CONFIG["port"])
    logger.info("Agents dir: %s", CONFIG["agents_dir"])
    logger.info("Page size: %s", CONFIG["page_size"])
    app.run(host=CONFIG["host"], port=CONFIG["port"], debug=CONFIG["debug"])
