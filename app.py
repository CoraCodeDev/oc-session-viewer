#!/usr/bin/env python3
"""
OC Session Viewer — Lightweight OpenClaw session viewer.
Reads JSONL files server-side, renders conversation with pagination.
"""

import json
import os
import time
import yaml
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

def load_config():
    config = {
        "agents_dir": os.path.expanduser("~/.openclaw/agents"),
        "port": 8888,
        "host": "0.0.0.0",
        "debug": False,
        "page_size": 100,
        "agent_names": {},
    }
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    if os.path.exists(config_path):
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
AGENT_NAMES = CONFIG["agent_names"] or {}

# ── Helpers ──────────────────────────────────────────────────────────────────

def human_size(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024: return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

def time_ago(ts):
    d = int(time.time() - ts)
    if d < 60: return "just now"
    if d < 3600: return f"{d // 60}m ago"
    if d < 86400: return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"

def find_sessions():
    sessions = []
    if not os.path.isdir(AGENTS_DIR):
        return sessions
    for root, dirs, files in os.walk(AGENTS_DIR):
        for f in files:
            if not f.endswith(".jsonl"): continue
            if "checkpoint" in f or ".trajectory" in f: continue
            path = os.path.join(root, f)
            rel = os.path.relpath(root, AGENTS_DIR)
            agent = rel.split(os.sep)[0] if rel else "unknown"
            sid = f.replace(".jsonl", "")
            stat = os.stat(path)
            sessions.append({
                "id": sid, "agent": agent,
                "agent_name": AGENT_NAMES.get(agent, agent),
                "path": path, "size": stat.st_size,
                "size_h": human_size(stat.st_size),
                "mtime": stat.st_mtime, "mtime_h": time_ago(stat.st_mtime),
            })
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions

def parse_session(path, page=0):
    messages = []
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
            if msg.get("model"): model = msg["model"]
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
                        "role": "tool_call", "tool": fn.get("name", "?"),
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
        "page": page, "total_pages": total_pages, "total": total,
        "has_prev": page > 0, "has_next": start + PAGE_SIZE < total,
        "model": model, "tool_calls": tool_calls, "user_msgs": user_msgs,
    }

def _extract_text(content):
    if isinstance(content, str): return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text": parts.append(b.get("text", ""))
                elif b.get("type") in ("image", "image_url"): parts.append("[image]")
            elif isinstance(b, str): parts.append(b)
        return "\n".join(parts)
    return ""

def _truncate(s, n):
    return s if len(s) <= n else s[:n] + "…"

def _ts(obj):
    return obj.get("timestamp") or obj.get("time") or ""

# ── HTML Templates ───────────────────────────────────────────────────────────

# Refresh button — inserted exactly once per template
_BTN = '<button onclick="location.reload()" style="background:#161b22;border:1px solid #30363d;color:#e6edf3;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:13px;margin-left:8px;">↻ Refresh</button>'

HTML_INDEX = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sessions — OpenClaw</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0d1117;color:#e6edf3;padding:24px}
.container{max-width:1100px;margin:0 auto}
h1{font-size:1.4rem;margin-bottom:8px;color:#58a6ff}
.sub{color:#8b949e;font-size:13px;margin-bottom:16px}
.toolbar{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
.toolbar input{background:#161b22;border:1px solid #30363d;color:#e6edf3;padding:8px 12px;border-radius:6px;font-size:14px;min-width:240px;flex:1}
.toolbar select{background:#161b22;border:1px solid #30363d;color:#e6edf3;padding:8px 12px;border-radius:6px;font-size:14px}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:8px 12px;border-bottom:2px solid #30363d;color:#8b949e;font-size:11px;text-transform:uppercase;cursor:pointer}
th:hover{color:#58a6ff}
td{padding:8px 12px;border-bottom:1px solid #21262d;font-size:14px}
tr:hover td{background:#161b22}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;background:rgba(88,166,255,.15);color:#58a6ff}
.meta{color:#8b949e;font-size:13px}
</style></head><body>
<div class="container">
<h1>💻 OpenClaw Sessions """ + _BTN + """</h1>
<div class="sub">{{ sessions|length }} sessions · {{ agents|length }} agents</div>
<div class="toolbar">
  <input type="text" id="q" placeholder="Search ID or agent…" oninput="f()">
  <select id="a" onchange="f()"><option value="">All Agents</option>
  {% for ag in agents %}<option value="{{ag}}">{{ag}}</option>{% endfor %}</select>
</div>
<table><thead><tr><th>Agent</th><th>Session</th><th>Size</th><th>Updated</th></tr></thead>
<tbody id="r">
{% for s in sessions %}
<tr data-a="{{s.agent}}" data-i="{{s.id}}">
  <td><span class="badge">{{s.agent_name}}</span></td>
  <td><a href="/s/{{s.id}}">{{s.id[:8]}}…{{s.id[-6:]}}</a></td>
  <td class="meta">{{s.size_h}}</td><td class="meta">{{s.mtime_h}}</td>
</tr>{% endfor %}
</tbody></table></div>
<script>function f(){const q=document.getElementById('q').value.toLowerCase(),a=document.getElementById('a').value;
document.querySelectorAll('#r tr').forEach(r=>{r.style.display=(!a||r.dataset.a===a)&&(!q||r.dataset.i.includes(q)||r.dataset.a.includes(q))?'':'none'})}</script>
</body></html>"""

HTML_SESSION = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{session.agent_name}} — {{session.id[:12]}}…</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0d1117;color:#e6edf3;padding:24px}
.container{max-width:900px;margin:0 auto}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}
.back{font-size:14px;margin-bottom:12px;display:inline-block}
.header{background:#161b22;padding:14px 16px;border-radius:8px;margin-bottom:12px}
.header h2{font-size:1rem;margin-bottom:4px;color:#58a6ff}
.header .m{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:#8b949e}
.stats{display:flex;gap:10px;margin-bottom:14px}
.stat{background:#161b22;padding:6px 12px;border-radius:6px}
.stat .v{font-size:1.1rem;font-weight:600;color:#58a6ff}
.stat .l{font-size:10px;color:#8b949e;text-transform:uppercase}
.msg{margin-bottom:10px}
.msg-h{display:flex;align-items:center;gap:6px;margin-bottom:4px}
.role{font-size:11px;font-weight:600;padding:1px 7px;border-radius:3px;text-transform:uppercase}
.role-user{background:#238636;color:#fff}.role-assistant{background:#1f6feb;color:#fff}
.role-tool_call{background:#6e40c9;color:#fff}.role-tool_result{background:#484f58;color:#fff}
.ts{font-size:11px;color:#8b949e}
.body{background:#161b22;padding:10px 14px;border-radius:6px;white-space:pre-wrap;word-break:break-word;font-size:13px;line-height:1.55;max-height:500px;overflow-y:auto}
.body.tool{font-family:monospace;font-size:12px;max-height:200px;color:#8b949e}
.pag{display:flex;gap:8px;margin:16px 0;align-items:center}
.pag button{background:#161b22;border:1px solid #30363d;color:#e6edf3;padding:6px 14px;border-radius:6px;cursor:pointer}
.pag button:hover{border-color:#58a6ff}
.pag button:disabled{opacity:.3;cursor:default}
.pag .info{color:#8b949e;font-size:13px}
</style></head><body>
<div class="container">
<a class="back" href="/">← Sessions</a> """ + _BTN + """
<div class="header"><h2>{{session.agent_name}} — {{session.id[:12]}}…</h2>
<div class="m"><span>Model: <b>{{data.model}}</b></span><span>File: <b>{{session.size_h}}</b></span></div></div>
<div class="stats">
<div class="stat"><div class="v">{{data.total}}</div><div class="l">Messages</div></div>
<div class="stat"><div class="v">{{data.tool_calls}}</div><div class="l">Tool Calls</div></div>
<div class="stat"><div class="v">{{data.user_msgs}}</div><div class="l">User Msgs</div></div>
</div>
{% for m in data.messages %}
<div class="msg"><div class="msg-h">
  <span class="role role-{{m.role}}">{{m.role|replace('_',' ')}}</span>
  {% if m.role in ('tool_call','tool_result') %}<span style="color:#bc8cff;font-size:12px">{{m.tool}}</span>{% endif %}
  <span class="ts">{{m.ts}}</span></div>
{% if m.role == 'tool_call' %}<div class="body tool">{{m.args|e}}</div>
{% else %}<div class="body">{{m.content|e}}</div>{% endif %}</div>
{% endfor %}
<div class="pag">
{% if data.has_prev %}<a href="?p={{data.page-1}}"><button>← Prev</button></a>{% else %}<button disabled>← Prev</button>{% endif %}
<span class="info">Page {{data.page+1}}/{{data.total_pages}} ({{data.total}} msgs)</span>
{% if data.has_next %}<a href="?p={{data.page+1}}"><button>Next →</button></a>{% else %}<button disabled>Next →</button>{% endif %}
</div></div></body></html>"""

# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    sessions = find_sessions()
    agents = sorted(set(s["agent_name"] for s in sessions))
    return render_template_string(HTML_INDEX, sessions=sessions, agents=agents)

@app.route("/s/<sid>")
def view_session(sid):
    sessions = find_sessions()
    sess = next((s for s in sessions if s["id"] == sid), None)
    if not sess: return "Not found", 404
    page = request.args.get("p", 0, type=int)
    data = parse_session(sess["path"], page=page)
    return render_template_string(HTML_SESSION, session=sess, data=data)

@app.route("/api/sessions")
def api_sessions():
    return jsonify(find_sessions())

@app.route("/api/s/<sid>")
def api_session(sid):
    sessions = find_sessions()
    sess = next((s for s in sessions if s["id"] == sid), None)
    if not sess: return jsonify({"error": "not found"}), 404
    page = request.args.get("p", 0, type=int)
    return jsonify(parse_session(sess["path"], page=page))

def _scan_agents(sessions):
    """Parse session files and aggregate stats per agent."""
    agents = {}
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
        # Quick parse for message count
        try:
            with open(s["path"], "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    a["total_lines"] += 1
                    try:
                        obj = json.loads(line.strip())
                    except (json.JSONDecodeError, ValueError):
                        continue
                    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                    if msg.get("role") == "user":
                        a["total_messages"] += 1
                    elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                        a["total_tool_calls"] += len(msg["tool_calls"])
                        a["total_messages"] += 1
        except (IOError, OSError):
            pass
        if s["mtime"] > a["latest_mtime"]:
            a["latest_mtime"] = s["mtime"]

    # Format for JSON output
    result = []
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
    return result


@app.route("/api/agents")
def api_agents():
    """Aggregate stats per agent for dashboard display.

    Caches output keyed on the newest session file mtime, same strategy
    as the /metrics endpoint.
    """
    sessions = find_sessions()
    newest_mtime = max((s["mtime"] for s in sessions), default=0)
    if hasattr(api_agents, "_cache") and api_agents._cache["mtime"] == newest_mtime:
        return jsonify(api_agents._cache["data"])

    result = _scan_agents(sessions)
    api_agents._cache = {"mtime": newest_mtime, "data": result}
    return jsonify(result)

@app.route("/metrics")
def metrics_endpoint():
    """Prometheus-compatible metrics endpoint.

    Caches output keyed on the newest session file mtime, so repeated
    scrapes within the same scrape interval return instantly without
    re-reading all JSONL files.
    """
    sessions = find_sessions()
    newest_mtime = max((s["mtime"] for s in sessions), default=0)
    if hasattr(metrics_endpoint, "_cache") and metrics_endpoint._cache["mtime"] == newest_mtime:
        return metrics_endpoint._cache["data"], 200, {"Content-Type": "text/plain; version=0.0.4"}

    lines = []
    lines.append("# HELP oc_sessions_total Total number of sessions per agent")
    lines.append("# TYPE oc_sessions_total gauge")
    lines.append("# HELP oc_session_size_bytes Total session file size per agent in bytes")
    lines.append("# TYPE oc_session_size_bytes gauge")
    lines.append("# HELP oc_session_messages_total Total messages across all sessions per agent")
    lines.append("# TYPE oc_session_messages_total gauge")
    lines.append("# HELP oc_session_tool_calls_total Total tool calls across all sessions per agent")
    lines.append("# TYPE oc_session_tool_calls_total gauge")
    lines.append("# HELP oc_session_lines_total Total JSONL lines across all sessions per agent")
    lines.append("# TYPE oc_session_lines_total gauge")

    # Aggregate per agent
    agents = {}
    for s in sessions:
        aid = s["agent_name"]
        if aid not in agents:
            agents[aid] = {
                "sessions": 0, "size": 0, "messages": 0, "tool_calls": 0, "lines": 0
            }
        a = agents[aid]
        a["sessions"] += 1
        a["size"] += s["size"]
        try:
            with open(s["path"], "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    a["lines"] += 1
                    try:
                        obj = json.loads(line.strip())
                    except (json.JSONDecodeError, ValueError):
                        continue
                    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                    if msg.get("role") == "user":
                        a["messages"] += 1
                    elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                        a["tool_calls"] += len(msg["tool_calls"])
                        a["messages"] += 1
        except (IOError, OSError):
            pass

    for aid, data in sorted(agents.items()):
        # Sanitize agent name for Prometheus label
        safe = "".join(c if c.isalnum() else "_" for c in aid).lower()
        lines.append(f'oc_sessions_total{{agent="{safe}"}} {data["sessions"]}')
        lines.append(f'oc_session_size_bytes{{agent="{safe}"}} {data["size"]}')
        lines.append(f'oc_session_messages_total{{agent="{safe}"}} {data["messages"]}')
        lines.append(f'oc_session_tool_calls_total{{agent="{safe}"}} {data["tool_calls"]}')
        lines.append(f'oc_session_lines_total{{agent="{safe}"}} {data["lines"]}')

    result = "\n".join(lines) + "\n"
    metrics_endpoint._cache = {"mtime": newest_mtime, "data": result}
    return result, 200, {"Content-Type": "text/plain; version=0.0.4"}

@app.route("/health")
def health():
    return jsonify({"status": "ok", "sessions": len(find_sessions())})

# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Starting OC Session Viewer on {CONFIG['host']}:{CONFIG['port']}")
    print(f"Agents dir: {CONFIG['agents_dir']}")
    print(f"Page size: {CONFIG['page_size']}")
    app.run(host=CONFIG["host"], port=CONFIG["port"], debug=CONFIG["debug"])
