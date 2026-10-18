#!/usr/bin/env bash
# Run the test suite in Docker (Home Assistant's test package can't run on Windows).
# Usage: scripts/test.sh [--rebuild] [pytest args]
set -euo pipefail

IMAGE=printlog-ha-test
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if WIN_ROOT="$(cd "$ROOT" && pwd -W 2>/dev/null)"; then :; else WIN_ROOT="$ROOT"; fi

export MSYS_NO_PATHCONV=1

REBUILD=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--rebuild" ]; then REBUILD=1; else ARGS+=("$a"); fi
done

if [ "$REBUILD" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -f "$WIN_ROOT/Dockerfile.test" -t "$IMAGE" "$WIN_ROOT"
fi

docker run --rm -v "$WIN_ROOT:/repo" -w /repo "$IMAGE" python -m pytest "${ARGS[@]}"
