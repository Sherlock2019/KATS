#!/usr/bin/env bash
# =============================================================================
# start.sh — serve the KT Support demo, and optionally the whole AI backend.
#
#   ./start.sh              EVERYTHING. Installs what is missing, then runs:
#                             PostgreSQL + pgvector   (docker, port 5433)
#                             FastAPI RAG API         (uvicorn, port 8001)
#                             Ollama + local models   (port 11434)
#                             seeds the demo corpus on an empty store
#                             serves the standalone demo (port 8000)
#
#   ./start.sh all          the above PLUS kt-ai-support
#                             its FastAPI          (port 8100)
#                             its PostgreSQL       (port 5434)
#
#   ./start.sh offline      no docker, no models — just serve the standalone
#                           file. The original zero-dependency path.
#
#   ./start.sh chooser      the chooser page (legacy view vs KATS)
#   ./start.sh dev          serve kt_support_v9.html (multi-file source)
#   ./start.sh v8           serve the previous release
#   ./start.sh core         serve the legacy ticket view
#
#   ./start.sh install      install/pull everything, start nothing
#   ./start.sh stop         stop the stack (Postgres container + API)
#
#   --port 9000    serve the UI on a specific port
#   --no-open      don't launch a browser
#   --build        force a rebuild of the standalone bundle
#
# The standalone bundle is rebuilt AUTOMATICALLY whenever a source file is
# newer than it, so the demo file can never silently serve stale code.
#
# Environment overrides (all optional, see rag/.env.example):
#   KATS_LLM_MODEL=gemma4:latest  better reasoning, several minutes/answer on CPU
#   KATS_EMBED_MODEL=...          embedding model (must match the vector dim)
#   SKIP_OLLAMA=1                 don't touch Ollama
#   SKIP_PULL=1                   don't pull models (use whatever is installed)
#   INSTALL_REQUIREMENTS=0        don't touch the Python venv
#   SEED_DEMO=0                   don't ingest the demo data
#   FORCE_SEED=1                  re-ingest even if the store is not empty
#
# Why serve instead of double-clicking the file:
# Safari (and some hardened Chrome policies) block localStorage on file://
# URLs, so published KB articles and closed cases would not persist. Over
# http://localhost they always do.
#
# Stop with Ctrl-C.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"
APP_DIR="$(pwd)"

LANDING_FILE="rax_ticket_support_page.html"
DEMO_FILE="kt_support_demo_v9.html"
DEMO_V8_FILE="kt_support_demo.html"
DEV_FILE="kt_support_v9.html"

PAGE="$LANDING_FILE"
PORT=8000
OPEN_BROWSER=1
DO_BUILD=0
MODE="serve"          # serve | rag | all | install | stop

# Whether the caller actually chose a page or a mode. `./start.sh --no-open`
# should still get the full default; only naming a page or a mode opts out
# of it. Without these two flags the default could not tell "no arguments"
# from "flags but no mode".
PAGE_SET=0
MODE_SET=0

# --- RAG stack settings ------------------------------------------------------
RAG_DIR="${APP_DIR}/rag"
VENV_DIR="${VENV_DIR:-${APP_DIR}/.venv}"
API_HOST="${KATS_API_HOST:-127.0.0.1}"
API_PORT="${KATS_API_PORT:-8001}"
PG_PORT="${POSTGRES_PORT:-5433}"
OLLAMA_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
# phi3 by default: 2.2 GB and ~1 minute per grounded answer on CPU, where
# gemma4 (9.6 GB) takes several. Set KATS_LLM_MODEL=gemma4:latest to trade
# speed for reasoning.
LLM_MODEL="${KATS_LLM_MODEL:-phi3}"
EMBED_MODEL="${KATS_EMBED_MODEL:-embeddinggemma}"
INSTALL_REQUIREMENTS="${INSTALL_REQUIREMENTS:-1}"
SKIP_OLLAMA="${SKIP_OLLAMA:-0}"
SKIP_PULL="${SKIP_PULL:-0}"

# rag/.env overrides the defaults above if it exists.
if [ -f "${RAG_DIR}/.env" ]; then
  set -a; . "${RAG_DIR}/.env"; set +a
  LLM_MODEL="${KATS_LLM_MODEL:-$LLM_MODEL}"
  EMBED_MODEL="${KATS_EMBED_MODEL:-$EMBED_MODEL}"
  API_PORT="${KATS_API_PORT:-$API_PORT}"
  PG_PORT="${POSTGRES_PORT:-$PG_PORT}"
fi

while [ $# -gt 0 ]; do
  case "$1" in
    dev|--dev)      PAGE="$DEV_FILE";     PAGE_SET=1; shift ;;
    demo|--demo)    PAGE="$DEMO_FILE";    PAGE_SET=1; shift ;;
    kats|--kats)    PAGE="$DEMO_FILE";    PAGE_SET=1; shift ;;
    v8|--v8)        PAGE="$DEMO_V8_FILE"; PAGE_SET=1; shift ;;
    core|--core)    PAGE="core_ticket_rebuilt.html"; PAGE_SET=1; shift ;;
    chooser|--chooser) PAGE="$LANDING_FILE"; PAGE_SET=1; shift ;;
    offline|--offline)
      # The original zero-dependency path: serve the standalone file, start
      # nothing. Kept because it is the only mode that needs no docker.
      MODE="serve"; MODE_SET=1; PAGE="$DEMO_FILE"; PAGE_SET=1; shift ;;
    rag|--rag)      MODE="rag";  MODE_SET=1; shift ;;
    all|--all)      MODE="all";  MODE_SET=1; shift ;;
    install|--install) MODE="install"; MODE_SET=1; shift ;;
    stop|--stop)    MODE="stop";    MODE_SET=1; shift ;;
    -p|--port)      PORT="${2:-8000}"; shift 2 ;;
    --no-open)      OPEN_BROWSER=0; shift ;;
    -b|--build)     DO_BUILD=1; shift ;;
    -h|--help)      sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1  (try --help)" >&2; exit 1 ;;
  esac
done

# No mode named: bring everything up. `./start.sh` should be the one command
# that gives you a working system, not the one that gives you the least.
[ "$MODE_SET" -eq 0 ] && MODE="rag"
# No page named, and we are starting the stack: serve the standalone demo,
# which is the page the RAG chat lives on.
[ "$PAGE_SET" -eq 0 ] && [ "$MODE" != "serve" ] && PAGE="$DEMO_FILE"

say()  { printf '  %s\n' "$*"; }
step() { printf '\n  \033[1m%s\033[0m\n' "$*"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\n  \033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# =============================================================================
# RAG stack
# =============================================================================

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "${RAG_DIR}/docker-compose.yml" "$@"
  elif have docker-compose; then
    docker-compose -f "${RAG_DIR}/docker-compose.yml" "$@"
  else
    die "docker compose not found. Install Docker, or run Postgres yourself and set KATS_DATABASE_URL."
  fi
}

ensure_docker() {
  have docker || die "docker not found.
  WSL/Ubuntu:  ./rag/install-docker.sh  (or enable Docker Desktop's WSL integration)
  Then re-run: ./start.sh rag"

  if ! docker info >/dev/null 2>&1; then
    say "Docker daemon not responding — trying to start it…"
    sudo service docker start >/dev/null 2>&1 || true
    sleep 3
    docker info >/dev/null 2>&1 || die "Docker daemon is not reachable.
  Try:  sudo service docker start
  Or enable WSL integration in Docker Desktop, then re-run ./start.sh rag"
  fi
  ok "docker is up"
}

ensure_postgres() {
  step "PostgreSQL + pgvector (port ${PG_PORT})"
  ensure_docker
  compose up -d

  say "waiting for the database to accept connections…"
  for i in $(seq 1 60); do
    if compose exec -T postgres pg_isready -U "${POSTGRES_USER:-kats}" >/dev/null 2>&1; then
      ok "postgres ready on ${PG_PORT}"
      return 0
    fi
    sleep 1
  done
  die "Postgres did not come up. Check:  docker compose -f rag/docker-compose.yml logs postgres"
}

ensure_python() {
  [ "$INSTALL_REQUIREMENTS" = "1" ] || { say "skipping python install (INSTALL_REQUIREMENTS=0)"; return 0; }
  step "Python environment"

  have python3 || die "python3 not found. sudo apt install -y python3 python3-venv python3-pip"
  if ! python3 -c 'import venv' >/dev/null 2>&1; then
    die "python3-venv missing. sudo apt install -y python3-venv"
  fi

  if [ ! -d "$VENV_DIR" ]; then
    say "creating $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi

  # Only reinstall when requirements.txt actually changed — a pip run on every
  # boot is 20 wasted seconds and the single most common reason people stop
  # using a launcher script.
  local req="${RAG_DIR}/backend/requirements.txt"
  local stamp="${VENV_DIR}/.kats-requirements.sha"
  local now=""
  if have sha256sum; then now="$(sha256sum "$req" | awk '{print $1}')"; fi

  if [ -n "$now" ] && [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$now" ]; then
    ok "python packages already current"
  else
    say "installing backend requirements…"
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
    "${VENV_DIR}/bin/pip" install --quiet -r "$req"
    [ -n "$now" ] && printf '%s' "$now" > "$stamp"
    ok "python packages installed"
  fi
}

ensure_ollama() {
  [ "$SKIP_OLLAMA" = "1" ] && { say "skipping ollama (SKIP_OLLAMA=1)"; return 0; }
  step "Ollama + local models"

  if ! have ollama; then
    warn "ollama is not installed."
    say "Install it with:  curl -fsSL https://ollama.com/install.sh | sh"
    say "Continuing — retrieval still works, but chat answers will not."
    return 0
  fi

  if ! curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    say "starting ollama serve in the background…"
    nohup ollama serve >"${APP_DIR}/ollama.log" 2>&1 &
    for i in $(seq 1 30); do
      curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1 && break
      sleep 1
    done
  fi

  if ! curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    warn "ollama did not answer at ${OLLAMA_URL} — see ollama.log"
    return 0
  fi
  ok "ollama is up at ${OLLAMA_URL}"

  [ "$SKIP_PULL" = "1" ] && { say "skipping model pulls (SKIP_PULL=1)"; return 0; }

  local installed
  installed="$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')"

  pull_if_missing() {
    local model="$1" why="$2"
    if printf '%s\n' "$installed" | grep -qx "$model"; then
      ok "$model already installed"
    else
      say "pulling $model  ($why) — first run only, this can take a few minutes"
      ollama pull "$model" || warn "could not pull $model — continuing without it"
    fi
  }

  # Two models, two jobs. A chat model cannot produce embeddings; the embedder
  # cannot answer a question. Both are small enough for a CPU laptop.
  pull_if_missing "$LLM_MODEL"   "generation"
  pull_if_missing "$EMBED_MODEL" "retrieval embeddings, 768-dim"
}

start_api() {
  step "RAG API (port ${API_PORT})"

  if curl -fsS "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1; then
    ok "an API is already listening on ${API_PORT} — reusing it"
    return 0
  fi

  export KATS_API_HOST="$API_HOST" KATS_API_PORT="$API_PORT"
  export KATS_LLM_MODEL="$LLM_MODEL" KATS_EMBED_MODEL="$EMBED_MODEL"
  export OLLAMA_BASE_URL="$OLLAMA_URL"
  export KATS_DATABASE_URL="${KATS_DATABASE_URL:-postgresql://${POSTGRES_USER:-kats}:${POSTGRES_PASSWORD:-kats_password}@127.0.0.1:${PG_PORT}/${POSTGRES_DB:-kats_rag}}"

  nohup "${VENV_DIR}/bin/uvicorn" app.main:app \
      --host "$API_HOST" --port "$API_PORT" \
      --app-dir "${RAG_DIR}/backend" \
      >"${APP_DIR}/rag-api.log" 2>&1 &
  echo $! > "${APP_DIR}/rag-api.pid"

  for i in $(seq 1 45); do
    if curl -fsS "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1; then
      ok "API ready at http://${API_HOST}:${API_PORT}  (docs: /docs)"
      return 0
    fi
    sleep 1
  done

  warn "the API did not answer in 45s. Last lines of rag-api.log:"
  tail -n 20 "${APP_DIR}/rag-api.log" 2>/dev/null | sed 's/^/      /'
  return 1
}

seed_demo() {
  [ "${SEED_DEMO:-1}" = "1" ] || { say "skipping demo ingest (SEED_DEMO=0)"; return 0; }
  step "Demo data"

  have node || { warn "node not found — skipping demo ingest"; return 0; }

  local health tickets
  health="$(curl -fsS "http://${API_HOST}:${API_PORT}/health" 2>/dev/null || true)"
  [ -z "$health" ] && { warn "API not answering — skipping demo ingest"; return 0; }

  tickets="$(printf '%s' "$health" | sed -n 's/.*"tickets":\([0-9]*\).*/\1/p')"

  # Only on an empty store, unless forced. Re-ingesting is harmless (every
  # write is an upsert on ticket_id) but it re-embeds everything, which is the
  # slow part, and nobody wants that on every start.
  if [ "${tickets:-0}" != "0" ] && [ "${FORCE_SEED:-0}" != "1" ]; then
    ok "store already holds ${tickets} documents — not re-ingesting (FORCE_SEED=1 to redo)"
    return 0
  fi

  say "ingesting the KB, the demo tickets and the case history…"
  if KATS_API_URL="http://${API_HOST}:${API_PORT}" node rag/seed_demo.js; then
    ok "demo data ingested"
  else
    warn "demo ingest reported problems — the chat will still work on whatever landed"
  fi
}

stop_stack() {
  step "Stopping the RAG stack"
  if [ -f "${APP_DIR}/rag-api.pid" ]; then
    kill "$(cat "${APP_DIR}/rag-api.pid")" 2>/dev/null && ok "API stopped" || say "API was not running"
    rm -f "${APP_DIR}/rag-api.pid"
  fi
  if [ -d "$RAG_DIR" ] && have docker; then
    compose down && ok "postgres stopped (data volume kept)"
  fi

  # `./start.sh all` can have started the second stack, and someone stopping
  # things expects everything to stop.
  if [ -d "${APP_DIR}/kt-ai-support" ]; then
    ( cd "${APP_DIR}/kt-ai-support" && bash ./start.sh stop >/dev/null 2>&1 ) \
      && ok "kt-ai-support stopped" || true
  fi

  echo
  say "Data volumes survive. To wipe them:"
  say "  docker compose -f rag/docker-compose.yml down -v"
  say "  docker compose -f kt-ai-support/docker-compose.yml down -v"
  echo
}

print_rag_health() {
  local health
  health="$(curl -fsS "http://${API_HOST}:${API_PORT}/health" 2>/dev/null || true)"
  [ -z "$health" ] && { warn "no /health response"; return 0; }

  # Small enough to read with grep rather than pull in jq.
  local llm embed_mode embed
  llm="$(printf '%s' "$health"       | sed -n 's/.*"llm_model":"\([^"]*\)".*/\1/p')"
  embed="$(printf '%s' "$health"     | sed -n 's/.*"embed_model":"\([^"]*\)".*/\1/p')"
  embed_mode="$(printf '%s' "$health"| sed -n 's/.*"embed_mode":"\([^"]*\)".*/\1/p')"

  say "generation : ${llm:-unknown}"
  say "embeddings : ${embed:-unknown} (${embed_mode:-unknown})"
  if [ "$embed_mode" != "ollama" ]; then
    warn "running the hash fallback embedder — retrieval is keyword-only."
    warn "fix it with:  ollama pull ${EMBED_MODEL}   then restart ./start.sh rag"
  fi
}

# =============================================================================
# Modes that exit early
# =============================================================================
if [ "$MODE" = "stop" ]; then
  stop_stack
  exit 0
fi

if [ "$MODE" = "install" ]; then
  [ -d "$RAG_DIR" ] || die "rag/ not found — nothing to install."
  ensure_docker
  ensure_python
  ensure_ollama
  echo
  ok "Everything installed. Start it with:  ./start.sh rag"
  echo
  exit 0
fi

# =============================================================================
# Rebuild the bundle — automatically when it is out of date
#
# kt_support_demo_v9.html is a build artifact that is committed to git, so it
# goes stale the moment a source file is edited without rebuilding. Serving a
# stale bundle is the worst kind of failure here: the page loads, looks fine,
# and silently lacks whatever you just added. So the staleness check is not
# optional and not a warning — it rebuilds.
# =============================================================================
BUNDLE_SOURCES="kt_support_v9.html kb_database.js kt_data.js kt_topology.js
                kt_pipeline.js kt_intake.js kt_record.js kt_rag.js
                ai_agent.js demo_tickets.js"

bundle_stale() {
  [ -f "$DEMO_FILE" ] || return 0            # missing counts as stale
  local bundle_time src_time
  bundle_time=$(stat -c %Y "$DEMO_FILE" 2>/dev/null || echo 0)
  for src in $BUNDLE_SOURCES; do
    [ -f "$src" ] || continue
    src_time=$(stat -c %Y "$src" 2>/dev/null || echo 0)
    if [ "$src_time" -gt "$bundle_time" ]; then
      echo "$src"
      return 0
    fi
  done
  return 1
}

rebuild_bundle() {
  have node || die "the bundle needs rebuilding but node is not installed.
  Install node, or serve the source instead:  ./start.sh dev"
  node build_demo.js v9 || die "build_demo.js failed — not serving a half-built bundle."
}

if [ "$DO_BUILD" -eq 1 ]; then
  echo "Rebuilding $DEMO_FILE ..."
  rebuild_bundle
  echo
elif [ "$PAGE" = "$DEMO_FILE" ] || [ "$PAGE" = "$LANDING_FILE" ]; then
  # The landing page links to the bundle, so it needs a current one too.
  if newer=$(bundle_stale); then
    if [ -f "$DEMO_FILE" ]; then
      echo "  $DEMO_FILE is older than $newer — rebuilding."
    else
      echo "  $DEMO_FILE does not exist yet — building it."
    fi
    rebuild_bundle
    echo
  fi
fi

# =============================================================================
# kt-ai-support — the second system, on its own ports (API 8100, pg 5434).
# Only started by `./start.sh all`, because it is a separate product with its
# own schema, corpus and launcher; bringing it up costs another container and
# another 90 seconds of embedding on a cold store.
# =============================================================================
KT_AI_DIR="${APP_DIR}/kt-ai-support"

start_kt_ai() {
  [ -d "$KT_AI_DIR" ] || { warn "kt-ai-support/ not found — skipping"; return 0; }
  step "kt-ai-support (API 8100, postgres 5434)"

  if curl -fsS "http://127.0.0.1:8100/health" >/dev/null 2>&1; then
    ok "already listening on 8100"
    return 0
  fi

  # Its own launcher owns its migrations, seeding and health checks; calling
  # it is better than duplicating any of that here. --no-open because this
  # script opens the browser once, at the end.
  ( cd "$KT_AI_DIR" && SKIP_PULL="${SKIP_PULL:-1}" bash ./start.sh --no-seed >"${APP_DIR}/kt-ai.log" 2>&1 ) &
  for _ in $(seq 1 90); do
    curl -fsS "http://127.0.0.1:8100/health" >/dev/null 2>&1 && {
      ok "http://127.0.0.1:8100  (docs at /docs)"; return 0; }
    sleep 1
  done
  warn "kt-ai-support did not answer in 90s — see kt-ai.log"
  tail -n 12 "${APP_DIR}/kt-ai.log" 2>/dev/null | sed 's/^/      /'
}

# =============================================================================
# The full stack
# =============================================================================
if [ "$MODE" = "rag" ] || [ "$MODE" = "all" ]; then
  [ -d "$RAG_DIR" ] || die "rag/ not found. This checkout has no backend."
  cat <<'HEAD'

  KATS — bringing up the AI backend
  =================================
  postgres + pgvector · FastAPI retrieval · local LLM via Ollama
  Everything runs on this machine. Nothing leaves it.
HEAD
  ensure_postgres
  ensure_python
  ensure_ollama
  start_api || warn "continuing without the API — the UI will run in local-only mode"
  seed_demo
  [ "$MODE" = "all" ] && start_kt_ai
  step "Backend summary"
  print_rag_health
fi

# =============================================================================
# Serve the UI
# =============================================================================
if [ ! -f "$PAGE" ]; then
  echo "ERROR: $PAGE not found in $(pwd)" >&2
  [ "$PAGE" = "$DEMO_FILE" ] && echo "Hint: run './start.sh --build' to generate it." >&2
  exit 1
fi

if [ "$PAGE" = "$DEV_FILE" ]; then
  for dep in kb_database.js kt_data.js kt_topology.js kt_pipeline.js kt_intake.js \
             kt_record.js kt_rag.js ai_agent.js; do
    [ -f "$dep" ] || die "dev mode needs $dep next to $DEV_FILE"
  done
fi

port_busy() {
  if have ss; then
    ss -ltn 2>/dev/null | grep -q ":$1 "
  else
    ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
    return $(( ! $? ))
  fi
}

START_PORT="$PORT"
while port_busy "$PORT"; do
  PORT=$((PORT + 1))
  [ "$PORT" -gt $((START_PORT + 20)) ] && die "no free port between $START_PORT and $PORT"
done
[ "$PORT" != "$START_PORT" ] && echo "Port $START_PORT busy, using $PORT instead."

URL="http://localhost:$PORT/$PAGE"

if have python3; then
  SERVE=(python3 -m http.server "$PORT" --bind 127.0.0.1)
elif have npx; then
  SERVE=(npx --yes http-server -p "$PORT" -a 127.0.0.1 --silent)
else
  die "need python3 or npx to serve. Alternatively open $(pwd)/$PAGE directly."
fi

open_browser() {
  sleep 1
  if have wslview;                                        then wslview "$URL"
  elif [ -n "${WSL_DISTRO_NAME:-}" ] && have explorer.exe; then
    explorer.exe "$URL" >/dev/null 2>&1 || true   # explorer.exe always exits non-zero
  elif have xdg-open;                                     then xdg-open "$URL" >/dev/null 2>&1
  elif have open;                                         then open "$URL"
  else echo "   (could not auto-open a browser — paste the URL above)"
  fi
}

if [ "$MODE" = "rag" ] || [ "$MODE" = "all" ]; then
cat <<BANNER

  KATS — KT AI Ticket Support, with the RAG backend live
  ======================================================
  UI          : $URL
  RAG API     : http://${API_HOST}:${API_PORT}      (Swagger at /docs)
  PostgreSQL  : 127.0.0.1:${PG_PORT}   db ${POSTGRES_DB:-kats_rag}
  Ollama      : ${OLLAMA_URL}$([ "$MODE" = "all" ] && printf '\n  kt-ai-support: http://127.0.0.1:8100  (Swagger at /docs, pg 5434)')
  Logs        : rag-api.log · ollama.log$([ "$MODE" = "all" ] && printf ' · kt-ai.log')

  Turn it on in the browser (once — it is remembered):
    Support view -> "Ask KARL" tab -> Backend card
      1. tick "Use the RAG backend"
      2. endpoint http://${API_HOST}:${API_PORT}
      3. "Test" should report the models
      4. "Index all local tickets" to load what is already in this browser

  Then try it:
    A. Customer ticket view -> "Fill with demo data" -> scroll to
       "Ticket summary" -> Submit. The confirmation shows what was captured
       and indexes it.
    B. Support view -> §0.0 opens on the SAME summary table, then the details.
    C. "Ask KARL" -> ask what has broken before and what fixed it.
       Every answer opens an Evidence table showing the tickets it read.

  Two evidence types, two questions:
    intake     = someone else reported this
    resolution = someone actually fixed this

  Stop the UI with Ctrl-C. Stop the backend with:  ./start.sh stop

BANNER
else
cat <<BANNER

  OpenStack Cloud - KT Support Ticket (AI Agent PoC)
  --------------------------------------------------
  Serving : $PAGE
  URL     : $URL
  Dir     : $(pwd)

  Pipeline: PRIORITIZE -> CONTAIN -> DEFINE -> NARROW -> TEST -> CONFIRM -> FIX
    "Stop the impact. Narrow the difference. Test one variable.
     Prove the cause. Fix it forever."

  Customer path (the view switch at the top):
    A. "Customer ticket view" -> "Fill with demo data" -> the Ticket summary
       table at the end gathers every answer -> Submit
    B. "Open in support view" -> §0.0 opens on that same summary table

  Demo path:
    1. Load a demo ticket                -> customer + Problem auto-match
    2. The sticky pipeline bar at the top -> 8 stages, the middle three
       bracketed as a LOOP with a pass counter
    3. Clear "Error message" in 5.1      -> PRIORITIZE drops to "1 missing"
    4. Section 0.1 Customer Topology     -> mind map of every OPEN ticket
    5. Section 10.0 "Plan the pipeline"  -> RECURRENCE of PRB-0001

  NOTE: the AI Agent is a MOCK. No model is called.
  For the real thing — PostgreSQL/pgvector retrieval and a local LLM —
  run:  ./start.sh rag

  Ctrl-C to stop.

BANNER
fi

[ "$OPEN_BROWSER" -eq 1 ] && open_browser &

exec "${SERVE[@]}"
