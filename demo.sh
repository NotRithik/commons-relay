#!/bin/sh
# Run the standalone LEZ demo after scripts/prepare-local.sh has prepared it.
# --help only displays usage; all other options go to scripts/demo-local.py.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.12 or newer is required; see docs/DEPLOYMENT.md.' >&2
    exit 127
fi
# Never accept mock proof mode from the calling shell.
export RISC0_DEV_MODE=0
export RISC0_PROVER=ipc
export RISC0_EXECUTOR=ipc
exec python3 "$ROOT/scripts/demo-local.py" "$@"
