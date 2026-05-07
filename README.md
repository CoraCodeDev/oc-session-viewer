# OC Session Viewer

A lightweight, server-side session viewer for [OpenClaw](https://github.com/openclaw/openclaw). Reads session JSONL files and renders them with pagination — fast even for 36MB+ files because it only loads 100 messages at a time, never the full file.

## Features

- **Server-side parsing** — reads JSONL files on demand, no browser memory hog
- **Pagination** — configurable page size (default 100 messages per page)
- **Refresh button** — reload sessions without restarting the app
- **Search & filter** — search by session ID or agent, filter by agent
- **Full conversation view** — user messages, assistant responses, tool calls, tool results
- **JSON API** — programmatic access to sessions for scripting
- **Zero dependencies on OpenClaw internals** — just reads the JSONL files

## Quick Start

```bash
# Clone
git clone https://github.com/CoraCodeDev/oc-session-viewer.git
cd oc-session-viewer

# Install dependencies
pip install -r requirements.txt

# Run (reads from ~/.openclaw/agents by default)
python app.py
```

Open `http://localhost:8888` in your browser.

## Configuration

All settings live in `config.yaml` and can be overridden with environment variables.

### config.yaml

```yaml
agents_dir: "~/.openclaw/agents"   # Where session JSONL files live
port: 8888                         # HTTP port
host: "0.0.0.0"                    # Bind address
debug: false                       # Flask debug mode
page_size: 100                     # Messages per page

# Friendly names for agent directories (optional)
agent_names: {}
  # coracode: "CoraCode"
  # tester: "Tester"
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OC_AGENTS_DIR` | `~/.openclaw/agents` | Path to OpenClaw agents directory |
| `OC_PORT` | `8888` | HTTP port |
| `OC_HOST` | `0.0.0.0` | Bind address |
| `OC_DEBUG` | `false` | Flask debug mode |
| `OC_PAGE_SIZE` | `100` | Messages per page |

Environment variables take priority over `config.yaml`.

## API

| Endpoint | Description |
|---|---|
| `GET /` | Session list with search/filter (HTML) |
| `GET /s/<session-id>` | Session view with pagination + refresh (HTML) |
| `GET /api/sessions` | List all sessions (JSON) |
| `GET /api/s/<session-id>?p=0` | Session data for a page (JSON) |
| `GET /health` | Health check (JSON) |

### Example API usage

```bash
# List all sessions
curl http://localhost:8888/api/sessions | jq '.[].agent_name'

# Get page 0 of a specific session
curl "http://localhost:8888/api/s/<session-id>?p=0" | jq '.messages[0]'

# Get page count
curl "http://localhost:8888/api/s/<session-id>" | jq '.total_pages'
```

## Deployment

### Running directly

```bash
pip install -r requirements.txt
python app.py
```

### Running with systemd

Create `/etc/systemd/system/oc-session-viewer.service`:

```ini
[Unit]
Description=OC Session Viewer
After=network.target

[Service]
Type=simple
User=<user>
WorkingDirectory=/opt/oc-session-viewer
Environment=OC_AGENTS_DIR=/home/<user>/.openclaw/agents
Environment=OC_PORT=8888
ExecStart=/usr/bin/python3 /opt/oc-session-viewer/app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now oc-session-viewer
```

### Running with Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8888
CMD ["python", "app.py"]
```

```bash
docker build -t oc-session-viewer .
docker run -d -p 8888:8888 \
  -v ~/.openclaw/agents:/root/.openclaw/agents:ro \
  -e OC_AGENTS_DIR=/root/.openclaw/agents \
  oc-session-viewer
```

### Running behind a reverse proxy (nginx)

```nginx
server {
    listen 80;
    server_name sessions.example.com;

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## File Structure

```
oc-session-viewer/
├── app.py              # Main application
├── config.yaml         # Configuration file
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
├── Dockerfile          # Docker build file
└── README.md           # This file
```

## How it works

1. Walks the OpenClaw agents directory to find `*.jsonl` session files
2. Excludes `.trajectory.jsonl` and `.checkpoint.*.jsonl` files (duplicates/overlays)
3. Parses each file line-by-line, extracting user messages, assistant responses, tool calls, and tool results
4. Returns paginated results — only `page_size` messages per request
5. The browser never sees more than one page at a time, so even 36MB files load instantly

## License

MIT
