#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "$ROOT_DIR"

if [[ -d "${ROOT_DIR}/.venv" ]]; then
    for site_pkg in "${ROOT_DIR}"/.venv/lib*/python*/site-packages; do
        if [[ -d "$site_pkg" ]]; then
            export PYTHONPATH="${site_pkg}${PYTHONPATH:+:$PYTHONPATH}"
        fi
    done
fi

if [[ -x "${ROOT_DIR}/.venv/bin/pytest" ]]; then
    exec "${ROOT_DIR}/.venv/bin/pytest" tests "$@"
fi

if command -v pytest >/dev/null 2>&1; then
    exec pytest tests "$@"
fi

exec python3 -m pytest tests "$@"
