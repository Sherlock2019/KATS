#!/usr/bin/env bash
# =============================================================================
# start.sh — serve the KT Support demo and open it in a browser.
#
#   ./start.sh              chooser page
#   ./start.sh kats         serve kt_support_demo_v9.html (standalone, ship this)
#   ./start.sh v8           serve kt_support_demo.html    (previous release)
#   ./start.sh dev          serve kt_support_v9.html      (multi-file source)
#   ./start.sh --port 9000  use a specific port
#   ./start.sh --no-open    don't launch a browser
#   ./start.sh --build      rebuild the standalone file first, then serve
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

LANDING_FILE="rax_ticket_support_page.html"
DEMO_FILE="kt_support_demo_v9.html"
DEMO_V8_FILE="kt_support_demo.html"
DEV_FILE="kt_support_v9.html"

PAGE="$LANDING_FILE"
PORT=8000
OPEN_BROWSER=1
DO_BUILD=0

while [ $# -gt 0 ]; do
  case "$1" in
    dev|--dev)      PAGE="$DEV_FILE"; shift ;;
    demo|--demo)    PAGE="$DEMO_FILE"; shift ;;
    kats|--kats)    PAGE="$DEMO_FILE"; shift ;;
    v8|--v8)        PAGE="$DEMO_V8_FILE"; shift ;;
    core|--core)    PAGE="core_ticket_rebuilt.html"; shift ;;
    -p|--port)      PORT="${2:-8000}"; shift 2 ;;
    --no-open)      OPEN_BROWSER=0; shift ;;
    -b|--build)     DO_BUILD=1; shift ;;
    -h|--help)      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1  (try --help)" >&2; exit 1 ;;
  esac
done

# --- optional rebuild -------------------------------------------------------
if [ "$DO_BUILD" -eq 1 ]; then
  if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: --build needs node, which is not installed." >&2
    exit 1
  fi
  echo "Rebuilding $DEMO_FILE ..."
  node build_demo.js v9
  echo
fi

# --- sanity check -----------------------------------------------------------
if [ ! -f "$PAGE" ]; then
  echo "ERROR: $PAGE not found in $(pwd)" >&2
  [ "$PAGE" = "$DEMO_FILE" ] && echo "Hint: run './start.sh --build' to generate it." >&2
  exit 1
fi

if [ "$PAGE" = "$DEV_FILE" ]; then
  for dep in kb_database.js kt_data.js kt_topology.js kt_pipeline.js kt_intake.js ai_agent.js; do
    if [ ! -f "$dep" ]; then
      echo "ERROR: dev mode needs $dep next to $DEV_FILE" >&2
      exit 1
    fi
  done
fi

# --- find a free port -------------------------------------------------------
port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -q ":$1 "
  else
    # Fall back to actually trying to bind.
    ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
    return $(( ! $? ))
  fi
}

START_PORT="$PORT"
while port_busy "$PORT"; do
  PORT=$((PORT + 1))
  if [ "$PORT" -gt $((START_PORT + 20)) ]; then
    echo "ERROR: no free port between $START_PORT and $PORT" >&2
    exit 1
  fi
done
[ "$PORT" != "$START_PORT" ] && echo "Port $START_PORT busy, using $PORT instead."

URL="http://localhost:$PORT/$PAGE"

# --- pick a server ----------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
  SERVE=(python3 -m http.server "$PORT" --bind 127.0.0.1)
elif command -v npx >/dev/null 2>&1; then
  SERVE=(npx --yes http-server -p "$PORT" -a 127.0.0.1 --silent)
else
  echo "ERROR: need python3 or npx to serve. Alternatively just open $PAGE directly:" >&2
  echo "       $(pwd)/$PAGE" >&2
  exit 1
fi

# --- browser opener (handles WSL, Linux, macOS) -----------------------------
open_browser() {
  sleep 1
  if command -v wslview >/dev/null 2>&1;        then wslview "$URL"
  elif [ -n "${WSL_DISTRO_NAME:-}" ] && command -v explorer.exe >/dev/null 2>&1; then
    explorer.exe "$URL" >/dev/null 2>&1 || true   # explorer.exe always exits non-zero
  elif command -v xdg-open >/dev/null 2>&1;     then xdg-open "$URL" >/dev/null 2>&1
  elif command -v open >/dev/null 2>&1;         then open "$URL"
  else echo "   (could not auto-open a browser — paste the URL above)"
  fi
}

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
    A. "Customer ticket view" -> "Fill with demo data" -> Submit
       -> ticket number issued, quality scored, known issue surfaced
    B. "Open in support view" -> the same ticket, already in the KT form,
       in the dashboard, in Customer 360 and on the topology map

  Demo path:
    1. Load a demo ticket                -> customer + Problem auto-match
    2. The sticky pipeline bar at the top -> 8 stages, the middle three
       bracketed as a LOOP with a pass counter; every chip's state is
       computed from the fields, and NEXT BEST ACTION sits under it
    3. Clear "Error message" in 5.1      -> PRIORITIZE drops to "1 missing"
       and CONTAIN goes "blocked by PRIORITIZE" (evidence is a gate)
    4. Set severity 3 + blast "specific user" + trend "stable"
                                         -> CONTAIN auto-marks "not required"
    5. Section 0.1 Customer Topology     -> mind map of every OPEN ticket,
       customer > site > infra location > issue; "Tree" view is clickable
    6. "Ticket history" next to Customer -> 3 days / week / month / custom
    7. Section 10.0 "Plan the pipeline"  -> RECURRENCE of PRB-0001; NARROW
       and TEST come back already answered, CONFIRM does not
    8. Section 10.6 Loop log             -> one row per pass around the loop
    9. Tabs: Customer 360 / Root Cause - Problems

  NOTE: the AI Agent is a MOCK. No model is called.

  Ctrl-C to stop.

BANNER

[ "$OPEN_BROWSER" -eq 1 ] && open_browser &

exec "${SERVE[@]}"
