#!/bin/sh
# 容器 API 启动入口；额外参数原样传给 Uvicorn。
set -eu

cd "$(dirname "$0")/.."

exec python -m uvicorn entrypoints.api:app \
  --host 0.0.0.0 \
  --port 8000 \
  --loop uvloop \
  --http httptools \
  --log-config entrypoints/uvicorn_logging.json \
  --access-log \
  "$@"
