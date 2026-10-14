#!/bin/zsh
# Engine for testing the UI in a browser: fixed port 8765, token "ui", separate test data.
cd "$(dirname "$0")/.."
export VYUM_DATA="${VYUM_DATA:-/private/tmp/claude-501/-Users-omdhardwivedi-local-chatgpt/360f5bcf-e8a6-4f1e-bb19-4b54ea326560/scratchpad/ui-data2}" VYUM_TOKEN="${VYUM_TOKEN:-ui}"
exec .venv/bin/python -m engine.main --port 8765
