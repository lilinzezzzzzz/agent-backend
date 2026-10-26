# 日志输出与轮转部署方案

本文档面向开发和运维人员，说明 `agent-backend` 的日志责任边界和本地开发用法，并提供
Docker Compose journald（当前默认）、Docker local、logrotate sidecar（参考）、Linux `logrotate`
和 Kubernetes 的日志持久化方案。

## 应用责任边界

Logger 只负责：

- 将日志输出到 stderr。
- 根据配置向固定的 `app.log` 追加日志。
- 输出 text 或 JSON Lines 格式，并补充 trace ID。

Logger 不负责：

- 重命名或切割日志文件。
- 压缩、保留或清理历史日志。
- 向集中日志后端上传日志。

这个边界避免多个应用实例或 Celery 进程同时轮转同一文件。需要注意，多进程直接追加
同一文件仍不提供严格的全局顺序保证；对顺序、完整性或审计有严格要求时，应使用单一日志采集器。

## 应用配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `log.format` | `text` | `text` 或 `json` |
| `log.base_dir` | 未配置 | 文件输出目录；开启文件输出时必须显式配置 |
| `log.write_to_file` | `false` | 是否写入固定日志文件 |
| `log.write_to_console` | `true` | 是否输出到 stderr |

Celery Worker 使用同一组配置；开启文件输出时路径为 `<log.base_dir>/celery/app.log`。日志格式、目录和输出
目标统一写入 `configs/app.{APP_ENV}.toml`，Compose 只把根目录 `.env` 中的 `APP_ENV` 注入容器。相对
`log.base_dir` 统一基于项目根目录解析，绝对路径保持不变。密钥由不含 `APP_ENV` 的 `.secrets` 提供。

> 开启 `log.write_to_file=true` 后，应用不会自动限制文件大小。长期运行环境必须配套外部轮转，
> 否则 `app.log` 会持续增长。

### 输出目标的选择原则

`log.write_to_file` 和 `log.write_to_console` 描述部署所需的输出目标。本项目选择在
`configs/app.{APP_ENV}.toml` 中显式维护它们，不通过 Compose `environment` 覆盖。当前仓库中的环境配置约定如下：

当前取值如下：

| 环境配置及对应部署 | `log.write_to_file` | `log.write_to_console` | `log.base_dir` | 日志轮转或采集责任 |
| --- | --- | --- | --- | --- |
| `local`：本地开发 | `true` | `true` | `logs`，解析为项目根目录下的 `logs/` | 开发者按需清理；不适合长期运行 |
| `dev` / `test`：开发、测试或临时进程 | `false` | `true` | 不配置 | CI / 终端收集标准错误流 |
| `prod`：本仓库 Compose + journald | `false` | `true` | 不配置，不写应用文件 | 宿主机持久化 journal |

还应遵守以下约束：

- 不要同时设置为 `false`，否则应用日志没有任何输出目标。
- 同时设置为 `true` 主要用于本地调试。在线环境双写会重复占用 I/O 和磁盘，并可能被日志平台重复采集。
- `log.write_to_file=true` 时，必须配置可写的 `log.base_dir` 和独立于应用进程的轮转机制。
- Compose journald 部署必须使用 console-only，避免日志落入容器文件系统。
- `log.format` 与输出目标相互独立；平台采集通常使用 `json`，本地阅读通常使用 `text`。

Compose journald 和 Kubernetes 均使用 console-only；日志存储由各自平台负责。输出目标仍由环境配置文件
声明，不通过 Compose 或 manifest 临时覆盖。

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
在 `configs/app.local.toml` 中配置：

```toml
[log]
format = "text"
base_dir = "logs"
write_to_file = true
write_to_console = true
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

只需要控制台日志时，可在 `configs/app.local.toml` 中关闭文件输出：

```toml
[log]
write_to_file = false
write_to_console = true
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

当前根 Compose 默认选择 journald；其他方案供不同部署需求参考，不会自动启用。
下表并列比较适用边界，当前环境配置取值见前文“输出目标的选择原则”，替代方案的输出要求见各自章节：

| 部署方式 | 主要解决的问题或痛点 | 应用输出 | 轮转和保留责任 |
| --- | --- | --- | --- |
| 本地开发 | 无需部署日志基础设施即可实时查看和回溯日志，降低本地调试成本；不解决长期运行时的磁盘增长问题 | stderr + 固定 `app.log` | 不定时轮转，按需手动清理 |
| Docker local（可选） | 避免应用自行轮转和运维人员直接操作 Docker 内部日志文件；限制单机容器日志的磁盘占用 | stderr | Docker `local` logging driver |
| Linux 直接部署 | 满足传统运维工具对固定日志路径、压缩归档和保留数量的要求，同时让应用继续写入固定文件 | 固定 `app.log` | Linux `logrotate` |
| Compose journald（当前默认，Linux + systemd） | 跨容器重建保留日志，按固定标签查询并按需导出文件 | stdout/stderr | 宿主机 journald |
| Compose logrotate sidecar（参考） | 持续保存宿主机固定路径文件，便于直接下载或对接文件工具；接受 copytruncate 丢失窗口 | bind mount 后的固定 `app.log` | 单副本 sidecar；历史资产见下文 |
| Kubernetes | 解决 Pod 日志易失、实例分散以及跨 Pod 检索困难的问题，并由平台统一补充工作负载元数据、集中存储和设置 retention | stdout/stderr | 节点采集 Agent 和集中日志平台 |

Docker `local` logging driver 和 Linux `logrotate` 主要控制单机磁盘占用与历史文件数量，并不提供跨实例集中检索、
跨节点持久化或严格无丢失保证。只有配置了节点采集 Agent、集中日志后端及其 retention 策略，才能覆盖这些平台级需求。

## Docker local 驱动（可选替代方案）

不需要跨容器删除保留历史时，可以在所选 `configs/app.{APP_ENV}.toml` 中配置 console-only，由 Docker `local`
logging driver 对 stderr 落盘、轮转和压缩：

```toml
[log]
write_to_file = false
write_to_console = true
```

本节不是根 Compose 的默认部署方案；容器删除时 Docker local 历史日志也会清理。
根目录 `.env` 的 `APP_ENV` 必须指向 console-only 的环境文件，`prod` 已符合此输出要求。
`.secrets` 只保留应用启动所需的密钥。以下 Compose 片段假设
容器内项目根目录为 `/app`：

```yaml
services:
  api:
    environment:
      APP_ENV: ${APP_ENV:?set APP_ENV in .env}
    volumes:
      - ./configs/app.${APP_ENV}.toml:/app/configs/app.${APP_ENV}.toml:ro
      - ./.secrets:/app/.secrets:ro
    logging:
      driver: local
      options:
        max-size: "50m"
        max-file: "10"
        compress: "true"

  celery-worker:
    environment:
      APP_ENV: ${APP_ENV:?set APP_ENV in .env}
    volumes:
      - ./configs/app.${APP_ENV}.toml:/app/configs/app.${APP_ENV}.toml:ro
      - ./.secrets:/app/.secrets:ro
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

## Docker Compose logrotate sidecar（参考方案）

本节保留原部署方案，适用于需要持续在宿主机固定目录保存 `.log/.gz` 文件的环境。
**它不是当前默认方案；当前默认使用下文的 Docker Compose journald。**

原 `deploy/logrotate/` 和 `scripts/verify_logrotate_sidecar.sh` 已从当前工作区移除，当前 `compose.yaml`、
`.env.compose.example` 和 `configs/app.prod.toml` 也已切换到 journald。下文的参数、文件路径和命令均属于
历史版本 `117597dad235c48f8b587ec406860082ee5339b4`，不能直接配合当前工作区执行。

### 历史资产与使用边界

以下路径均相对于上述历史提交，而非当前工作区：

| 历史资产 | 用途 |
| --- | --- |
| `compose.yaml`、`.env.compose.example` | API 与 sidecar 编排、部署参数 |
| `configs/.env.prod` | 历史版本的 JSON 文件输出配置（当时仍使用 dotenv） |
| `deploy/logrotate/Dockerfile` | 固定版本的 logrotate 镜像 |
| `deploy/logrotate/agent-backend.conf`、`deploy/logrotate/crontab` | 轮转策略与调度 |
| `scripts/verify_logrotate_sidecar.sh` | 当时的隔离验收脚本 |

可以在仓库根目录只读查看历史内容，不会恢复或覆盖当前文件：

```bash
git show 117597dad235c48f8b587ec406860082ee5339b4:compose.yaml
git show 117597dad235c48f8b587ec406860082ee5339b4:deploy/logrotate/agent-backend.conf
git show 117597dad235c48f8b587ec406860082ee5339b4:scripts/verify_logrotate_sidecar.sh
```

若要重新采用本方案，应准备完整匹配的历史部署资产或另行适配当前版本，并在隔离 Linux 环境重新验收；
不能只恢复 sidecar service 而继续使用 console-only 的生产配置。本节不表示历史镜像、依赖和脚本已针对
当前版本重新验证。已完成计划中的结果仅属于当时版本。

### 架构与适用范围

API 与 sidecar 共享同一个宿主机 bind source，但使用各自职责对应的容器路径：API 只追加
`/app/logs/app.log`，sidecar 从 `/var/log/agent-backend/app.log` 轮转同一个文件。sidecar 不挂载应用配置、
密钥或 Docker socket，不使用网络，根文件系统只读；轮转 state 单独保存在 `logrotate-state` named volume。

该方案只适合接受 `copytruncate` 丢失窗口的普通业务日志。多进程不负责轮转，但仍共享写入文件；
单 worker 也不能消除复制与截断之间的丢失窗口。严格审计完整性需单独设计可靠存储，不能仅凭采用
日志采集器宣称零丢失。多主机和 Kubernetes 通常由平台统一采集与存储。

### Compose 参数和应用配置

以下初始化命令仅用于已经准备好完整历史资产的新部署；已有 `.env` 应手动合并：

```bash
cp .env.compose.example .env
```

该历史提交的 Compose 固定使用 `--workers 1`，这属于当时的进程约定，并非 sidecar 方案本身要求单 worker。
历史 Compose 固定发布宿主机端口，多容器扩容需额外配置端口和负载均衡。当前默认 Compose 的 worker 配置见 journald 章节。

`.env` 保存唯一的 `APP_ENV` 和 Compose 部署参数：镜像名、宿主机日志目录、端口、资源限制及
Docker 内部日志上限。真实密码、token 和 API key 仍只放在 `.secrets`。关键参数如下：

| 参数 | 作用 |
| --- | --- |
| `APP_ENV` | 唯一应用环境标识；可选值为 `local`、`dev`、`test`、`prod` |
| `AGENT_BACKEND_LOG_DIR` | API 与 sidecar 共享的宿主机绝对目录 |
| `AGENT_BACKEND_IMAGE` / `LOGROTATE_IMAGE` | API 和 sidecar 镜像名 |
| `LOGROTATE_ALPINE_REPOSITORY` | sidecar 构建使用的 Alpine repository 根地址；默认使用官方源 |
| `APP_SECRETS_FILE` | 不含 `APP_ENV` 的应用密钥文件，只读挂载到 API 容器 |
| `DOCKER_LOG_MAX_SIZE` / `DOCKER_LOG_MAX_FILE` | API access/error log 与 sidecar stderr 的 Docker `local` driver 上限 |

Compose 根据同一个 `APP_ENV` 挂载 `configs/app.${APP_ENV}.toml`，并只把该环境选择器注入 API 进程。
历史版本的 `configs/.env.prod` 使用以下 Sidecar 拓扑配置；当前版本已改为 console-only：

```env
LOG_BASE_DIR=logs
LOG_WRITE_TO_FILE=true
LOG_WRITE_TO_CONSOLE=false
```

`logs` 基于容器项目根目录 `/app` 解析为 `/app/logs`。Compose 将宿主机 `AGENT_BACKEND_LOG_DIR` 挂载到该
路径；Sidecar 把同一个宿主机目录挂载到 `/var/log/agent-backend`。两个容器目标路径不同，但底层文件相同。

如果目标网络访问 Alpine 官方 CDN 不稳定，可在 `.env` 中覆盖 repository，例如阿里云主机使用：

```dotenv
LOGROTATE_ALPINE_REPOSITORY=https://mirrors.aliyun.com/alpine/v3.22
```

该变量只影响镜像构建，不进入运行中容器；logrotate 版本仍固定为 `3.21.0-r1`。

### 目录权限和启动

先构建 API 镜像，再根据镜像内 `app` 用户的数字 UID/GID 初始化宿主机目录。以下命令使用模板的默认镜像名和
日志路径；修改 `.env` 后应同步替换：

```bash
docker compose build api logrotate
API_UID=$(docker run --rm --entrypoint id agent-backend:prod -u)
API_GID=$(docker run --rm --entrypoint id agent-backend:prod -g)
sudo chown "$API_UID:$API_GID" .secrets
sudo chmod 0400 .secrets
sudo install -d -m 0750 -o "$API_UID" -g "$API_GID" /var/log/agent-backend
docker compose config
docker compose up -d logrotate
docker compose up -d api
```

API 以镜像内的非 root `app` 用户运行，因此宿主机密钥文件必须由对应数字 UID/GID 持有并保持仅所有者可读；只设置
root `0600` 会导致容器启动时报 `PermissionError`。更换 API 镜像后如果运行用户 UID/GID 发生变化，需要先重新设置
密钥文件和日志目录的权限。

Compose 的 bind mount 设置了 `create_host_path: false`，目录或配置文件缺失时应直接失败，避免 Docker 静默创建
root 所有的目录。先启动 sidecar 可以提前暴露配置、state volume 和权限错误；`missingok` 允许此时 `app.log`
尚不存在。

### 轮转策略和文件名

历史策略位于上述提交的 `deploy/logrotate/agent-backend.conf`，内容为：

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
docker compose exec logrotate \
  logrotate --debug --state /var/lib/logrotate/status /etc/logrotate.d/agent-backend
```

只在测试环境或受控维护窗口强制轮转：

```bash
docker compose exec logrotate \
  logrotate --force --state /var/lib/logrotate/status /etc/logrotate.d/agent-backend
ls -lh /var/log/agent-backend
```

检查状态和错误：

```bash
docker compose ps logrotate
docker compose logs --tail=200 logrotate
docker compose exec logrotate test -s /var/lib/logrotate/status
```

sidecar 停止不会联动终止 API，但 `app.log` 会持续增长。此时应提高磁盘告警等级并恢复原 sidecar；不要临时启动
第二个周期调度 sidecar，以免轮转竞争。`docker compose down` 默认保留 state volume 和宿主机日志，不要在普通回滚中
使用 `--volumes` 或删除归档。

仅在准备好完整历史部署资产的隔离 Linux 环境中，使用该历史版本的脚本验收；当前工作区没有此脚本：

```bash
./scripts/verify_logrotate_sidecar.sh /tmp/agent-backend-logrotate-acceptance
```

脚本要求显式隔离目录，覆盖单 worker 数量检查、并发请求持续写入、JSON Lines、inode、`delaycompress`、`rotate 30`、state 持久化、
API 重建和 sidecar 故障隔离。隔离目录必须位于仓库之外，防止测试 secrets 或日志进入 API 镜像 build context。
脚本结束时清理测试容器、state volume 和唯一测试镜像，但保留隔离日志供复核。macOS Docker Desktop 只能用于功能
冒烟，不能替代 Linux bind mount、权限和 inode 验收。

## Docker Compose journald

**当前默认方案。** 本节描述当前根 Compose 与生产配置；原 logrotate sidecar 方案作为独立参考保留在前文。

根 `compose.yaml` 使用 Docker `journald` 驱动，要求 Docker Engine 与持久化 journal 位于同一台
Linux systemd 主机。macOS Docker Desktop、远程 Docker context 和未运行 journald 的主机不能代替目标环境验收。
API 业务日志输出到 stderr；Uvicorn access/error 输出也由同一驱动收集。容器不挂载日志目录或 journal socket，
Docker daemon 负责发送日志。仓库不再部署 logrotate sidecar。

### 配置与固定服务标签

应用配置 `configs/app.prod.toml`：

```toml
[log]
format = "json"
write_to_file = false
write_to_console = true
```

新部署从 `.env.compose.example` 创建根 `.env`；已有部署手动合并参数，不覆盖自己的配置。
`API_WORKERS` 控制 Uvicorn worker 数，默认 1；多 worker 与容器 CPU/内存和下游连接预算共同调整。
日志方案支持多个 worker，不强制单进程。

```yaml
logging:
  driver: journald
  options:
    tag: "${COMPOSE_PROJECT_NAME:?set COMPOSE_PROJECT_NAME in .env}-${APP_ENV:?set APP_ENV in .env}-api"
    mode: blocking
```

默认项目名 `agent-backend`、环境 `prod` 得到 `agent-backend-prod-api`。项目名需在同一部署跨发布保持不变，
不要包含版本、时间或容器 ID。不同项目/环境使用不同标签；同服务副本共享标签。
`CONTAINER_ID_FULL` 和 `IMAGE_NAME` 等 journal 元数据用于区分实例和版本。
不要依赖 `docker compose -p` 覆盖插值中的项目名，统一在根 `.env` 设置 `COMPOSE_PROJECT_NAME`。

此驱动没有日志路径、`max-size` 或 `max-file` 选项。`blocking` 可能把日志背压传递给请求处理；改为
`non-blocking` 会引入缓冲区满时丢弃消息的取舍，不应在没有压测证据时直接更改。

### 宿主机持久化与保留

`deploy/journald/agent-backend.conf` 是供运维审核的宿主机配置示例，不会被 Compose 自动安装。
在获准修改的 Linux 主机上先检查现有配置、磁盘容量和系统日志需求：

```bash
systemctl is-active systemd-journald
systemd-analyze cat-config systemd/journald.conf
journalctl --disk-usage
```

示例内容如下，数值是初始预算，必须按实际日志速率调整：

```ini
[Journal]
Storage=persistent
SystemMaxUse=2G
SystemKeepFree=2G
MaxRetentionSec=14day
Compress=yes
```

普通 journal 配置影响整机，不是本项目独享的配额。空间不足可能提前清理，14day 不保证完整保存 14 天。
不要直接操作 `.journal` 内部文件，也不要对 `/var/log/journal` 再运行 logrotate。
持久化路径为 `/var/log/journal`；`/run/log/journal` 是临时存储。需要独立磁盘时由运维设置挂载，
Compose 不能逐服务指定 journal 存储路径。磁盘损坏和主机丢失需要独立备份，不由本方案保证。

确认不与现有 drop-in 冲突后，在维护窗口安装配置并使其生效；以下命令会修改宿主机，不能在本地文档检查时执行：

```bash
sudo install -d -m 0755 /etc/systemd/journald.conf.d
sudo install -m 0644 deploy/journald/agent-backend.conf /etc/systemd/journald.conf.d/agent-backend.conf
sudo systemctl restart systemd-journald
sudo journalctl --flush
sudo journalctl --sync
```

`RateLimitIntervalSec` / `RateLimitBurst` 可能丢弃高峰日志；Docker 汇聚的流量不能假定按容器独立限流。
上线前观察 journald 的 suppressed/dropped 提示，并按实际峰值校准宿主机策略；不默认关闭整机限流。
避免把“避开 copytruncate 窗口”描述成端到端零丢失保证。

### 构建与启动

先确认上面的宿主机前置条件、真实依赖可达，以及 `.secrets` 不含 `APP_ENV`。API 保持非 root，
应用配置与密钥只读挂载。以下示例镜像名需与根 `.env` 的 `AGENT_BACKEND_IMAGE` 一致：

```bash
docker compose build api
API_UID=$(docker run --rm --entrypoint id agent-backend:prod -u)
API_GID=$(docker run --rm --entrypoint id agent-backend:prod -g)
sudo chown "$API_UID:$API_GID" .secrets
sudo chmod 0400 .secrets
docker compose config --quiet
docker compose up -d api
```

更换镜像用户 UID/GID 后重新核对密钥文件权限。journald 不可用时应修复宿主机前置条件，
不要静默回退为容器内文件日志。仅为该 service 配置日志驱动，无需修改 Docker daemon 的全局默认驱动。

### 日常查询

以下命令在 Docker 所在 Linux 主机执行；将标签换成实际项目名和环境。读取系统日志需要相应权限，
示例使用 sudo。固定标签匹配跨发布的现存 journal 记录，旧容器已删除也可查询；不要加仅限本次开机的 `-b`。

```bash
# 最近 200 条并持续跟踪
sudo journalctl CONTAINER_TAG=agent-backend-prod-api -n 200 -f -o cat

# 最近一小时
sudo journalctl CONTAINER_TAG=agent-backend-prod-api --since "1 hour ago" --no-pager -o short-iso

# 按 trace ID 搜索
sudo journalctl CONTAINER_TAG=agent-backend-prod-api --since "1 day ago" --no-pager -o cat | grep -F '实际_trace_id'

# 包含容器 ID、镜像等元数据
sudo journalctl CONTAINER_TAG=agent-backend-prod-api --since "1 hour ago" --no-pager -o json

# 当前 Compose 容器的便捷查询；跨发布历史使用上面的 journalctl
docker compose logs --tail=200 -f api
```

`-o cat` 输出正文；`-o short-iso` 增加 journal 时间；`-o json` 是包含 MESSAGE 的 journal 外层 JSON。
应用业务消息为 JSON，但 Uvicorn 原生日志可能是文本，因此不能把整个 `-o cat` 输出当成纯 JSON Lines。
需要区分实例时同时增加 `CONTAINER_ID_FULL=实际完整ID` 条件。

### 导出和下载 .log

在服务器上选择有写权限的目录，明确时间范围与时区，导出已保留的历史。以下为北京时间示例：

```bash
mkdir -p "$HOME/log-exports"
sudo journalctl CONTAINER_TAG=agent-backend-prod-api \
  --since "2026-10-15 09:00:00 Asia/Shanghai" \
  --until "2026-10-15 12:00:00 Asia/Shanghai" \
  --no-pager -o cat > "$HOME/log-exports/agent-backend-20261015.log"
```

重定向由当前登录用户执行，sudo 不赋予导出目录写权限。若要保留容器元数据，用 `-o json` 导出 `.jsonl`。
在本地电脑执行下载，替换真实用户、服务器和目录：

```bash
scp 用户名@服务器地址:/home/用户名/log-exports/agent-backend-20261015.log "$HOME/Downloads/"
```

导出不会修改 journal。已过期、被清理或未成功采集的日志不能恢复；导出文件含业务内容，应限制访问并按需清理。

### 从旧 sidecar 部署切换

历史计划中的 logrotate 描述保留为当时记录，不代表当前部署方式。
原方案的结构、参数和可核验 Git 资产见前文“Docker Compose logrotate sidecar（参考方案）”。
旧 `.log/.gz` 不会自动导入 journal，保留原宿主机日志目录和 `logrotate-state` volume，按旧文件方式查询。
先完成宿主机准备及隔离验收，再安排获准的发布窗口：

1. 记录原 Compose 项目、镜像、环境和日志目录，保留可用的旧版本部署文件。
2. 更新环境配置为 console-only，合并新模板；移除旧 `AGENT_BACKEND_LOG_DIR`、`LOGROTATE_*`、
   `DOCKER_LOG_MAX_*` 参数。保留 `API_WORKERS` 和现有资源限制。
3. 重建 API，并验证新的请求日志进入固定标签且可从持久化 journal 查询。
4. 使用旧部署文件精确停止旧 logrotate service；本仓库脚本不自动删除旧容器、归档或 volume。
   不要用无差别 `--remove-orphans`/`--volumes` 清理共享部署。
5. 若失败，使用旧镜像、旧生产配置和旧 Compose 恢复文件输出及 sidecar；切换期间的 journal 与旧文件需分别查询。

更改日志驱动需要重建容器，普通 restart 不足以完成切换。旧归档是否清理是独立运维决定。

### 隔离验收与未验证边界

仅在获准的 Linux 测试主机执行；需要本机 Docker Engine、Compose v2.24.4 或更新版本、持久化 journald、
可构建 API 镜像的网络以及 root 或 `sudo -n journalctl` 权限：

```bash
./scripts/verify_journald.sh /tmp/agent-backend-journald-acceptance
```

脚本使用唯一 Compose 项目、固定测试标签、示例密钥与 3 个 worker。它验证驱动/标签、业务 JSON 到达、
并发请求、容器重建导致 ID 变化但标签不变、旧容器删除后历史仍可读取，以及从 `/var/log/journal` 导出日志。
不存在的匿名探测路径用于产生 404 请求日志，不要求新增业务 API，也不证明数据库等业务依赖健康。
脚本只清理自己的容器、镜像和示例密钥，保留导出文件；测试 journal 记录由宿主机正常 retention 清理。
不会安装宿主机配置、重启服务、清空 journal 或删除旧部署资源。

宿主机重启后的持久化需要另行安排重启验收：保存测试标签与 marker，重启后再用 journalctl 查询。
高负载限流、长消息/异常堆栈完整性、长期 retention、断电与磁盘故障不由此冒烟脚本证明。
本地语法与 YAML 校验不能替代上述 Linux 验收。

2026-10-15 验证记录：在授权 Linux 开发服务器上，Docker Engine 26.1.3、Compose v2.27.0，
执行 `./scripts/verify_journald.sh /tmp/agent-backend-journald-acceptance`，退出码为 0。
两次发布各 3 个 worker、各 20 个并发请求的业务 JSON 均从持久化 journal 读出；容器 ID 改变、
固定标签保持不变，两个容器删除后导出仍包含两次发布的 marker。根配置模板的 Compose 校验也通过。
服务器已有持久化 journal，本次未安装示例 drop-in、未重启宿主机、未修改其他服务。
本地 `uv run --no-sync pytest tests/config/test_config.py -q -p no:cacheprovider`：36 项通过，
存在依赖库弃用警告。shell 语法、YAML 契约和 Markdown 链接检查通过。
以上结果不覆盖前述重启、高负载、长消息、长期保留或业务依赖健康。

参考：[Docker journald driver](https://docs.docker.com/engine/logging/drivers/journald/)、
[journald.conf](https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html)、
[journalctl](https://www.freedesktop.org/software/systemd/man/latest/journalctl.html)。

## Linux logrotate

本节只适用于仍需固定文件的 Linux 直接部署或既有环境，不是根 Compose 的当前方案。
同一日志文件只能有一个轮转者；启用前需显式改为文件输出。

### 应用配置

在所选 `configs/app.{APP_ENV}.toml` 中配置：

```toml
[log]
base_dir = "/var/log/agent-backend"
write_to_file = true
write_to_console = false
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
    environment:
      APP_ENV: ${APP_ENV:?set APP_ENV in .env}
    volumes:
      - ./configs/app.prod.toml:/app/configs/app.prod.toml:ro
      - ./.secrets:/app/.secrets:ro
      - /var/log/agent-backend:/var/log/agent-backend

  celery-worker:
    environment:
      APP_ENV: ${APP_ENV:?set APP_ENV in .env}
    volumes:
      - ./configs/app.prod.toml:/app/configs/app.prod.toml:ro
      - ./.secrets:/app/.secrets:ro
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

仓库内只能验证应用不会执行轮转；`logrotate`、systemd、bind mount 权限和多进程运行时行为必须在
Linux 测试环境完成最终验证。

## Docker 和 Kubernetes 平台统一采集

Kubernetes 部署不应让应用管理 Pod 内的日志文件。在 Kubernetes 使用的
`configs/app.{APP_ENV}.toml` 中显式配置：

```toml
[log]
write_to_file = false
write_to_console = true
```

`configs/app.prod.toml` 已使用 console-only；Kubernetes 仍需自行配置节点采集与后端，不能照搬 Compose 的 journald 驱动。

使用 ConfigMap 和 Secret 将完整配置挂载为文件，容器环境变量只保留 `APP_ENV`。
以下 Deployment 片段假设容器内项目根目录为 `/app`：

```yaml
volumeMounts:
  - name: app-env
    mountPath: /app/configs/app.prod.toml
    subPath: app.prod.toml
    readOnly: true
  - name: app-secrets
    mountPath: /app/.secrets
    subPath: .secrets
    readOnly: true

volumes:
  - name: app-env
    configMap:
      name: agent-backend-config
      items:
        - key: app.prod.toml
          path: app.prod.toml
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
