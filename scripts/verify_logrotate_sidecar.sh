#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
COMPOSE_FILE="$REPO_ROOT/compose.prod.yaml"

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

[ "$#" -eq 1 ] || die "usage: $0 <explicit-isolated-work-directory>"
[ "$(uname -s)" = "Linux" ] || die "this acceptance test must run on Linux"

require_command curl
require_command docker
require_command find
require_command grep
require_command mktemp
require_command stat

docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
docker info >/dev/null 2>&1 || die "Docker Engine is not available"

mkdir -p -- "$1"
WORK_ROOT=$(CDPATH= cd -- "$1" && pwd -P)
case "$WORK_ROOT" in
    / | /var | /var/log | /var/log/agent-backend)
        die "refusing to use a production or broad system directory: $WORK_ROOT"
        ;;
esac

WORK_DIR=$(mktemp -d "$WORK_ROOT/agent-backend-logrotate.XXXXXX")
LOG_DIR="$WORK_DIR/logs"
SECRETS_FILE="$WORK_DIR/.secrets"
COMPOSE_ENV_FILE="$WORK_DIR/compose.env"
PROJECT_NAME="agent-backend-logrotate-verify-$$"
API_IMAGE="$PROJECT_NAME-api:verify"
LOGROTATE_IMAGE="$PROJECT_NAME-logrotate:verify"
API_PORT=${LOGROTATE_VERIFY_API_PORT:-18000}
API_WORKERS=${LOGROTATE_VERIFY_API_WORKERS:-3}
ALPINE_REPOSITORY=${LOGROTATE_ALPINE_REPOSITORY:-https://dl-cdn.alpinelinux.org/alpine/v3.22}
STARTED=false

mkdir -p -- "$LOG_DIR"
cp "$REPO_ROOT/configs/.secrets.example" "$SECRETS_FILE"
sed -i 's/^APP_ENV=.*/APP_ENV=prod/' "$SECRETS_FILE"
chmod 0600 "$SECRETS_FILE"

cat >"$COMPOSE_ENV_FILE" <<EOF
COMPOSE_PROJECT_NAME=$PROJECT_NAME
AGENT_BACKEND_IMAGE=$API_IMAGE
LOGROTATE_IMAGE=$LOGROTATE_IMAGE
LOGROTATE_ALPINE_REPOSITORY=$ALPINE_REPOSITORY
AGENT_BACKEND_LOG_DIR=$LOG_DIR
API_WORKERS=$API_WORKERS
API_PORT=$API_PORT
API_CPUS=2.0
API_MEMORY_LIMIT=1g
LOGROTATE_CPUS=0.25
LOGROTATE_MEMORY_LIMIT=64m
DOCKER_LOG_MAX_SIZE=10m
DOCKER_LOG_MAX_FILE=3
APP_CONFIG_ENV=prod
APP_ENV_FILE=$REPO_ROOT/configs/.env.prod
APP_SECRETS_FILE=$SECRETS_FILE
EOF

compose() {
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

cleanup() {
    if [ "$STARTED" = true ]; then
        compose down --remove-orphans --volumes >/dev/null 2>&1 || true
    fi
}

on_signal() {
    trap - EXIT HUP INT TERM
    cleanup
    exit 130
}

trap cleanup EXIT
trap on_signal HUP INT TERM

wait_for_api() {
    attempt=0
    while [ "$attempt" -lt 60 ]; do
        if curl --max-time 2 -sS -o /dev/null "http://127.0.0.1:$API_PORT/v1/public/logrotate-probe"; then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    compose logs --no-color api >&2 || true
    die "API did not become ready within 60 seconds"
}

emit_requests() {
    marker=$1
    request_count=${2:-1}
    index=1
    request_pids=""
    while [ "$index" -le "$request_count" ]; do
        curl --max-time 10 -sS -o /dev/null \
            "http://127.0.0.1:$API_PORT/v1/public/logrotate-probe?marker=$marker&request=$index" &
        request_pids="$request_pids $!"
        index=$((index + 1))
    done
    for request_pid in $request_pids; do
        wait "$request_pid" || die "request failed while emitting marker: $marker"
    done
}

wait_for_marker() {
    marker=$1
    attempt=0
    while [ "$attempt" -lt 50 ]; do
        if grep -q "$marker" "$LOG_DIR/app.log" 2>/dev/null; then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    die "marker was not flushed to app.log: $marker"
}

validate_json_lines() {
    compose exec -T api python -c \
        'import json, pathlib; lines = [line for line in pathlib.Path("/var/log/agent-backend/app.log").read_text().splitlines() if line]; assert lines; [json.loads(line) for line in lines]'
}

force_rotate() {
    compose exec -T logrotate /usr/sbin/logrotate --force \
        --state /var/lib/logrotate/status /etc/logrotate.d/agent-backend
}

archive_count() {
    find "$LOG_DIR" -maxdepth 1 -type f -name 'app.*.log*' | wc -l | tr -d ' '
}

printf 'Acceptance workspace: %s\n' "$WORK_DIR"
compose config --quiet
compose build

API_UID=$(docker run --rm --entrypoint id "$API_IMAGE" -u)
API_GID=$(docker run --rm --entrypoint id "$API_IMAGE" -g)
docker run --rm --user 0:0 --entrypoint sh -v "$LOG_DIR:/logs" "$API_IMAGE" \
    -c "chown $API_UID:$API_GID /logs && chmod 0750 /logs"

STARTED=true
compose up -d
wait_for_api

WORKER_COUNT=$(compose logs --no-color api | awk '/Started server process/ {count++} END {print count + 0}')
[ "$WORKER_COUNT" -eq "$API_WORKERS" ] || \
    die "expected $API_WORKERS Uvicorn workers, observed $WORKER_COUNT"

BEFORE_MARKER="before-rotation-$$"
emit_requests "$BEFORE_MARKER" 40
wait_for_marker "$BEFORE_MARKER"
validate_json_lines

ACTIVE_INODE=$(stat -c %i "$LOG_DIR/app.log")
force_rotate
[ "$(stat -c %i "$LOG_DIR/app.log")" = "$ACTIVE_INODE" ] || \
    die "copytruncate changed the active app.log inode"
[ "$(archive_count)" -eq 1 ] || die "first rotation did not create exactly one archive"

AFTER_MARKER="after-rotation-$$"
emit_requests "$AFTER_MARKER" 40
wait_for_marker "$AFTER_MARKER"
validate_json_lines

sleep 1
force_rotate
find "$LOG_DIR" -maxdepth 1 -type f -name 'app.*.log.gz' | grep -q . || \
    die "delaycompress did not compress the previous archive on the second rotation"
compose exec -T logrotate sh -c \
    'set -- /var/log/agent-backend/app.*.log.gz; [ -e "$1" ]; gzip -t "$@"'

rotation=3
while [ "$rotation" -le 31 ]; do
    emit_requests "retention-$rotation-$$" 1
    sleep 1
    force_rotate
    rotation=$((rotation + 1))
done
[ "$(archive_count)" -eq 30 ] || die "rotate 30 retention was not enforced"

compose exec -T logrotate test -s /var/lib/logrotate/status || die "state file is missing"
ARCHIVES_BEFORE_RESTART=$(archive_count)
compose restart logrotate
compose exec -T logrotate test -s /var/lib/logrotate/status || die "state file was lost after restart"
compose exec -T logrotate /usr/sbin/logrotate \
    --state /var/lib/logrotate/status /etc/logrotate.d/agent-backend
[ "$(archive_count)" -eq "$ARCHIVES_BEFORE_RESTART" ] || \
    die "sidecar restart caused an unexpected duplicate rotation"

ARCHIVES_BEFORE_RECREATE=$(archive_count)
compose up -d --force-recreate api
wait_for_api
[ "$(archive_count)" -eq "$ARCHIVES_BEFORE_RECREATE" ] || \
    die "archives changed during API recreation"

compose stop logrotate
curl --max-time 10 -sS -o /dev/null \
    "http://127.0.0.1:$API_PORT/v1/public/logrotate-probe?marker=sidecar-stopped-$$"
compose ps --status running --services | grep -qx api || die "API stopped with the sidecar"

printf 'PASS: logrotate sidecar acceptance completed.\n'
printf 'Logs and archives remain at: %s\n' "$LOG_DIR"
