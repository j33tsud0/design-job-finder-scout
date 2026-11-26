#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARCH="$(uname -m)"
NAME="Design Job Finder Scout"

cd "$ROOT"

test "$(uname -s)" = "Darwin"
test -x scout/scout

WORK="$(mktemp -d)"
WORK="$(cd "$WORK" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT

OUT="$ROOT/dist"
STAGE="$WORK/stage"
APP="$STAGE/$NAME.app"
CONTENTS="$APP/Contents"
RESOURCES="$CONTENTS/Resources"
RUNTIME="$RESOURCES/runtime"
APPLICATION="$RESOURCES/application"

mkdir -p \
    "$OUT" \
    "$CONTENTS/MacOS" \
    "$RESOURCES" \
    "$APPLICATION"

echo "[*] Architecture: $ARCH"
echo "[*] Provisioning private Python runtime..."

export UV_PYTHON_INSTALL_DIR="$WORK/python"
export UV_CACHE_DIR="$WORK/cache"

uv python install 3.12

PYTHON="$(uv python find --managed-python 3.12)"

PYTHON_ROOT="$("$PYTHON" -c \
    'import sys; print(sys.base_prefix)')"

ditto "$PYTHON_ROOT" "$RUNTIME"

PY="$RUNTIME/bin/python3"

test -x "$PY"

"$PY" -c 'import sys; print(sys.version)'

SITE="$("$PY" -c \
    'import sysconfig; print(sysconfig.get_path("purelib"))')"

echo "[*] Runtime directory: $RUNTIME"
echo "[*] Python package directory: $SITE"

"$PY" - "$RUNTIME" "$SITE" <<'PATHCHECK'
import sys
from pathlib import Path

runtime = Path(sys.argv[1]).resolve(strict=True)
site = Path(sys.argv[2]).resolve()

print(f"[*] Resolved runtime: {runtime}")
print(f"[*] Resolved package directory: {site}")

if site == runtime or not site.is_relative_to(runtime):
    raise SystemExit(
        "[!] Python package directory genuinely escaped the runtime."
    )

print("[+] Python package directory containment verified")
PATHCHECK

SITE="$("$PY" -c 'import sysconfig; from pathlib import Path; print(Path(sysconfig.get_path("purelib")).resolve())')"

echo "[*] Installing bundled Python dependencies..."

uv pip install \
    --python "$PY" \
    --target "$SITE" \
    -r requirements.txt \
    -c constraints.txt

echo "[*] Copying approved application files..."

git archive HEAD \
    engine \
    assets/JobFinder.icns \
    VERSION \
    LICENSE \
    README.md \
    config.example.yaml \
    portfolio_notes.example.md \
    requirements.txt \
    constraints.txt |
    tar -xf - -C "$APPLICATION"

mkdir -p "$APPLICATION/scout"

install -m 755 \
    scout/scout \
    "$APPLICATION/scout/scout"

cp assets/JobFinder.icns "$RESOURCES/"
cp LICENSE "$RESOURCES/"

echo "[*] Preserving native library license..."

JANSSON_LICENSE="$(find "$(brew --prefix jansson)/" \
    -type f \( -name LICENSE -o -name COPYING \) \
    -print | head -n 1)"

test -n "$JANSSON_LICENSE"

cp "$JANSSON_LICENSE" \
    "$RESOURCES/Jansson-LICENSE.txt"

echo "[*] Creating application launcher..."

cat > "$CONTENTS/MacOS/$NAME" <<'LAUNCHER'
#!/bin/bash
set -euo pipefail

umask 077

HERE="$(cd "$(dirname "$0")" && pwd)"
RESOURCES="$(cd "$HERE/../Resources" && pwd)"

export JOBSEARCH_SCOUT_PACKAGED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1

unset PYTHONHOME PYTHONPATH VIRTUAL_ENV

DATA="$HOME/Library/Application Support/Design Job Finder Scout"

mkdir -p "$DATA/logs"

cd "$DATA"

exec "$RESOURCES/runtime/bin/python3" \
    "$RESOURCES/application/engine/app_server.py" \
    "$@" >> "$DATA/logs/launcher.log" 2>&1
LAUNCHER

chmod 755 "$CONTENTS/MacOS/$NAME"

echo "[*] Creating application metadata..."

python3 - "$CONTENTS/Info.plist" <<'PY'
import plistlib
import sys
from pathlib import Path

name = "Design Job Finder Scout"
version = Path("VERSION").read_text().strip()

info = {
    "CFBundleName": name,
    "CFBundleDisplayName": name,
    "CFBundleIdentifier": "com.productdesignjobfinder.scout",
    "CFBundleExecutable": name,
    "CFBundleIconFile": "JobFinder",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": version,
    "CFBundleVersion": "1",
    "LSMinimumSystemVersion": "15.0",
    "NSHighResolutionCapable": True,
}

with open(sys.argv[1], "wb") as f:
    plistlib.dump(info, f)
PY

plutil -lint "$CONTENTS/Info.plist"

echo "[*] Testing runtime relocation..."

RELOCATED="$WORK/Relocation Test"
mkdir -p "$RELOCATED"

mv "$APP" "$RELOCATED/"

TEST_APP="$RELOCATED/$NAME.app"
TEST_RES="$TEST_APP/Contents/Resources"
TEST_PY="$TEST_RES/runtime/bin/python3"

mkdir -p "$WORK/test-home"

HOME="$WORK/test-home" \
JOBSEARCH_SCOUT_PACKAGED=1 \
PYTHONNOUSERSITE=1 \
PYTHONDONTWRITEBYTECODE=1 \
"$TEST_PY" - "$TEST_RES/application" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])

sys.path.insert(0, str(root / "engine"))

import requests
import yaml
import pandas
import anthropic
import jobspy
import webview
import AppKit
import settings
import app_server
import schedule
import updates

assert settings.PACKAGED
assert not settings.SANDBOX
assert settings.DATA != settings.ROOT
assert app_server.FIRST_PORT == 8805
assert schedule.LABEL == "com.productdesignjobfinder.scout.weekly"

print("[+] Bundled Python imports successful")
print("[+] Native Python extensions loaded")
print("[+] Application relocation successful")
print("[+] Packaged storage isolation verified")
PY

echo "[*] Testing relocated native Scout..."

"$TEST_RES/application/scout/scout" designer \
    > "$WORK/discovery.json"

"$TEST_PY" - "$WORK/discovery.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as f:
    results = json.load(f)

assert isinstance(results, list)

print(f"[+] Live HTTPS discovery returned {len(results)} results")
PY

mv "$TEST_APP" "$STAGE/"

echo "[*] Creating Applications shortcut..."

ln -s /Applications "$STAGE/Applications"

echo "[*] Creating Macintosh disk image..."

DMG="$OUT/Design-Job-Finder-Scout-$ARCH.dmg"

hdiutil create \
    -volname "$NAME" \
    -srcfolder "$STAGE" \
    -format UDZO \
    -ov \
    "$DMG"

echo "[*] Verifying disk image..."

hdiutil verify "$DMG"

(
    cd "$OUT"
    shasum -a 256 "$(basename "$DMG")" \
        > "$(basename "$DMG").sha256"
)

ls -lh "$DMG"

echo
echo "=========================================="
echo "       MACINTOSH DMG BUILD COMPLETE"
echo "=========================================="
