#!/bin/bash
# Double-click to open Metroidvania Maker on your last-opened project
# (else the first projects/<name>/; or pass --project DIR from a terminal).
cd "$(dirname "$0")"
exec ./.venv/bin/python -m maker "$@"
