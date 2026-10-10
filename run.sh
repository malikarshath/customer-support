#!/usr/bin/env bash
# Start, inspect and stop the whole support desk (SPEC section 6). Every box starts in dependency order, each
# with a health check before the next one begins:  Postgres -> MCP Toolbox -> Judge -> Masker -> Phoenix -> CLI/web
#
#   ./run.sh [web|cli]    start everything that is down, then run the web app (default) or the CLI
#   ./run.sh services     start everything that is down and return
#   ./run.sh status       show each service; exit 0 only if all are up
#   ./run.sh reset        re-seed the database (db/seed.sql)
#   ./run.sh stop         stop toolbox, judge, masker, web. Phoenix and Postgres keep running (O-1)
#   ./run.sh stop all     also stop Phoenix
#
# Secrets come from .env (never this file). A service that is already healthy is left alone.
set -uo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "missing .env (copy .env.example and fill in your keys)" >&2; exit 2; }
set -a; . ./.env; set +a

PY=.venv/bin/python
LOGS=.run/logs
mkdir -p "$LOGS" .toolbox .phoenix
TOOLBOX_PORT="${TOOLBOX_PORT:-5001}"; JUDGE_PORT="${JUDGE_PORT:-10002}"; MASKER_PORT="${MASKER_PORT:-10003}"
PHOENIX_PORT="${PHOENIX_PORT:-6006}"; WEB_PORT="${WEB_PORT:-8000}"; DB_PORT="${DB_PORT:-5432}"
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"

# --- health probes (one per service) -------------------------------------------------------------
up_postgres() { pg_isready -q -h "${DB_HOST:-127.0.0.1}" -p "$DB_PORT" 2>/dev/null; }
up_toolbox()  { curl -fs -o /dev/null "http://127.0.0.1:$TOOLBOX_PORT/" 2>/dev/null; }
up_judge()    { curl -fs -o /dev/null "http://127.0.0.1:$JUDGE_PORT/.well-known/agent.json" 2>/dev/null; }
up_masker()   { curl -fs -o /dev/null "http://127.0.0.1:$MASKER_PORT/.well-known/agent.json" 2>/dev/null; }
up_phoenix()  { curl -fs -o /dev/null "http://127.0.0.1:$PHOENIX_PORT/healthz" 2>/dev/null; }
up_web()      { curl -s -o /dev/null "http://127.0.0.1:$WEB_PORT/health" 2>/dev/null; }

# Start a command fully detached from this shell, so it outlives `./run.sh` and the CLI (O-1).
#   Its own session (setsid via python: macOS has no setsid binary) so a closed terminal, a killed process group or
#   a tool harness ending its shell cannot take the service down with it.
detach() {
  local log="$1"; shift
  "$PY" - "$log" "$@" <<'PYEOF'
import subprocess, sys
log, cmd = sys.argv[1], sys.argv[2:]
with open(log, "ab") as f:
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=f, start_new_session=True, close_fds=True)
PYEOF
}

wait_up() { # name probe timeout_seconds
  local name="$1" probe="$2" t="${3:-60}" i=0
  while ! $probe; do
    i=$((i+1)); [ "$i" -ge "$t" ] && { echo "  FAILED: $name did not become healthy in ${t}s (see $LOGS/$name.log)" >&2; return 1; }
    sleep 1
  done
}

start() { # name probe timeout launch...
  local name="$1" probe="$2" t="$3"; shift 3
  if $probe; then echo "  $name: already up"; return 0; fi
  echo "  $name: starting..."
  "$@" || return 1
  wait_up "$name" "$probe" "$t" && echo "  $name: up"
}

launch_postgres() { brew services start postgresql@16 >"$LOGS/postgres.log" 2>&1; }
launch_toolbox()  { detach "$LOGS/toolbox.log" npx -y @toolbox-sdk/server@1.13.1 --config mcp_toolbox/tools.yaml --enable-api --address 127.0.0.1 --port "$TOOLBOX_PORT"; }
launch_judge()    { detach "$LOGS/judge.log" "$PY" -m support.services.judge; }
launch_masker()   { detach "$LOGS/masker.log" "$PY" -m support.services.masker; }
launch_phoenix()  { PHOENIX_WORKING_DIR="$PWD/.phoenix" PHOENIX_HOST=127.0.0.1 detach "$LOGS/phoenix.log" .venv/bin/phoenix serve; }
launch_web()      { detach "$LOGS/web.log" "$PY" -m uvicorn support.web:app --host 127.0.0.1 --port "$WEB_PORT" --log-level warning; }

services() {
  echo "Starting services in dependency order:"
  start postgres up_postgres 30  launch_postgres || return 1
  start toolbox  up_toolbox  90  launch_toolbox  || return 1
  start judge    up_judge    60  launch_judge    || return 1
  start masker   up_masker   60  launch_masker   || return 1
  start phoenix  up_phoenix  150 launch_phoenix  || return 1
}

status() {
  local bad=0 name probe port
  for row in "postgres:up_postgres:$DB_PORT" "toolbox:up_toolbox:$TOOLBOX_PORT" "judge:up_judge:$JUDGE_PORT" \
             "masker:up_masker:$MASKER_PORT" "phoenix:up_phoenix:$PHOENIX_PORT" "web:up_web:$WEB_PORT"; do
    IFS=: read -r name probe port <<<"$row"
    if $probe; then printf '  %-9s up    :%s\n' "$name" "$port"; else printf '  %-9s DOWN  :%s\n' "$name" "$port"; [ "$name" != web ] && bad=1; fi
  done
  return $bad
}

kill_port() { local pids; pids=$(lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null); [ -n "$pids" ] && kill $pids 2>/dev/null; return 0; }

case "${1:-web}" in
  web)      services || exit 1
            echo "Web UI: http://127.0.0.1:$WEB_PORT"
            start web up_web 30 launch_web || exit 1 ;;
  cli)      services || exit 1
            shift; exec "$PY" -m support.cli "$@" ;;
  services) services ;;
  status)   status ;;
  reset)    up_postgres || { echo "postgres is down; run ./run.sh services first" >&2; exit 1; }
            bash db/db.sh reset ;;
  stop)     for p in "$WEB_PORT" "$TOOLBOX_PORT" "$JUDGE_PORT" "$MASKER_PORT"; do kill_port "$p"; done
            [ "${2:-}" = all ] && kill_port "$PHOENIX_PORT"
            echo "stopped web, toolbox, judge, masker${2:+, phoenix}. (Phoenix and Postgres keep running unless 'stop all'.)" ;;
  *)        sed -n '2,13p' "$0"; exit 2 ;;
esac
