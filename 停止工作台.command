#!/bin/zsh
set -eu
cd "${0:A:h}"
.venv/bin/python scripts/local_workbench.py stop
