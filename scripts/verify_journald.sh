#!/bin/sh
# 在获准的本机 Linux Docker 测试环境验证 journald；不修改宿主机日志策略。
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

[ "$#" -eq 1 ] || die "usage: $0 <explicit-isolated-work-directory>"
[ "$(uname -s)" = Linux ] || die "requires Linux with host journald"
for command_name in docker journalctl systemctl curl python3 mktemp; do
    require_command "$command_name"
done
systemctl is-active --quiet systemd-journald || die "journald is not active"
[ -d /var/log/journal ] || die "persistent /var/log/journal is required"
docker compose version >/dev/null
docker info >/dev/null
DOCKER_ENDPOINT=${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}
case "$DOCKER_ENDPOINT" in unix://*) ;; *) die "requires local Docker Engine, not a remote context" ;; esac
journal() {
    if [ "$(id -u)" -eq 0 ]; then journalctl "$@"; else sudo -n journalctl "$@"; fi
}
journal --directory=/var/log/journal --no-pager -n 0 >/dev/null

# 拒绝仓库、生产目录和可能包含用户资料的宽泛根目录。
mkdir -p -- "$1"
WORK_ROOT=$(CDPATH= cd -- "$1" && pwd -P)
case "$WORK_ROOT" in
    /|/var|/var/log|/var/log/*|/etc|/etc/*|/usr|/usr/*|/app|/home|/root|"$REPO_ROOT"|"$REPO_ROOT"/*)
        die "use an isolated work directory outside repository/system/log directories" ;;
esac
WORK_DIR=$(mktemp -d "$WORK_ROOT/agent-backend-journald.XXXXXX")
PROJECT_NAME="agent-backend-journal-$(basename "$WORK_DIR" | tr '[:upper:]' '[:lower:]' | tr '.' '-')"
API_IMAGE="$PROJECT_NAME:verify"
TAG="$PROJECT_NAME-prod-api"
API_PORT=${JOURNAL_VERIFY_API_PORT:-18081}
case "$API_PORT" in ''|*[!0-9]*) die "invalid port" ;; esac
[ "$API_PORT" -gt 1024 ] && [ "$API_PORT" -lt 65536 ] || die "invalid port"
SECRETS_FILE="$WORK_DIR/.secrets"
COMPOSE_ENV_FILE="$WORK_DIR/compose.env"
OVERRIDE_FILE="$WORK_DIR/override.yaml"
STARTED=false
cp "$REPO_ROOT/.secrets.example" "$SECRETS_FILE"
chmod 0600 "$SECRETS_FILE"
cat > "$COMPOSE_ENV_FILE" <<ENV
COMPOSE_PROJECT_NAME=$PROJECT_NAME
APP_ENV=prod
AGENT_BACKEND_IMAGE=$API_IMAGE
API_WORKERS=3
API_PORT=$API_PORT
API_CPUS=2.0
API_MEMORY_LIMIT=2g
APP_SECRETS_FILE=$SECRETS_FILE
ENV
# 避免生产重启策略掩盖失败；端口重写为回环地址。!override 需要 Compose >= 2.24.4。
cat > "$OVERRIDE_FILE" <<YAML
services:
  api:
    restart: "no"
    ports: !override
      - "127.0.0.1:$API_PORT:8000"
YAML
compose() {
    # 不允许调用者的 shell 配置覆盖隔离项目、镜像、端口和示例密钥。
    (
        unset COMPOSE_PROJECT_NAME APP_ENV AGENT_BACKEND_IMAGE API_WORKERS API_PORT API_CPUS API_MEMORY_LIMIT APP_SECRETS_FILE
        docker compose --env-file "$COMPOSE_ENV_FILE" -p "$PROJECT_NAME" \
            -f "$REPO_ROOT/compose.yaml" -f "$OVERRIDE_FILE" "$@"
    )
}
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    if [ "$STARTED" = true ]; then
        compose down >/dev/null 2>&1 || result=1
    fi
    docker image rm "$API_IMAGE" >/dev/null 2>&1 || true
    rm -f -- "$SECRETS_FILE"
    printf 'Evidence: %s\nTag: %s\n' "$WORK_DIR" "$TAG"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

wait_for_api() {
    attempt=0
    while [ "$attempt" -lt 60 ]; do
        code=$(curl --max-time 2 -s -o /dev/null -w '%{http_code}' \
            "http://127.0.0.1:$API_PORT/v1/public/journald-probe" || true)
        [ "$code" = 404 ] && return 0
        attempt=$((attempt + 1))
        sleep 1
    done
    compose logs --tail=80 api >&2 || true
    die "API did not return expected probe 404"
}
check_identity() {
    container_id=$1
    [ "$(docker inspect --format '{{.HostConfig.LogConfig.Type}}' "$container_id")" = journald ] || die "unexpected log driver"
    [ "$(docker inspect --format '{{index .HostConfig.LogConfig.Config "tag"}}' "$container_id")" = "$TAG" ] || die "unexpected tag"
}
emit_requests() {
    marker=$1
    pids=""
    index=1
    while [ "$index" -le 20 ]; do
        curl --max-time 10 -sS -o /dev/null \
            "http://127.0.0.1:$API_PORT/v1/public/journald-probe?marker=$marker&request=$index" &
        pids="$pids $!"
        index=$((index + 1))
    done
    for request_pid in $pids; do wait "$request_pid" || die "probe request failed"; done
}
wait_for_marker() {
    container_id=$1
    marker=$2
    attempt=0
    while [ "$attempt" -lt 30 ]; do
        journal CONTAINER_TAG="$TAG" CONTAINER_ID_FULL="$container_id" --no-pager -o cat > "$WORK_DIR/current.log"
        if grep -Fq "$marker" "$WORK_DIR/current.log"; then return 0; fi
        attempt=$((attempt + 1))
        sleep 1
    done
    die "journal marker missing: $marker"
}

printf 'Acceptance workspace: %s\nTag: %s\n' "$WORK_DIR" "$TAG"
compose config --quiet
compose build api
API_UID=$(docker run --rm --entrypoint id "$API_IMAGE" -u)
API_GID=$(docker run --rm --entrypoint id "$API_IMAGE" -g)
docker run --rm --user 0:0 --entrypoint sh -v "$SECRETS_FILE:/secrets" "$API_IMAGE" \
    -c "chown $API_UID:$API_GID /secrets && chmod 0400 /secrets"
STARTED=true
compose up -d api
wait_for_api
OLD_ID=$(compose ps -q api)
check_identity "$OLD_ID"
BEFORE_MARKER="$PROJECT_NAME-before"
emit_requests "$BEFORE_MARKER"
wait_for_marker "$OLD_ID" "$BEFORE_MARKER"
WORKER_COUNT=$(grep -c 'Started server process' "$WORK_DIR/current.log" || true)
[ "$WORKER_COUNT" -eq 3 ] || die "expected 3 workers, observed $WORKER_COUNT"

compose up -d --force-recreate api
wait_for_api
NEW_ID=$(compose ps -q api)
[ "$OLD_ID" != "$NEW_ID" ] || die "container ID did not change"
check_identity "$NEW_ID"
if docker inspect "$OLD_ID" >/dev/null 2>&1; then die "old container still exists"; fi
AFTER_MARKER="$PROJECT_NAME-after"
emit_requests "$AFTER_MARKER"
wait_for_marker "$NEW_ID" "$AFTER_MARKER"
wait_for_marker "$OLD_ID" "$BEFORE_MARKER"
compose down
STARTED=false
if docker inspect "$NEW_ID" >/dev/null 2>&1; then die "new container still exists after down"; fi

# 强制同步已接收日志，不轮转、不 vacuum；只从持久化目录查询以排除仅存内存的情况。
journal --sync
journal --directory=/var/log/journal CONTAINER_TAG="$TAG" --no-pager -o json > "$WORK_DIR/journal.jsonl"
journal --directory=/var/log/journal CONTAINER_TAG="$TAG" --no-pager -o cat > "$WORK_DIR/export.log"
python3 - "$WORK_DIR/journal.jsonl" "$OLD_ID" "$NEW_ID" "$BEFORE_MARKER" "$AFTER_MARKER" <<'PY'
import json
import re
import sys
from pathlib import Path

path, old_id, new_id, before, after = sys.argv[1:]
records = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
for container_id, marker in ((old_id, before), (new_id, after)):
    messages = [record.get('MESSAGE', '') for record in records if record.get('CONTAINER_ID_FULL') == container_id]
    assert any(isinstance(message, str) and marker in message for message in messages), (container_id, 'marker missing')
    business_messages = []
    for message in messages:
        if not isinstance(message, str):
            continue
        try:
            payload = json.loads(message)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and marker in str(payload.get('message', '')):
            business_messages.append(payload)
    assert business_messages, (container_id, 'no JSON application request log')
    request_ids = set()
    for payload in business_messages:
        match = re.search(r'&request=(\d+)', payload['message'])
        if match:
            request_ids.add(int(match.group(1)))
    assert request_ids == set(range(1, 21)), (container_id, 'missing concurrent request logs', request_ids)
    starts = sum('Started server process' in message for message in messages if isinstance(message, str))
    assert starts == 3, (container_id, 'unexpected worker count', starts)
print('PASS: persistent journal contains application JSON for both deleted container IDs')
PY
grep -Fq "$BEFORE_MARKER" "$WORK_DIR/export.log"
grep -Fq "$AFTER_MARKER" "$WORK_DIR/export.log"
printf 'PASS: journald driver, stable tag, 3 workers, recreation, deletion and export.\n'
printf 'Reboot/retention/load tests were NOT run.\n'
