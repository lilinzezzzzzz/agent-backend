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

| 部署方式 | 应用输出 | 轮转和保留责任 |
| --- | --- | --- |
| 本地开发 | stderr + 固定 `app.log` | 不定时轮转，按需手动清理 |
| Docker / Docker Compose | stderr | Docker `local` logging driver |
| Linux 直接部署 | 固定 `app.log` | Linux `logrotate` |
| Docker Compose 且要求宿主机固定文件 | bind mount 后的固定 `app.log` | 宿主机 Linux `logrotate` |
| Kubernetes | stdout/stderr | 节点采集 Agent 和集中日志平台 |

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

## Linux logrotate

本方案适用于 Linux 直接部署，也适用于 Docker Compose 容器需要把日志以固定路径暴露到宿主机的场景。

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
