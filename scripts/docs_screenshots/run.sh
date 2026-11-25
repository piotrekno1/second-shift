#!/bin/sh
# Regenerate the dashboard screenshots in docs/screenshots.
#
#   scripts/docs_screenshots/run.sh [NAME]
#
# Builds the dashboard from this working tree, seeds a throwaway SQLite
# database with the Acme workspace from seed.py, serves it, takes every shot in
# shots.json (or those whose name contains NAME) in headless Chrome, and saves
# them as 256-colour PNGs. Set SKIP_BUILD=1 to reuse the last dashboard build,
# CHROME to the browser binary, and NODE to another Node 22 or later. Under WSL,
# NODE can be Windows' node.exe, which drives Windows' Chrome.
set -eu
cd "$(dirname "$0")/../.."

HERE=scripts/docs_screenshots
PORT=${PORT:-8767}
NODE=${NODE:-node}
DATA=$(mktemp -d)
export SECONDSHIFT_ENV=dev
export SECONDSHIFT_DATA_DIR="$DATA"
export SECONDSHIFT_DATABASE_URL="sqlite:///$DATA/db.sqlite3"
export SECONDSHIFT_PUBLIC_URL=https://secondshift.acme.example
export SECONDSHIFT_RUNNER_RUNTIME=runsc

case "$NODE" in
*.exe) native() { wslpath -w "$1"; } ;;
*) native() { echo "$1"; } ;;
esac

[ -n "${SKIP_BUILD:-}" ] || npm --prefix dashboard run build
uv run python manage.py migrate --noinput >/dev/null
uv run python manage.py shell <"$HERE/seed.py"

uv run python manage.py runserver "127.0.0.1:$PORT" --noreload >"$DATA/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$DATA"' EXIT
tries=0
until curl -sf -o /dev/null "http://127.0.0.1:$PORT/"; do
  tries=$((tries + 1))
  [ "$tries" -lt 60 ] || { cat "$DATA/server.log"; exit 1; }
  sleep 1
done

TOKEN=$(uv run python manage.py shell -c '
from django.contrib.auth import get_user_model
from secondshift.core.signin import make_token
print(make_token(get_user_model().objects.get(username="shots")))' | tail -n 1)

status=0
"$NODE" "$(native "$HERE/shoot.mjs")" "$(native "$HERE/shots.json")" \
  "http://127.0.0.1:$PORT" "/sign-in/$TOKEN/" "$(native "$DATA/raw")" "${1:-}" ||
  status=$?
uv run --with pillow python "$HERE/quantize.py" "$DATA/raw" docs/screenshots
exit "$status"
