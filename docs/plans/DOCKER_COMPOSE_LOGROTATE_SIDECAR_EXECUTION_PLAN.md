# Docker Compose + logrotate Sidecar 实施计划

状态：开发与目标 Linux 验收已于 2026-07-26 完成。Compose、logrotate sidecar、生产日志配置、验收脚本和
运行文档均已落地；生产上线仍需使用真实环境参数完成依赖连通性、磁盘告警和容量预算确认。

## 1. 目标与关键结论

目标是在一台 Linux 服务器上使用 Docker Compose 运行单个 FastAPI 容器，并允许容器内启动多个
Uvicorn Worker，同时满足以下要求：

- 应用日志持续写入宿主机固定目录 `/var/log/agent-backend`。
- 不修改宿主机 `/etc/logrotate.d`、systemd timer 或 cron 配置。
- 多个 Worker 只追加同一个固定文件，不在 Loguru 中执行 rotation、retention 或 compression。
- 由一个独立的 logrotate sidecar 负责切割、压缩和保留日志。
- API 容器重建后日志仍保留，sidecar 重建后轮转状态仍保留。
- 保持未来迁移 Kubernetes 时可通过配置切换到 stdout/stderr，不让业务代码依赖宿主机日志路径。

关键结论：

- FastAPI 与 logrotate 分属两个 Compose service，共享同一个宿主机 bind mount。
- logrotate service 固定为单副本；不得跟随 API Worker 数或未来 API 容器副本数扩容。
- 活动日志始终为 `/var/log/agent-backend/app.log`，初版使用 `copytruncate`，避免要求所有 Worker
  重新打开文件句柄。
- `copytruncate` 只适用于允许极小丢失窗口的普通业务日志，不提供审计级严格无丢失保证。
- sidecar 是 Compose 部署适配层，不进入应用镜像，也不作为 Kubernetes 目标架构。

日志格式、应用责任边界和其他部署形态以
[日志输出与轮转部署方案](../logging/log_rotation.md)为准；本计划只负责 sidecar 的落地步骤和验收门槛。

## 2. 当前状态

当前代码和部署资产具有以下事实：

- `Dockerfile` 以非 root 的 `app` 用户运行 Uvicorn；`compose.prod.yaml` 通过 `API_WORKERS` 显式配置 Worker 数量。
- `pkg/logger/handler.py` 将应用日志追加到 `<LOG_BASE_DIR>/app.log`，没有配置 Loguru rotation、retention
  或 compression，符合目标责任边界。
- `configs/.env.prod` 已显式配置 JSON、固定日志目录、文件输出开启和控制台业务日志关闭。
- `compose.prod.yaml` 已定义 API 与单副本 sidecar；根目录 `.env` 负责 Compose 参数，模板为
  `.env.compose.example`，应用配置和 secrets 继续只读挂载。
- `deploy/logrotate/` 已提供 digest 锁定的 Alpine 基础镜像、固定 logrotate 版本、轮转策略和前台调度配置。
- sidecar 构建默认使用 Alpine 官方 repository，并允许通过 Compose `.env` 覆盖 repository 根地址，以适配目标网络。
- `scripts/verify_logrotate_sidecar.sh` 已覆盖隔离 Linux 多 Worker 验收流程，README 和日志文档已提供运行手册；
  脚本还会按 API 运行用户的数字 UID/GID 设置测试 secrets 和日志目录权限。

目标 Linux 已验证权限、inode、`copytruncate`、压缩、state、API 重建和故障隔离行为。该验收使用测试配置，不能
替代生产数据库、Redis、LLM provider、告警和容量确认。

## 3. 范围与非目标

本计划包含：

- 单个 API 容器和单个 logrotate sidecar 的 Compose 编排。
- API 容器内可配置的 Uvicorn Worker 数量。
- FastAPI `app.log` 的宿主机固定目录、轮转策略、权限和持久化。
- sidecar 镜像的最小化构建、前台调度、状态文件和容器日志。
- Compose 静态校验、镜像构建校验和 Linux 多 Worker 运行时验收。
- README 和日志部署文档同步。

本计划不包含：

- PostgreSQL、Redis、Milvus、Celery Worker 或 Celery Beat 的完整生产编排。
- 集中日志平台、Fluent Bit、Vector、Loki 或 Elasticsearch 的部署。
- 审计级零丢失日志、跨节点检索或异地持久化。
- Kubernetes manifests、Helm Chart、PVC 或节点级日志 Agent。
- 修改 Loguru 公共接口、日志 JSON schema 或 trace ID contract。
- 自动创建、删除或清理宿主机 `/var/log/agent-backend`；目录初始化由部署前置步骤显式完成。

如果后续把 Celery 纳入同一 Compose 项目，应另行评估并将 `/var/log/agent-backend/celery/app.log` 纳入
sidecar 配置和验收，不在本次 FastAPI 首版中默认扩展范围。

## 4. 目标拓扑与责任边界

```text
Linux host
└── /var/log/agent-backend                         固定宿主机目录
    ├── app.log                                    活动日志
    └── app.<rotation-time>.log[.gz]               历史日志
          ▲                              ▲
          │ bind mount                   │ 同一 bind mount
          │                              │
    ┌─────┴──────────────────┐     ┌─────┴────────────────────┐
    │ api container          │     │ logrotate sidecar       │
    │                        │     │                          │
    │ Uvicorn parent         │     │ 单个前台调度进程         │
    │ ├── Worker 1           │     │ └── logrotate           │
    │ ├── Worker 2           │     │     copytruncate        │
    │ └── Worker N           │     │     compress/retention  │
    │                        │     │                          │
    │ Loguru: append only    │     │ 不挂载应用密钥或         │
    │ app.log                │     │ Docker socket            │
    └────────────────────────┘     └──────────────────────────┘
                                             │
                                             ▼
                                  logrotate-state named volume
```

责任边界：

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| FastAPI Worker | 生成 JSON Lines 日志并追加 `app.log` | 切割、压缩、清理历史日志 |
| API 容器 | 运行 Uvicorn、多 Worker 和健康检查 | 启动 cron、管理 logrotate |
| logrotate sidecar | 定时检查、`copytruncate`、压缩、保留和输出执行错误 | 生成业务日志、读取应用密钥 |
| Docker Compose | 挂载目录、限制资源、配置重启策略和容器日志上限 | 提供跨节点日志持久化 |
| Linux 宿主机 | 提供固定目录和磁盘容量 | 安装或配置宿主机 logrotate |

## 5. 配置与安全决策

### 5.1 API 日志配置

`configs/.env.prod` 计划显式增加：

```dotenv
LOG_FORMAT=json
LOG_BASE_DIR=/var/log/agent-backend
LOG_WRITE_TO_FILE=true
LOG_WRITE_TO_CONSOLE=false
```

配置文件和 `configs/.secrets` 继续只读挂载到 API 容器。sidecar 不挂载 `configs/`、数据库密码、Redis
密码、LLM API key 或其他应用密钥。

Uvicorn access/error log 仍可能写入容器 stdout/stderr，因此 API 和 sidecar 都应配置 Docker `local`
logging driver 的 `max-size` 和 `max-file`，避免 Docker 内部日志无限增长。Docker 内部日志文件不是固定目录
交付 contract。

### 5.2 Compose 参数

生产 Compose 计划支持以下非密钥参数，并提供保守默认值：

| 参数 | 初始默认值 | 用途 |
| --- | --- | --- |
| `AGENT_BACKEND_LOG_DIR` | `/var/log/agent-backend` | 宿主机 bind mount 来源目录 |
| `API_WORKERS` | `2` | API 容器内 Uvicorn Worker 数 |

`API_WORKERS` 只是启动拓扑参数，不加入 `internal.config.Settings`。正式值必须结合容器 CPU quota、内存、
数据库连接上限和压测结果调整，不能使用未验证的固定 CPU 公式。

### 5.3 初始轮转策略

初版配置采用：

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

sidecar 每 5 分钟检查一次配置；`maxsize` 只在检查时生效，因此文件可能在两次检查之间超过 50 MiB。
正式阈值必须根据压测日志速率和宿主机磁盘预算校准。

### 5.4 权限与容器约束

- 部署前解析 API 镜像中 `app` 用户的数字 UID/GID，并据此创建宿主机目录。
- API 容器继续以非 root 用户运行。
- sidecar 初版允许以 root 用户运行 logrotate，但只挂载日志目录和状态 volume；禁止 privileged、Docker
  socket、应用配置和密钥挂载，并启用 `no-new-privileges`。
- sidecar 根文件系统设为只读，只开放日志 bind mount、状态 volume 和确有需要的临时目录。
- sidecar 不需要网络，验证基础镜像和调度器后优先设置 `network_mode: none`。
- sidecar 镜像使用明确版本并在实施时锁定 digest，不使用 `latest` 或未经审查的第三方 logrotate 镜像。

root sidecar 对 bind mount 内文件具有修改能力，这是该方案的剩余风险。若 Linux 验证证明 logrotate、压缩和
调度器可在统一数字 UID/GID 下稳定工作，应在后续收紧为非 root；不得为了降权牺牲可验证的轮转行为。

## 6. 已新增或调整的文件

已新增：

```text
.env.compose.example
compose.prod.yaml
deploy/logrotate/Dockerfile
deploy/logrotate/agent-backend.conf
deploy/logrotate/crontab
scripts/verify_logrotate_sidecar.sh
```

已调整：

```text
configs/.env.prod
README.md
docs/logging/log_rotation.md
docs/plans/DOCKER_COMPOSE_LOGROTATE_SIDECAR_EXECUTION_PLAN.md
```

`docs/README.md` 和 `docs/plans/README.md` 已存在指向本计划的链接，经核对无需修改。

文件职责：

- `compose.prod.yaml`：定义单个 API service、单个 logrotate service、共享 bind mount、状态 volume、
  Worker 数、资源边界和 Docker logging driver。
- `.env.compose.example`：Compose 非密钥部署参数模板，复制为 Git 忽略的根目录 `.env` 后使用。
- `deploy/logrotate/Dockerfile`：构建最小 sidecar，安装固定版本的 logrotate，并以前台方式运行调度器。
- `deploy/logrotate/agent-backend.conf`：唯一轮转策略来源。
- `deploy/logrotate/crontab`：每 5 分钟触发一次检查，命令明确指定持久化 state 文件。
- `scripts/verify_logrotate_sidecar.sh`：只面向隔离 Linux 测试环境，执行可重复的 Compose 验收；不得默认操作
  生产日志目录。
- `docs/logging/log_rotation.md`：增加 sidecar 运行方案和故障排查，保留宿主机 logrotate 作为另一种部署形态。

实施前已检查工作区和 `docs/logging/log_rotation.md`，当时不存在未提交修改；本次以增量方式增加 sidecar 章节。

## 7. 分阶段实施

### Phase 0：基线和部署参数确认

进度：目标 Linux 的 Docker/Compose、动态 UID/GID 权限初始化和文件系统余量已验证；生产外部依赖、告警、备份、
最终资源值和长期磁盘预算仍需上线前确认。

目标：在创建部署资产前确认运行用户、Docker Compose 能力和磁盘预算。

执行项：

- 确认目标 Linux 主机的 Docker Engine 和 Docker Compose 版本。
- 构建当前 API 镜像并查询 `app` 用户的数字 UID/GID。
- 确认 `/var/log/agent-backend` 所在文件系统的容量、inode、备份和监控策略。
- 确认 `50M`、`rotate 30`、每 5 分钟检查的初始策略满足磁盘预算。
- 确认生产 DB、Redis 和其他依赖的网络地址由现有配置正确提供，不把依赖部署扩展进本计划。
- 记录目标容器 CPU/memory 限制和初始 `API_WORKERS`。

完成门槛：

- 已获得可复现的 UID/GID 和目录创建命令。
- 已确认轮转阈值不会超过宿主机磁盘预算。
- 已确认 Compose 版本能够解析计划使用的字段。
- 不存在需要在 Compose 文件中写入明文密钥的依赖配置。

### Phase 1：构建 logrotate sidecar

进度：已完成。固定依赖镜像构建、配置执行、state 写入和 Compose SIGTERM 停止均在目标 Linux 验证通过。

目标：产生职责单一、可重复构建且可独立验证的 sidecar 镜像。

执行项：

- 选择并锁定最小基础镜像版本和 digest。
- 安装 logrotate 及压缩所需的最小运行时依赖。
- 将轮转配置复制到镜像内固定只读路径。
- 配置前台调度器，每 5 分钟执行一次 logrotate，并明确指定
  `/var/lib/logrotate/status`。
- 让 logrotate 和调度器错误输出到 stderr，确保可用 `docker compose logs logrotate` 排查。
- 验证 SIGTERM 能终止前台调度器，不遗留失控子进程。

完成门槛：

- sidecar 镜像可离线启动，不需要运行时安装包或下载配置。
- `logrotate --debug` 能解析配置且不执行轮转。
- state 文件目录可写，日志目录缺失或 `app.log` 尚未创建时不会导致容器退出。
- sidecar 不包含应用源代码和密钥。

### Phase 2：新增生产 Compose 编排

进度：已完成，并使用 `.env.compose.example` 执行 `docker compose config` 静态验证。

目标：把 API、多 Worker、固定宿主机目录和单个 sidecar 组成可验证的部署单元。

执行项：

- 新增 `compose.prod.yaml`，API 使用当前项目镜像和显式 Uvicorn `--workers` 参数。
- API 与 sidecar 将同一个 `${AGENT_BACKEND_LOG_DIR}` 挂载到容器内
  `/var/log/agent-backend`。
- sidecar 额外挂载 `logrotate-state` named volume。
- 两个 service 均设置重启策略和 Docker logging driver 上限。
- sidecar 不暴露端口、不加入不需要的网络、不挂载应用 secrets。
- Compose 不声明 sidecar replicas，也不提供随 API 扩容的入口。
- `configs/.env.prod` 显式写入文件日志配置，避免依赖 `Settings` 默认值。

完成门槛：

- `docker compose -f compose.prod.yaml config` 成功且展开结果无明文密钥。
- 展开结果中 API 和 sidecar 的容器内日志目录完全一致。
- 只有 API 挂载应用配置和 secrets。
- API Worker 数可以通过非密钥部署参数调整，sidecar 始终为一个容器。

### Phase 3：增加可重复的 Linux 验收

进度：已完成。验收脚本通过 `sh -n`，并在目标 Linux 隔离目录完整运行通过。

目标：验证真实 bind mount、多 Worker、`copytruncate`、压缩和重建后的持续写入行为。

验收脚本必须使用显式传入的隔离临时目录，不得默认指向生产
`/var/log/agent-backend`，并覆盖：

1. 启动 API 和 sidecar，确认 API 容器存在预期数量的 Uvicorn Worker。
2. 产生带唯一 marker 的并发请求日志，确认 `app.log` 存在且每个非空行都是合法 JSON。
3. 记录活动文件 inode，通过 sidecar 强制轮转，确认归档文件生成且活动文件 inode 未变化。
4. 轮转后再次产生 marker，确认所有 Worker 仍能向活动 `app.log` 写入。
5. 再执行一个轮转周期，确认 `delaycompress` 后的历史文件被压缩且保留数量符合配置。
6. 重启 sidecar，确认 state 文件仍存在且不会因状态丢失产生非预期重复轮转。
7. 重建 API 容器，确认宿主机日志和归档文件未丢失。
8. 停止 sidecar，确认 API 业务不被联动停止；同时验证 sidecar 故障能从容器状态或日志中被发现。

完成门槛：

- 所有验收步骤在目标 Linux 文件系统上通过，并保存命令、Compose 版本和关键输出。
- 没有出现权限拒绝、归档覆盖、sidecar 多实例或 Worker 轮转竞争。
- 测试只证明观测到的日志完整性，不宣称 `copytruncate` 严格零丢失。

### Phase 4：文档、运行手册和上线准备

进度：开发文档和 Linux 验收记录已同步；真实生产依赖、告警接入和上线记录待部署阶段完成。

目标：让部署、排障和回滚不依赖口头知识。

执行项：

- README 增加生产 Compose 的前置条件、启动、查看状态和停止命令。
- `docs/logging/log_rotation.md` 增加 sidecar 方案、权限初始化、强制轮转、state 检查和常见故障。
- 文档明确区分应用 `app.log` 与 Docker stdout/stderr 内部日志。
- 文档记录 sidecar 停止、磁盘接近阈值、压缩失败和权限错误的检查方法。
- 记录 Kubernetes 迁移方式：删除 sidecar 和日志 bind mount，切换为
  `LOG_WRITE_TO_FILE=false`、`LOG_WRITE_TO_CONSOLE=true`，交由节点 Agent 采集。

完成门槛：

- 新环境仅依赖仓库文档即可完成部署和一次安全的测试轮转。
- 所有命令标明生产与测试边界，不包含真实凭据。
- 文档中的文件路径、service 名和配置键与 Compose 展开结果一致。

## 8. 验证矩阵

| 层级 | 验证方式 | 通过信号 |
| --- | --- | --- |
| 文档 | 检查相对链接、路径、命令和当前/目标状态 | 无断链，已实现与待验证状态明确区分 |
| Compose | `docker compose --env-file .env.compose.example -f compose.prod.yaml config` | 退出码为 0，挂载和参数符合预期 |
| Sidecar 镜像 | 构建镜像并运行 `logrotate --debug` | 构建成功，配置解析成功且无写操作 |
| 应用 logger | `uv run --group dev python -m pytest tests/logger/test_logger.py` | 固定文件追加和关闭文件输出测试通过 |
| Python 静态检查 | `uv run ruff check` | 退出码为 0；如新增 Python 文件则必须执行 |
| Shell 脚本 | `sh -n scripts/verify_logrotate_sidecar.sh` | 退出码为 0 |
| Linux 集成 | 隔离目录运行验收脚本 | 多 Worker 在轮转前后持续写入，归档和 state 持久化符合预期 |
| 故障验证 | 停止或重启 sidecar | API 不被联动停止，sidecar 故障可观察 |

macOS Docker Desktop、本地单元测试或静态 Compose 解析不能替代 Linux bind mount、文件权限和
`copytruncate` 的最终验收。

### 2026-07-26 Linux 验收记录

- Git commit：`c5eee560ec0d32a4c531ff0e6b2b66ff58324976`，服务器通过 `git pull --ff-only` 获取代码。
- 环境：Alibaba Cloud Linux，kernel `5.10.134-19.2.al8.x86_64`，Docker `26.1.3`，Compose `v2.27.0`。
- 参数：3 个 Uvicorn Worker，测试端口 `28000`；sidecar 构建使用阿里云 Alpine repository 覆盖值。
- 结果：验收脚本退出码为 0；完成 31 次强制轮转，最终保留 30 个归档，其中 29 个 gzip 压缩归档和 1 个因
  `delaycompress` 保持未压缩的最新归档。
- 已验证：JSON Lines、活动文件 inode 不变、轮转后继续写入、state 重启持久化、API 重建后归档保留、停止
  sidecar 后 API 继续运行。
- 清理：测试容器、测试 network、state volume 和唯一测试镜像均已删除；日志证据保留在隔离验收目录。
- 生产前置：验收时根文件系统约有 7.8 GiB 可用、使用率约 80%；上线前必须结合真实日志速率复核
  `50M × 30` 策略，并接入磁盘、活动日志增长、sidecar 状态和 logrotate 错误告警。

## 9. 上线顺序

1. 构建并扫描 API 与 sidecar 镜像，记录不可变镜像标识。
2. 在目标主机创建 `/var/log/agent-backend`，设置为 API 容器 `app` 用户可写，并验证剩余磁盘空间。
3. 挂载生产配置和 secrets，执行 Compose config 校验，不输出 secrets 内容。
4. 先启动 sidecar，确认配置、state 路径和缺失日志文件处理正常。
5. 启动 API，确认 Worker 数、健康检查和 `app.log` 写入。
6. 执行一次测试环境或维护窗口内的强制轮转，确认轮转后新日志继续进入 `app.log`。
7. 接入至少以下告警：宿主机磁盘使用率、`app.log` 异常增长、sidecar 非运行状态、logrotate 错误日志。

如果任一项未通过，不进入下一步，也不通过手工修改运行中容器绕过镜像或 Compose 配置。

## 10. 回滚与故障处理

### 10.1 sidecar 发布失败

- 不启动 API 文件日志，或保持旧部署继续运行。
- 修复 sidecar 镜像或配置后重新执行 `logrotate --debug` 和隔离目录验收。
- 不删除已有宿主机日志或 state volume。

### 10.2 sidecar 运行中故障

- API 可以继续追加 `app.log`，但磁盘风险会随时间增长。
- 先恢复 sidecar 或在受控维护窗口内使用同一镜像执行一次单次 logrotate。
- 在 sidecar 恢复前提高磁盘和活动文件大小监控等级。
- 不同时启动第二个周期调度 sidecar，以免形成轮转竞争。

### 10.3 撤销 sidecar 方案

按以下顺序回滚，避免留下无人管理的固定文件：

1. 将 API 切换为 `LOG_WRITE_TO_FILE=false`、`LOG_WRITE_TO_CONSOLE=true`，重新创建 API 容器。
2. 确认 Docker logging driver 正常接收并轮转应用日志。
3. 停止并移除 logrotate sidecar，但保留宿主机日志目录和 state volume。
4. 归档或删除历史日志属于破坏性数据操作，必须由运维人员另行确认，不纳入自动回滚。

## 11. 风险与演进触发条件

| 风险 | 当前控制 | 需要升级方案的触发条件 |
| --- | --- | --- |
| `copytruncate` 窗口内丢日志 | 缩短检查和复制时间，明确非审计用途 | 日志成为审计、计费或合规依据 |
| 多 Worker 日志无严格全局顺序 | JSON 时间戳和 trace ID 关联 | 需要严格事件序列或跨服务检索 |
| sidecar 停止后活动文件增长 | 重启策略、状态监控、磁盘告警 | 无法可靠监控单机 sidecar |
| root sidecar 可修改 bind mount | 最小挂载、无密钥、无 Docker socket | 安全基线要求所有容器非 root |
| 单机日志随主机故障丢失 | 明确固定目录只提供单机持久化 | 需要跨节点、异地或长期保留 |
| 未来 API 容器扩为多个副本 | 当前禁止 sidecar 随副本扩容 | 需要多容器横向扩展或迁移 Kubernetes |

出现审计级可靠性、多主机扩展或 Kubernetes 迁移任一条件时，应切换为 stdout/stderr + 节点级单一采集器 +
集中日志后端，不继续扩展共享文件和 logrotate sidecar。

## 12. Definition of Done

- [x] `compose.prod.yaml` 能通过 Compose 解析，并且不包含真实密钥。
- [x] API 使用显式、可配置的多 Worker 启动方式，sidecar 保持单副本。
- [x] API 仅追加固定 `app.log`，没有新增 Loguru rotation、retention 或 compression。
- [x] sidecar 镜像可重复构建，基础镜像使用固定版本和 digest。
- [x] sidecar 的调度、state 持久化、权限和 SIGTERM 行为已验证。
- [x] Linux bind mount 环境中，轮转前后所有 Worker 均可继续写入合法 JSON Lines。
- [x] API 和 sidecar 重建后，活动日志、历史日志与轮转状态符合预期。
- [x] sidecar 故障不会直接终止 API，且文档已定义对应的磁盘增长监控项。
- [x] README、日志部署文档和计划状态已与当前实现同步。
- [x] 已记录 `copytruncate` 非严格零丢失和 Kubernetes 不沿用本 sidecar 的边界。
