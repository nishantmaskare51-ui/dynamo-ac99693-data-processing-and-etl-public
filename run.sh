#!/usr/bin/env bash
# Minimal wrapper entrypoint expected by the verifier.
# - Ensures /app/output exists
# - Invokes the reference implementation (python3 solution/solve_core.py)
# - Exits with the implementation's exit code

set -euo pipefail

OUT_DIR="/app/output"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure output directory exists and is writeable
mkdir -p "$OUT_DIR"
chmod 0777 "$OUT_DIR" || true

# Ensure Python is available; prefer python3
PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
  PY=python
fi

# Run the core solution. We run from repository root so relative imports (if any)
# will behave as on the evaluator.
"$PY" "$SCRIPT_DIR/solution/solve_core.py"
EXIT_CODE=$?

# Exit with the underlying script's exit code so CI sees success/failure.
exit $EXIT_CODE
