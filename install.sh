#!/usr/bin/env bash
# geolens - macOS / Linux installer. Run:  bash install.sh
set -euo pipefail
cd "$(dirname "$0")"
for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then exec "$PY" install.py "$@"; fi
done
echo "No python3 found on PATH."
exit 1
