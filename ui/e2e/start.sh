#!/usr/bin/env bash
# Prepares a throwaway swarm project, seeds it, and runs the real server on :7799 (used by playwright's webServer).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
export SWARM_HOME="$(mktemp -d)/proj"; mkdir -p "$SWARM_HOME/.swarm"; echo "$SWARM_HOME" > /tmp/swarm-e2e-home
cd "$SWARM_HOME" && git init -q -b main && git config user.email t@t && git config user.name t && echo x > f && git add . && git commit -qm init
printf 'e2e-token' > .swarm/token
S() { uv run --quiet --project "$ROOT" swarm "$@"; }
S init "e2e swarm: build a tiny thing" >/dev/null
S worker define api --goal "build the api" --owns src/api --model haiku >/dev/null
S worker define ui --goal "build the ui" --owns src/ui --model haiku >/dev/null
S post coordinator plan "split into api and ui" >/dev/null
S post api claim "taking the api" >/dev/null
S post ui blocked "need the api shape first" >/dev/null
export SWARM_CLAUDE_BIN="$HERE/fake-claude.py" PYTHONPATH="$ROOT"
exec uv run --quiet --project "$ROOT" swarm serve --port 7799
