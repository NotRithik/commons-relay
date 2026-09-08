#!/bin/sh
# Build the three Relay modules from already verified local SDK inputs.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
: "${LOGOS_DEPS_DIR:?Set LOGOS_DEPS_DIR to the pinned official source checkouts}"
: "${QT_PREFIX:?Set QT_PREFIX to a compatible Qt 6.9 SDK}"
OUT=${RELAY_NATIVE_BUILD_DIR:-"$ROOT/out/native"}
PREFIX=${RELAY_NATIVE_INSTALL_DIR:-"$ROOT/out/modules"}
JOBS=${COMMONS_BUILD_JOBS:-2}
case "$JOBS" in ''|*[!0-9]*) echo 'Build jobs must be 1..16' >&2; exit 2;; esac
[ "$JOBS" -ge 1 ] && [ "$JOBS" -le 16 ] || { echo 'Build jobs must be 1..16' >&2; exit 2; }
cmake -S "$LOGOS_DEPS_DIR/logos-view-module/view-generator" -B "$OUT/generator" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$QT_PREFIX"
cmake --build "$OUT/generator" --parallel "$JOBS"
cmake -S "$ROOT/native" -B "$OUT/plugin" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$QT_PREFIX" -DLOGOS_DEPS_DIR="$LOGOS_DEPS_DIR" \
  -DLOGOS_VIEW_GENERATOR="$OUT/generator/logos-view-generator" \
  -DCMAKE_INSTALL_PREFIX="$PREFIX"
cmake --build "$OUT/plugin" --parallel "$JOBS"
cmake --install "$OUT/plugin" --prefix "$PREFIX"
printf 'Built Relay modules at %s. Real Basecamp acceptance is a separate check.\n' "$PREFIX"
