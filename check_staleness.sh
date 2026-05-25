#!/usr/bin/env bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ ! -d ".venv" ]; then
    echo "Error: Virtual environment (.venv) not found in $DIR"
    exit 1
fi

.venv/bin/python3 scripts/check_staleness.py "$@"
