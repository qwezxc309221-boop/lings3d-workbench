#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/scripts/environment.sh"
cd "$REPO_DIR"
export PYTHONDONTWRITEBYTECODE=1
"$LINGS_PYTHON" scripts/validate_repository.py
node --check workbench/static/app.js
while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find workbench docker scripts third_party -type f -name '*.sh' -print0)
"$LINGS_PYTHON" -m unittest discover -s workbench/tests -v
