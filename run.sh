#!/bin/sh
# Start DJTools, creating or refreshing .venv first if it needs it.
set -e
cd "$(dirname "$0")"

VENV=.venv
PY="$VENV/bin/python"
STAMP="$VENV/.requirements-cksum"

# Python 3.10+, wherever it lives (Homebrew ARM or Intel, python.org, pyenv…).
find_python() {
    for c in python3.12 python3.13 python3.11 python3.10 python3; do
        p=$(command -v "$c" 2>/dev/null) || continue
        "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null || continue
        echo "$p"
        return 0
    done
    return 1
}

if [ ! -x "$PY" ]; then
    base=$(find_python) || {
        echo "DJTools needs Python 3.10 or newer, and none was found on PATH." >&2
        echo "Install it (brew install python@3.12) and run this again." >&2
        exit 1
    }
    echo "Creating ${VENV} with ${base}..." >&2
    "$base" -m venv "$VENV"
fi

# Reinstall when requirements.txt changes — content, not mtime, since a fresh
# clone's timestamps say nothing.
want=$(cksum requirements.txt)
if [ ! -f "$STAMP" ] || [ "$want" != "$(cat "$STAMP")" ]; then
    echo "Installing dependencies..." >&2
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
    echo "$want" > "$STAMP"
fi

exec "$PY" -m djtools "$@"
