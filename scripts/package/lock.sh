#!/bin/sh
# Pins the app's packages for each platform to the versions in the tested dev environment (.venv).
set -e
cd "$(dirname "$0")"
uv pip freeze --python ../../.venv/bin/python > tested.txt
for target in aarch64-apple-darwin:mac x86_64-pc-windows-msvc:windows; do
  MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile requirements-app.txt -c tested.txt --python-version 3.12 --python-platform "${target%%:*}" \
    --no-header --no-annotate -o "requirements-${target##*:}.lock" -q
done
rm tested.txt
