#!/bin/zsh
# Builds Vyum-<version>-mac-arm64.dmg (Apple Silicon, macOS 14+). Unsigned for now: ad-hoc signature only.
#   scripts/package/build_mac.sh            (needs: GenOffice built at ../genoffice, uv, node, Rust)
set -e
cd "${0:A:h}/../.."
ROOT=$PWD
VERSION=$(python3 -c "import json;print(json.load(open('app/src-tauri/tauri.conf.json'))['version'])")

[[ -d build/resources/runtime ]] || python3 scripts/package/assemble.py
python3 scripts/package/assemble.py engine            # always ship the current engine sources
node pdfhost/build.mjs ../genoffice && python3 scripts/package/assemble.py pdfhost

echo "== app"
(cd app && npm ci --no-audit --no-fund && npx tauri build --bundles app)
APP=app/src-tauri/target/release/bundle/macos/Vyum.app

echo "== resources into the app (ditto keeps the symlinks inside GenOffice.app and Electron.app)"
for d in runtime engine genoffice pdfhost licenses; do
  rm -rf "$APP/Contents/Resources/$d"
  ditto "build/resources/$d" "$APP/Contents/Resources/$d"
done

echo "== ad-hoc signature (Apple Silicon runs only signed code; a Developer ID replaces this later)"
codesign --force --deep --sign - "$APP"

echo "== dmg"
OUT=build/Vyum-$VERSION-mac-arm64.dmg
STAGE=$(mktemp -d)
ditto "$APP" "$STAGE/Vyum.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
hdiutil create -volname "Vyum $VERSION" -srcfolder "$STAGE" -fs APFS -format ULMO "$OUT"
rm -rf "$STAGE"
shasum -a 256 "$OUT" | tee "$OUT.sha256"
ls -lh "$OUT"
