#!/bin/sh
# Start the IF Player.
#
# Runs the venv's own if-player, so this works from any working directory.
# `python if_player/main.py` does not: Python puts that file's directory on
# sys.path instead of the repo root, so `import if_player` finds nothing.
set -eu

exec /Users/benjamin/venvs/if-player-s5Eq2ROt-py3.14/bin/if-player "$@"
