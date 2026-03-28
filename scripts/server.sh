#!/usr/bin/env bash
# Manage the search web server.
#
# Usage:
#   ./scripts/server.sh start     # start in background
#   ./scripts/server.sh stop      # stop
#   ./scripts/server.sh restart   # restart
#   ./scripts/server.sh status    # show status

set -euo pipefail
cd "$(dirname "$0")/.."

# Source env if not already loaded (direnv may not be active in this shell)
if [[ -z "${LLM_BASE_URL:-}" ]]; then
  [[ -f .envrc.local ]] && source .envrc.local
  [[ -f .envrc ]] && eval "$(grep '^export ' .envrc)" 2>/dev/null || true
fi

PID_FILE="${PID_FILE:-data/search.pid}"

_pid() {
  [[ -f "$PID_FILE" ]] && cat "$PID_FILE" || echo ""
}

_is_running() {
  local pid=$(_pid)
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

case "${1:-status}" in
  start)
    if _is_running; then
      echo "Already running (pid $(_pid))"
      exit 0
    fi
    echo "Starting search server..."
    nohup python src/main.py > data/server.log 2>&1 &
    sleep 1
    if _is_running; then
      echo "Started (pid $(_pid))"
    else
      echo "Failed to start. Check data/server.log"
      exit 1
    fi
    ;;
  stop)
    if _is_running; then
      echo "Stopping (pid $(_pid))..."
      kill "$(_pid)"
      rm -f "$PID_FILE"
      echo "Stopped"
    else
      echo "Not running"
      rm -f "$PID_FILE"
    fi
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;
  status)
    if _is_running; then
      echo "Running (pid $(_pid))"
    else
      echo "Not running"
      [[ -f "$PID_FILE" ]] && rm -f "$PID_FILE"
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
