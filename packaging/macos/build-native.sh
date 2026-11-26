#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This build script requires macOS."
    exit 1
fi

JANSSON="$(brew --prefix jansson)"
SDK="$(xcrun --sdk macosx --show-sdk-path)"

echo "[*] Building portable native Scout..."
echo "[*] Architecture: $(uname -m)"
echo "[*] SDK: $SDK"

test -f "$JANSSON/lib/libjansson.a"

xcrun --sdk macosx clang \
    -std=c17 \
    -O2 \
    -Wall \
    -Wextra \
    -Wpedantic \
    -Werror \
    -isysroot "$SDK" \
    -I"$JANSSON/include" \
    scout/src/scout.c \
    "$JANSSON/lib/libjansson.a" \
    -lcurl \
    -o scout/scout

echo "[*] Inspecting executable..."
file scout/scout
otool -L scout/scout

echo "[*] Checking for external library dependencies..."

if otool -L scout/scout | tail -n +2 |
    grep -Ev '^[[:space:]]+(/usr/lib/|/System/Library/)'
then
    echo "[!] Non-system dynamic dependency detected."
    exit 1
fi

echo "[*] Running native tests..."
make -C scout test

echo "[*] Portable native build successful."
