#!/bin/sh
# Prepare the pinned local LEZ sequencer + Commons Relay wallet proof stack.
# Fetch and build are separate so the build phase can run with outbound network denied.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PHASE=${1:-all}
case "$PHASE" in fetch|build|all) ;; *) echo 'usage: prepare-local.sh [fetch|build|all]' >&2; exit 2;; esac
OUT=${COMMONS_RELAY_LOCAL_OUT:-"$ROOT/out/local"}
OUT=$(python3 -c 'import pathlib,sys; root=pathlib.Path(sys.argv[1]).resolve(); out=pathlib.Path(sys.argv[2]).resolve(); assert out != root and out.is_relative_to(root), "COMMONS_RELAY_LOCAL_OUT must stay inside this checkout"; print(out)' "$ROOT" "$OUT")
mkdir -p "$OUT"
umask 077
mkdir -p "$OUT/home" "$OUT/tmp" "$OUT/cargo" "$OUT/host" "$OUT/wallet"
export HOME="$OUT/home" TMPDIR="$OUT/tmp/" CARGO_HOME="$OUT/cargo"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 CARGO_NET_GIT_FETCH_WITH_CLI=true
export CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"
export RISC0_DEV_MODE=0 RISC0_PROVER=ipc RISC0_EXECUTOR=ipc
PIN=47eba256479f6f785acbd138834340703cd03401
LEZ="$OUT/lez"

if [ "$PHASE" = fetch ] || [ "$PHASE" = all ]; then
    python3 "$ROOT/scripts/fetch-proof-deps.py" "$OUT/proof-deps"
    if [ ! -d "$LEZ/.git" ]; then
        mkdir -p "$LEZ"
        git -c core.hooksPath=/dev/null -C "$LEZ" init -q
        git -C "$LEZ" remote add origin https://github.com/logos-blockchain/logos-execution-zone.git
        git -c core.hooksPath=/dev/null -C "$LEZ" fetch --depth 1 origin "$PIN"
        git -c core.hooksPath=/dev/null -C "$LEZ" checkout --detach -q FETCH_HEAD
    fi
    test "$(git -C "$LEZ" rev-parse HEAD)" = "$PIN"
    cargo +1.94.0 fetch --locked --manifest-path "$LEZ/Cargo.toml"
    cargo +1.94.0 fetch --locked --manifest-path "$ROOT/wallet/Cargo.toml"
fi
if [ "$PHASE" = fetch ]; then
    echo 'Fetch complete. The build phase can run with outbound networking denied.'
    exit 0
fi

PATHS="$OUT/proof-deps/paths.json"
test -f "$PATHS" || { echo 'Missing verified proof dependencies; run fetch first' >&2; exit 1; }
test "$(git -C "$LEZ" rev-parse HEAD)" = "$PIN"
get_path() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$PATHS" "$1"; }
LBC_ROOT_DIR=$(get_path lbc_root); export LBC_ROOT_DIR
RAPIDSNARK_LIB_DIR=$(get_path rapidsnark_lib); export RAPIDSNARK_LIB_DIR
RISC0_SERVER_PATH=$(get_path r0vm); export RISC0_SERVER_PATH
export CARGO_NET_OFFLINE=true
case "$(uname -s)" in
 Darwin) export DYLD_LIBRARY_PATH="$RAPIDSNARK_LIB_DIR${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}" ;;
 Linux) export LD_LIBRARY_PATH="$RAPIDSNARK_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
esac

CARGO_TARGET_DIR="$OUT/host" cargo +1.94.0 build --locked --offline --manifest-path "$LEZ/Cargo.toml" --features standalone -p sequencer_service
python3 "$ROOT/scripts/build-rust.py" host --manifest "$ROOT/wallet/Cargo.toml" --target-dir "$OUT/wallet"

test -x "$OUT/host/debug/sequencer_service"
test -x "$OUT/wallet/debug/commons-relay-wallet"
echo 'Pinned local sequencer and Relay wallet compiled. No node or transaction was started.'
