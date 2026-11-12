#!/bin/bash
# Celery Worker 启动脚本
#
# 使用方式：
#   ./entrypoints/run_celery_worker.sh
#   CELERY_CONCURRENCY=8 ./entrypoints/run_celery_worker.sh
#   ./entrypoints/run_celery_worker.sh --max-tasks-per-child=1000
#
# 环境变量：
#   CELERY_LOG_LEVEL   日志级别，默认 info
#   CELERY_CONCURRENCY 并发数，默认 4
#   CELERY_QUEUES      显式覆盖订阅队列；未设置或为空时使用 Celery app 的 task_queues

set -e

# 切换到项目根目录
cd "$(dirname "$0")/.."

# 默认配置
LOG_LEVEL=${CELERY_LOG_LEVEL:-info}
CONCURRENCY=${CELERY_CONCURRENCY:-4}
QUEUE_ARGS=()
if [[ -n "${CELERY_QUEUES:-}" ]]; then
    QUEUE_ARGS=(-Q "$CELERY_QUEUES")
fi

# 检查 uv 是否可用。Celery 通过 uv run 在项目环境中启动。
if ! command -v uv &> /dev/null; then
    echo "Error: uv not found. Please install uv first:"
    echo "  https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# 启动 Worker
exec uv run celery -A internal.infra.celery.application:celery_app worker \
    -l "$LOG_LEVEL" \
    -c "$CONCURRENCY" \
    --pool prefork \
    "${QUEUE_ARGS[@]}" \
    "$@"
