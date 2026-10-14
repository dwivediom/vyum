#!/bin/zsh
# Starts the local SearXNG that Vyum uses for web search (http://127.0.0.1:8888).
# One-time install:  git clone --depth 1 https://github.com/searxng/searxng.git ../searxng
#                    cd ../searxng && uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.txt -r requirements-server.txt
set -e
HERE=${0:A:h}
SEARXNG=${SEARXNG_DIR:-$HERE/../../searxng}
export SEARXNG_SETTINGS_PATH=$HERE/searxng/settings.yml
export SEARXNG_SECRET=$(openssl rand -hex 32)
cd $SEARXNG
exec .venv/bin/python -m searx.webapp
