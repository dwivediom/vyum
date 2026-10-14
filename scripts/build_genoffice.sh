#!/bin/zsh
# Builds GenOffice from source for Vyum: no analytics and no auto-updater (neither is baked into source builds).
# Source: https://github.com/genspark-ai/genoffice (Apache-2.0; the ee/ folder is not used).
set -e
GO=${GENOFFICE_DIR:-${0:A:h}/../../genoffice}
cd $GO
unset GENOFFICE_UPDATE_URL GENOFFICE_GA4_MEASUREMENT_ID GENOFFICE_GA4_API_SECRET
echo "== npm ci"; npm ci --no-audit --no-fund
echo "== xlsx sidecar (Rust)"; npm run native:build -w @genoffice/sheets
echo "== third-party notices"; npm run notices
echo "== build all apps + cli"; npm run build:all
echo "== package (unsigned, arm64, folder only)"
cd apps/shell && CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder --mac dir --arm64 --publish never
echo "== done"; ls -d $GO/apps/shell/release/mac-arm64/GenOffice.app
