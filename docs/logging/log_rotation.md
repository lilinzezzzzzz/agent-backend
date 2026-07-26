# 日志输出与轮转部署方案

本文档面向开发和运维人员，说明 `agent-backend` 的日志责任边界和本地开发用法，并提供
Docker Compose、Linux `logrotate` 和 Kubernetes 三种部署环境的轮转与持久化方案。

## 应用责任边界

Logger 只负责：

- 将日志输出到 stderr。
- 根据配置向固定的 `app.log` 追加日志。
- 输出 text 或 JSON Lines 格式，并补充 trace ID。

Logger 不负责：

- 重命名或切割日志文件。
- 压缩、保留或清理历史日志。
- 向集中日志后端上传日志。

这个边界避免多个 Uvicorn Worker 或 Celery 进程同时轮转同一文件。需要注意，多进程直接追加
同一文件仍不提供严格的全局顺序保证；对顺序、完整性或审计有严格要求时，应使用单一日志采集器。

## 应用配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `LOG_FORMAT` | `text` | `text` 或 `json` |
| `LOG_BASE_DIR` | 仓库根目录的 `logs/` | FastAPI 写入 `<LOG_BASE_DIR>/app.log` |
| `LOG_WRITE_TO_FILE` | `true` | 是否写入固定日志文件 |
| `LOG_WRITE_TO_CONSOLE` | `true` | 是否输出到 stderr |

Celery Worker 使用同一组配置，文件路径为 `<LOG_BASE_DIR>/celery/app.log`。
日志配置统一写入 `configs/.env.{APP_ENV}`；`APP_ENV` 和密钥仍由 `configs/.secrets` 提供。
本文档的容器示例通过只读挂载这两个文件传入配置，不使用 Compose `environment`
或 Kubernetes `env` 注入日志配置。

> 开启 `LOG_WRITE_TO_FILE=true` 后，应用不会自动限制文件大小。长期运行环境必须配套外部轮转，
> 否则 `app.log` 会持续增长。

## 日志格式

text 格式用于本地阅读，字段顺序为：

```text
time | level | location | trace_id | message
```

JSON Lines 格式用于平台采集，每条日志是一个独立 JSON 对象且实际落盘只占一行。
下面为便于阅读而格式化展示：

```json
{
  "timestamp": "2026-07-24T11:32:10.123Z",
  "severity_text": "INFO",
  "trace_id": "019f9223fc8a72fca871cca296801911",
  "message": "request completed",
  "code": {
    "namespace": "internal.middlewares.recorder",
    "function": "__call__",
    "lineno": 194
  }
}
```

- `code` 拆分保留 namespace、function 和 lineno，便于日志平台按字段查询。
- 调用方附加的上下文放入 `attributes`；`json_content` 保留原有结构。
- 异常日志的 `exception` 包含 type、message 和 stacktrace。
- 普通日志只使用 `trace_id` 关联 Trace，不输出 `span_id`。

## 本地开发

本地开发默认同时输出到控制台和文件，便于实时调试和回看完整日志。
在 `configs/.env.local` 中配置：

```env
LOG_FORMAT=text
LOG_BASE_DIR=logs
LOG_WRITE_TO_FILE=true
LOG_WRITE_TO_CONSOLE=true
```

从仓库根目录启动应用时，日志文件为：

```text
logs/app.log
logs/celery/app.log
```

可以在另一个终端持续查看：

```bash
tail -F logs/app.log
tail -F logs/celery/app.log
```

只需要控制台日志时，可在 `configs/.env.local` 中关闭文件输出：

```env
LOG_WRITE_TO_FILE=false
LOG_WRITE_TO_CONSOLE=true
```

本地开发不配置定时轮转。如果日志过大，先停止应用，再按需清空固定文件：

```bash
truncate -s 0 logs/app.log
truncate -s 0 logs/celery/app.log
```

长时间运行或需要保留历史日志时，应切换到后文的 Docker 或 Linux 部署方案。

## 选型

这些方案根据不同部署形态解决以下痛点：避免日志文件无限增长耗尽磁盘；避免多个应用进程竞争轮转同一文件；
按需应对容器重建、Pod 漂移或节点故障造成的日志丢失；明确应用、容器运行时、宿主机和日志平台之间的轮转与
保留责任；同时保留统一的 JSON Lines 和 trace ID，便于检索、聚合和故障排查。

不同方案解决的问题和适用边界如下：

| 部署方式 | 主要解决的问题或痛点 | 应用输出 | 轮转和保留责任 |
| --- | --- | --- | --- |
| 本地开发 | 无需部署日志基础设施即可实时查看和回溯日志，降低本地调试成本；不解决长期运行时的磁盘增长问题 | stderr + 固定 `app.log` | 不定时轮转，按需手动清理 |
| Docker / Docker Compose | 避免应用自行轮转和运维人员直接操作 Docker 内部日志文件；限制单机容器日志的磁盘占用 | stderr | Docker `local` logging driver |
| Linux 直接部署 | 满足传统运维工具对固定日志路径、压缩归档和保留数量的要求，同时让应用继续写入固定文件 | 固定 `app.log` | Linux `logrotate` |
| Docker Compose 且要求宿主机固定文件 | 兼顾容器化部署和宿主机固定路径需求，便于既有采集器、备份或普通业务日志工具读取日志 | bind mount 后的固定 `app.log` | 单副本 logrotate sidecar |
| Kubernetes | 解决 Pod 日志易失、实例分散以及跨 Pod 检索困难的问题，并由平台统一补充工作负载元数据、集中存储和设置 retention | stdout/stderr | 节点采集 Agent 和集中日志平台 |

Docker `local` logging driver 和 Linux `logrotate` 主要控制单机磁盘占用与历史文件数量，并不提供跨实例集中检索、
跨节点持久化或严格无丢失保证。只有配置了节点采集 Agent、集中日志后端及其 retention 策略，才能覆盖这些平台级需求。

## Docker 和 Docker Compose

不要求宿主机固定文件路径时，应用只输出 stderr，由 Docker `local` logging driver 落盘、轮转和压缩。
在用于部署的 `configs/.env.prod` 中配置：

```env
LOG_FORMAT=json
LOG_WRITE_TO_CONSOLE=true
LOG_WRITE_TO_FILE=false
```

`configs/.secrets` 必须包含 `APP_ENV=prod`，并保留应用启动所需的其他密钥。
以下 Compose 片段假设容器内项目根目录为 `/app`：

```yaml
services:
  api:
    volumes:
      - ./configs/.env.prod:/app/configs/.env.prod:ro
      - ./configs/.secrets:/app/configs/.secrets:ro
    logging:
      driver: local
      options:
        max-size: "50m"
        max-file: "10"
        compress: "true"

  celery-worker:
    volumes:
      - ./configs/.env.prod:/app/configs/.env.prod:ro
      - ./configs/.secrets:/app/configs/.secrets:ro
    logging:
      driver: local
      options:
        max-size: "50m"
        max-file: "10"
        compress: "true"
```

按 Compose service 查看日志：

```bash
docker compose logs --tail=200 -f api
docker compose logs --since=30m celery-worker
```

Docker 管理的内部日志文件不是运维契约，不应直接使用 `tail`、`logrotate` 或清理脚本操作。
参考 [Docker local logging driver](https://docs.docker.com/engine/logging/drivers/local/) 和
[Compose logging 配置](https://docs.docker.com/reference/compose-file/services/#logging)。

## Docker Compose logrotate sidecar

当单台 Linux 主机必须在固定宿主机目录交付日志文件时，仓库提供 `compose.prod.yaml` 和
`deploy/logrotate/`。API 与 sidecar 共享同一个 bind mount：API 只追加
`/var/log/agent-backend/app.log`，sidecar 保持单副本并负责轮转。sidecar 不挂载应用配置、密钥或 Docker
socket，不使用网络，根文件系统只读；轮转 state 单独保存在 `logrotate-state` named volume。

该方案只适合允许 `copytruncate` 极小丢失窗口的普通业务日志。审计、计费、合规、多主机或 Kubernetes 场景应使用
stdout/stderr、节点级单一采集器和集中日志后端。

### Compose 参数和应用配置

从模板创建根目录 `.env`：

```bash
cp .env.compose.example .env
```

`.env` 只保存 Compose 部署参数：镜像名、宿主机日志目录、API Worker 数、端口、资源限制、Docker 内部日志上限，
以及应用配置文件的挂载路径。真实密码、token 和 API key 仍只放在 `configs/.secrets`。关键参数如下：

| 参数 | 作用 |
| --- | --- |
| `AGENT_BACKEND_LOG_DIR` | API 与 sidecar 共享的宿主机绝对目录 |
| `API_WORKERS` | API 容器内 Uvicorn Worker 数量 |
| `AGENT_BACKEND_IMAGE` / `LOGROTATE_IMAGE` | API 和 sidecar 镜像名 |
| `LOGROTATE_ALPINE_REPOSITORY` | sidecar 构建使用的 Alpine repository 根地址；默认使用官方源 |
| `APP_CONFIG_ENV` | 配置文件在容器内的环境后缀，生产环境为 `prod` |
| `APP_ENV_FILE` / `APP_SECRETS_FILE` | 只读挂载的应用配置和密钥文件 |
| `DOCKER_LOG_MAX_SIZE` / `DOCKER_LOG_MAX_FILE` | API access/error log 与 sidecar stderr 的 Docker `local` driver 上限 |

`APP_CONFIG_ENV` 必须与 `APP_SECRETS_FILE` 中的 `APP_ENV` 一致。`configs/.env.prod` 已将文件日志显式配置为：

```env
LOG_FORMAT=json
LOG_BASE_DIR=/var/log/agent-backend
LOG_WRITE_TO_FILE=true
LOG_WRITE_TO_CONSOLE=false
```

如果目标网络访问 Alpine 官方 CDN 不稳定，可在 `.env` 中覆盖 repository，例如阿里云主机使用：

```dotenv
LOGROTATE_ALPINE_REPOSITORY=https://mirrors.aliyun.com/alpine/v3.22
```

该变量只影响镜像构建，不进入运行中容器；logrotate 版本仍固定为 `3.21.0-r1`。

### 目录权限和启动

先构建 API 镜像，再根据镜像内 `app` 用户的数字 UID/GID 初始化宿主机目录。以下命令使用模板的默认镜像名和
日志路径；修改 `.env` 后应同步替换：

```bash
docker compose -f compose.prod.yaml build api logrotate
API_UID=$(docker run --rm --entrypoint id agent-backend:prod -u)
API_GID=$(docker run --rm --entrypoint id agent-backend:prod -g)
sudo chown "$API_UID:$API_GID" configs/.secrets
sudo chmod 0400 configs/.secrets
sudo install -d -m 0750 -o "$API_UID" -g "$API_GID" /var/log/agent-backend
docker compose -f compose.prod.yaml config
docker compose -f compose.prod.yaml up -d logrotate
docker compose -f compose.prod.yaml up -d api
```

API 以镜像内的非 root `app` 用户运行，因此宿主机密钥文件必须由对应数字 UID/GID 持有并保持仅所有者可读；只设置
root `0600` 会导致容器启动时报 `PermissionError`。更换 API 镜像后如果运行用户 UID/GID 发生变化，需要先重新设置
密钥文件和日志目录的权限。

Compose 的 bind mount 设置了 `create_host_path: false`，目录或配置文件缺失时应直接失败，避免 Docker 静默创建
root 所有的目录。先启动 sidecar 可以提前暴露配置、state volume 和权限错误；`missingok` 允许此时 `app.log`
尚不存在。

### 轮转策略和文件名

唯一策略来源是 `deploy/logrotate/agent-backend.conf`：

```text
daily
maxsize 50M
rotate 30
missingok
notifempty
dateext
dateformat .%Y-%m-%d_%H-%M-%S
extension .log
compress
delaycompress
copytruncate
```

sidecar 每 5 分钟检查一次。`maxsize 50M` 只在检查时生效，活动文件可能在两次检查之间超过阈值；阈值和检查周期
必须根据峰值日志速率与磁盘预算校准。活动文件及归档命名如下：

```text
app.log
app.2026-07-26_14-30-00.log
app.2026-07-25_14-30-00.log.gz
```

`delaycompress` 让最新归档保持未压缩，下一次轮转后才压缩。`rotate 30` 表示最多保留 30 个轮转文件，不等同于
严格保留 30 个自然日。

### 验证与故障排查

调试解析不会执行轮转：

```bash
docker compose -f compose.prod.yaml exec logrotate \
  logrotate --debug --state /var/lib/logrotate/status /etc/logrotate.d/agent-backend
```

只在测试环境或受控维护窗口强制轮转：

```bash
docker compose -f compose.prod.yaml exec logrotate \
  logrotate --force --state /var/lib/logrotate/status /etc/logrotate.d/agent-backend
ls -lh /var/log/agent-backend
```

检查状态和错误：

```bash
docker compose -f compose.prod.yaml ps logrotate
docker compose -f compose.prod.yaml logs --tail=200 logrotate
docker compose -f compose.prod.yaml exec logrotate test -s /var/lib/logrotate/status
```

sidecar 停止不会联动终止 API，但 `app.log` 会持续增长。此时应提高磁盘告警等级并恢复原 sidecar；不要临时启动
第二个周期调度 sidecar，以免轮转竞争。`docker compose down` 默认保留 state volume 和宿主机日志，不要在普通回滚中
使用 `--volumes` 或删除归档。

目标 Linux 环境的完整验收命令为：

```bash
./scripts/verify_logrotate_sidecar.sh /tmp/agent-backend-logrotate-acceptance
```

脚本要求显式隔离目录，覆盖多 Worker 持续写入、JSON Lines、inode、`delaycompress`、`rotate 30`、state 持久化、
API 重建和 sidecar 故障隔离。隔离目录必须位于仓库之外，防止测试 secrets 或日志进入 API 镜像 build context。
脚本结束时清理测试容器、state volume 和唯一测试镜像，但保留隔离日志供复核。macOS Docker Desktop 只能用于功能
冒烟，不能替代 Linux bind mount、权限和 inode 验收。

## Linux logrotate

本方案适用于 Linux 直接部署，或仍由宿主机统一管理 logrotate 的既有环境；它是 sidecar 的替代部署形态，不能与
sidecar 同时管理同一个 `app.log`。

### 应用配置

在 `configs/.env.prod` 中配置：

```env
LOG_FORMAT=json
LOG_BASE_DIR=/var/log/agent-backend
LOG_WRITE_TO_FILE=true
LOG_WRITE_TO_CONSOLE=false
```

目标文件为：

```text
/var/log/agent-backend/app.log
/var/log/agent-backend/celery/app.log
```

### Docker Compose bind mount

以下片段是示例，需要根据实际镜像用户和 Compose 文件合并：

```yaml
services:
  api:
    volumes:
      - ./configs/.env.prod:/app/configs/.env.prod:ro
      - ./configs/.secrets:/app/configs/.secrets:ro
      - /var/log/agent-backend:/var/log/agent-backend

  celery-worker:
    volumes:
      - ./configs/.env.prod:/app/configs/.env.prod:ro
      - ./configs/.secrets:/app/configs/.secrets:ro
      - /var/log/agent-backend:/var/log/agent-backend
```

在启动容器前创建目录。`<uid>` 和 `<gid>` 必须替换为容器内应用用户的数字 UID/GID：

```bash
sudo install -d -m 0750 -o <uid> -g <gid> /var/log/agent-backend
sudo install -d -m 0750 -o <uid> -g <gid> /var/log/agent-backend/celery
```

### logrotate 配置

在 Linux 宿主机创建 `/etc/logrotate.d/agent-backend`：

```text
/var/log/agent-backend/app.log
/var/log/agent-backend/celery/app.log {
    daily
    rotate 30
    missingok
    notifempty

    dateext
    dateformat .%Y-%m-%d_%H-%M-%S
    extension .log

    compress
    delaycompress
    copytruncate
}
```

活动文件始终保持为 `app.log`，轮转文件名使用轮转发生时间，例如：

```text
app.log
app.2026-07-24_03-20-00.log
app.2026-07-23_03-20-00.log.gz
```

`copytruncate` 会复制当前文件，然后在原 inode 上清空内容，使仍持有原文件句柄的 Uvicorn 和 Celery 进程可以
继续写入。复制和清空之间存在很小的窗口，该窗口内的日志理论上可能丢失。严格无丢失场景应改用单一日志
采集器，不依赖 `copytruncate`。参考 [logrotate manual](https://man7.org/linux/man-pages/man8/logrotate.8.html)。

`rotate 30` 表示保留 30 个轮转文件，不等同于严格保留 30 个自然日。如果增加 `maxsize`，阈值只会在 `logrotate`
被调度时检查，因此还需要根据日志增长速度调整 systemd timer 的执行频率。

### Linux 验证

以调试模式检查配置，不执行轮转：

```bash
sudo logrotate -d /etc/logrotate.d/agent-backend
```

在测试环境强制轮转：

```bash
sudo logrotate -f /etc/logrotate.d/agent-backend
ls -lh /var/log/agent-backend /var/log/agent-backend/celery
tail -F /var/log/agent-backend/app.log
```

检查 systemd 调度：

```bash
systemctl status logrotate.timer
journalctl -u logrotate.service
```

仓库内只能验证应用不会执行轮转；`logrotate`、systemd、bind mount 权限和多 Worker 运行时行为必须在
Linux 测试环境完成最终验证。

## Docker 和 Kubernetes 平台统一采集

Kubernetes 部署不应让应用管理 Pod 内的日志文件。应用只输出 stdout/stderr：
在 `configs/.env.prod` 中配置：

```env
LOG_FORMAT=json
LOG_WRITE_TO_CONSOLE=true
LOG_WRITE_TO_FILE=false
```

使用 ConfigMap 和 Secret 将完整配置挂载为文件，而不是注入为环境变量。
以下 Deployment 片段假设容器内项目根目录为 `/app`：

```yaml
volumeMounts:
  - name: app-env
    mountPath: /app/configs/.env.prod
    subPath: .env.prod
    readOnly: true
  - name: app-secrets
    mountPath: /app/configs/.secrets
    subPath: .secrets
    readOnly: true

volumes:
  - name: app-env
    configMap:
      name: agent-backend-config
      items:
        - key: .env.prod
          path: .env.prod
  - name: app-secrets
    secret:
      secretName: agent-backend-secrets
      items:
        - key: .secrets
          path: .secrets
```

节点级采集 Agent，例如 Fluent Bit 或 Vector，读取容器日志并补充 namespace、Pod、container 和 label 等元数据，
然后发送至 Loki、Elasticsearch 或对象存储等后端。轮转、保留、压缩、索引和归档由平台统一负责。

节点上的容器日志只是采集源，不是长期持久化保证。需要跨 Pod 删除、节点故障或集群重建保留日志时，
必须配置集中后端及其 retention 策略。参考 [Kubernetes Logging Architecture](https://kubernetes.io/docs/concepts/cluster-administration/logging/)。
