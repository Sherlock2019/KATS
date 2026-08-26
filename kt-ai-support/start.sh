#!/usr/bin/env bash
# =============================================================================
# kt-ai-support — bring up the whole stack.
#
#   ./start.sh                install what is missing, migrate, seed, serve
#   ./start.sh --no-seed      skip the demo corpus
#   ./start.sh --reset        DROP the schema and rebuild from scratch
#   ./start.sh --test         run the test suite instead of serving
#   ./start.sh --evaluate     run the retrieval evaluation instead of serving
#   ./start.sh stop           stop the API and Postgres (keeps the volume)
#   ./start.sh install        install and pull everything, start nothing
#
# Environment (all optional, see .env.example):
#   LLM_MODEL=gemma4:latest       better reasoning, minutes per answer on CPU
#   EMBEDDING_MODEL=...           must match EMBEDDING_DIM
#   SKIP_OLLAMA=1 / SKIP_PULL=1   leave the local models alone
#
# Everything runs on this machine. No cloud AI provider is contacted.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"
APP_DIR="$(pwd)"

VENV="${VENV:-${APP_DIR}/.venv}"
BACKEND="${APP_DIR}/backend"

# Defaults, overridable by .env
POSTGRES_PORT="${POSTGRES_PORT:-5434}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8100}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
LLM_MODEL="${LLM_MODEL:-phi3}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-embeddinggemma}"
EMBEDDING_DIM="${EMBEDDING_DIM:-768}"
SKIP_OLLAMA="${SKIP_OLLAMA:-0}"
SKIP_PULL="${SKIP_PULL:-0}"

if [ -f "${APP_DIR}/.env" ]; then
  set -a; . "${APP_DIR}/.env"; set +a
fi

export DATABASE_URL="${DATABASE_URL:-postgresql://${POSTGRES_USER:-kt}:${POSTGRES_PASSWORD:-kt_password}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB:-kt_ai_support}}"
export EMBEDDING_MODEL EMBEDDING_DIM LLM_MODEL OLLAMA_URL API_HOST API_PORT
export PYTHONPATH="${BACKEND}:${APP_DIR}"

MODE="serve"; SEED=1; RESET=0
while [ $# -gt 0 ]; do
  case "$1" in
    stop)         MODE="stop"; shift ;;
    install)      MODE="install"; shift ;;
    --test)       MODE="test"; shift ;;
    --evaluate)   MODE="evaluate"; shift ;;
    --no-seed)    SEED=0; shift ;;
    --reset)      RESET=1; shift ;;
    -h|--help)    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1  (try --help)" >&2; exit 1 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
step() { printf '\n  \033[1m%s\033[0m\n' "$*"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\n  \033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "${APP_DIR}/docker-compose.yml" "$@"
  else
    docker-compose -f "${APP_DIR}/docker-compose.yml" "$@"
  fi
}

ensure_postgres() {
  step "PostgreSQL + pgvector (port ${POSTGRES_PORT})"
  have docker || die "docker not found."
  docker info >/dev/null 2>&1 || { sudo service docker start >/dev/null 2>&1 || true; sleep 3; }
  docker info >/dev/null 2>&1 || die "Docker daemon unreachable. Try: sudo service docker start"

  compose up -d >/dev/null 2>&1
  for _ in $(seq 1 60); do
    compose exec -T postgres pg_isready -U "${POSTGRES_USER:-kt}" >/dev/null 2>&1 && break
    sleep 1
  done
  compose exec -T postgres pg_isready -U "${POSTGRES_USER:-kt}" >/dev/null 2>&1 \
    || die "Postgres did not start. compose logs postgres"
  ok "postgres ready"
}

ensure_python() {
  step "Python environment"
  have python3 || die "python3 not found."
  [ -d "$VENV" ] || python3 -m venv "$VENV"

  local req="${BACKEND}/requirements.txt" stamp="${VENV}/.requirements.sha" now=""
  have sha256sum && now="$(sha256sum "$req" | awk '{print $1}')"
  if [ -n "$now" ] && [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$now" ]; then
    ok "packages already current"
  else
    "${VENV}/bin/pip" install --quiet --upgrade pip
    "${VENV}/bin/pip" install --quiet -r "$req"
    [ -n "$now" ] && printf '%s' "$now" > "$stamp"
    ok "packages installed"
  fi
}

ensure_ollama() {
  [ "$SKIP_OLLAMA" = "1" ] && { say "skipping ollama"; return 0; }
  step "Local models"

  if ! have ollama; then
    warn "ollama not installed — retrieval works, /api/ai answers from the database only."
    say "Install: curl -fsSL https://ollama.com/install.sh | sh"
    return 0
  fi

  if ! curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    nohup ollama serve >"${APP_DIR}/ollama.log" 2>&1 &
    for _ in $(seq 1 30); do
      curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1 && break; sleep 1
    done
  fi
  curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1 \
    || { warn "ollama not answering — see ollama.log"; return 0; }
  ok "ollama up"

  [ "$SKIP_PULL" = "1" ] && return 0
  local installed; installed="$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')"
  for pair in "${LLM_MODEL}:generation" "${EMBEDDING_MODEL}:retrieval embeddings"; do
    local model="${pair%%:*}" why="${pair#*:}"
    [ "$model" = "${LLM_MODEL}" ] && model="${LLM_MODEL}"
    if printf '%s\n' "$installed" | grep -q "^${model%%:*}"; then
      ok "${model} present"
    else
      say "pulling ${model} (${why}) — first run only"
      ollama pull "$model" || warn "could not pull ${model}"
    fi
  done
}

migrate() {
  step "Schema"
  cd "$BACKEND"
  if [ "$RESET" = "1" ]; then
    "${VENV}/bin/python" -m migrations.run --reset
  else
    "${VENV}/bin/python" -m migrations.run
  fi
  cd "$APP_DIR"
}

seed() {
  [ "$SEED" = "1" ] || { say "skipping demo corpus"; return 0; }
  step "Demo corpus"
  local count
  count="$("${VENV}/bin/python" - <<'PY' 2>/dev/null || echo 0
import os, psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    print(c.execute("SELECT COUNT(*) FROM support_tickets").fetchone()[0])
PY
)"
  if [ "${count:-0}" -gt 0 ] && [ "$RESET" != "1" ]; then
    ok "${count} tickets already loaded"
    return 0
  fi
  "${VENV}/bin/python" -m scripts.seed_demo_cases | tail -4
}

start_api() {
  step "API (port ${API_PORT})"
  if curl -fsS "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1; then
    ok "already listening on ${API_PORT}"
    return 0
  fi
  nohup "${VENV}/bin/uvicorn" app.main:app \
      --host "$API_HOST" --port "$API_PORT" --app-dir "$BACKEND" \
      >"${APP_DIR}/api.log" 2>&1 &
  echo $! > "${APP_DIR}/api.pid"
  for _ in $(seq 1 45); do
    curl -fsS "http://${API_HOST}:${API_PORT}/health" >/dev/null 2>&1 && {
      ok "http://${API_HOST}:${API_PORT}  (docs at /docs)"; return 0; }
    sleep 1
  done
  warn "API did not answer. Last lines of api.log:"
  tail -n 20 "${APP_DIR}/api.log" | sed 's/^/      /'
  return 1
}

case "$MODE" in
  stop)
    step "Stopping"
    [ -f "${APP_DIR}/api.pid" ] && { kill "$(cat "${APP_DIR}/api.pid")" 2>/dev/null && ok "API stopped"; rm -f "${APP_DIR}/api.pid"; }
    have docker && compose down >/dev/null 2>&1 && ok "postgres stopped (volume kept)"
    echo; say "Wipe the data with: docker compose -f kt-ai-support/docker-compose.yml down -v"; echo
    exit 0 ;;
  install)
    ensure_postgres; ensure_python; ensure_ollama
    echo; ok "Ready. Start with: ./start.sh"; echo; exit 0 ;;
  test)
    ensure_postgres; ensure_python; migrate; seed
    step "Test suite"
    cd "$BACKEND"
    exec "${VENV}/bin/python" -m pytest tests/ -v -p no:warnings ;;
  evaluate)
    ensure_postgres; ensure_python; migrate; seed
    step "Retrieval evaluation"
    exec "${VENV}/bin/python" -m scripts.evaluate_retrieval --verbose ;;
esac

cat <<'HEAD'

  KT AI Support
  =============
  Kepner-Tregoe troubleshooting with PostgreSQL as the knowledge model
  and pgvector + a local LLM layered on top. Nothing leaves this machine.
HEAD

ensure_postgres
ensure_python
ensure_ollama
migrate
seed
start_api || warn "continuing without the API"

cat <<BANNER

  ------------------------------------------------------------------
  API        http://${API_HOST}:${API_PORT}
  Swagger    http://${API_HOST}:${API_PORT}/docs
  Health     http://${API_HOST}:${API_PORT}/health
  Postgres   127.0.0.1:${POSTGRES_PORT}
  Logs       api.log · ollama.log

  Try it:

    # what has broken like this before, and what fixed it
    curl -s localhost:${API_PORT}/api/rag/search -H 'content-type: application/json' \\
      -d '{"query":"authentication returns 404 could not find token","top_k":5}' | jq

    # the RAG inspector — every score term, per result
    curl -s localhost:${API_PORT}/api/rag/inspect -H 'content-type: application/json' \\
      -d '{"query":"pods crash looping after node upgrade","top_k":5}' | jq '.rows'

    # prove IS/IS-NOT matching earns its place
    ./start.sh --evaluate

    # the acceptance scenario and the guardrails
    ./start.sh --test

  Stop with:  ./start.sh stop

BANNER
