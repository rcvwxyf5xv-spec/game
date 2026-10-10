#!/bin/bash
# Double-click to open Metroidvania Maker on the demo project
# (or pass --project DIR when run from a terminal).
cd "$(dirname "$0")"
exec ./.venv/bin/python -m maker "$@"
