#!/usr/bin/env bash
# Watches the OpenClaw log for model changes and updates Prometheus relabel config
# Runs as a lightweight daemon - near-zero overhead

LOG_FILE="/tmp/openclaw/openclaw-2026-05-07.log"
PROM_CONFIG="/home/dave/openclaw-telemetry/prometheus.yml"
PROM_RELOAD="http://localhost:9090/-/reload"
PROM_TOKEN="c7f9428175a6a4e73f0f4ad6da2785cfb807d3b17ceff033"
LAST_MODEL=""

echo "[model-label-watcher] Starting at $(date)"

while true; do
    # Extract current model from latest log entry
    CURRENT_MODEL=$(grep "agent model:" "$LOG_FILE" 2>/dev/null | tail -1 | \
        python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        obj = json.loads(line)
        msg = obj.get('message', '')
        if msg.startswith('agent model: '):
            # Extract: 'kilocode/kilo-auto/free (thinking=...'
            print(msg.split(' ')[2].strip())
    except: pass
" 2>/dev/null)

    if [ -n "$CURRENT_MODEL" ] && [ "$CURRENT_MODEL" != "$LAST_MODEL" ]; then
        echo "[$(date)] Model changed: $LAST_MODEL -> $CURRENT_MODEL"

        # Update prometheus.yml relabel config
        sed -i "s|replacement: .*$|replacement: \"$CURRENT_MODEL\"|" "$PROM_CONFIG"

        # Reload Prometheus
        curl -s -X POST "$PROM_RELOAD" -o /dev/null 2>/dev/null

        LAST_MODEL="$CURRENT_MODEL"
    fi

    sleep 30
done
