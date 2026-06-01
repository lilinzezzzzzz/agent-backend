# FastAPI Backend

一个基于 FastAPI 的后端工程模板，覆盖了常见的 Web API、认证、异步数据库访问、Redis 缓存、Celery 任务调度，以及可复用的向量检索与第三方登录能力。

当前仓库更接近“可直接扩展的后端基础设施骨架”，而不是单一业务服务。README 只描述仓库中可以直接验证的现状，细分能力放在 `docs/` 和 `pkg/` 子模块文档里。

## 技术栈

- FastAPI + Starlette
- Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x Async ORM
- Redis
- Celery + Beat
- anyio
- Loguru
- uv
- 向量能力：`zvec`、`pymilvus`

## 当前能力

- 提供基于 `main.py` 的 FastAPI 应用入口
- 通过 `configs/.secrets` + `configs/.env.{APP_ENV}` 加载配置
- 支持 MySQL、PostgreSQL、Oracle 三种数据库连接串生成
- 内置 Redis 连接、Token 校验、签名认证和请求日志中间件
- 提供基础认证接口：登录、注册、登出、当前用户信息
- 提供 Celery Worker 与 Beat 调度骨架
- 提供向量抽象层，以及 `Milvus` / `zvec` 两套 backend
- 提供微信第三方登录接入点和扩展文档

## 环境要求

- Python `3.12+`
- `uv`
- Redis
- MySQL / PostgreSQL / Oracle 任选其一

如果要使用向量检索或相关测试，还需要额外准备对应向量存储环境，例如 Milvus。

## 快速开始

### 1. 安装依赖

开发环境建议安装 `dev` 组依赖：

```bash
uv sync --group dev
```

生产环境只安装运行时依赖：

```bash
uv sync --frozen
```

### 2. 准备配置

项目启动时会先读取 `configs/.secrets` 中的 `APP_ENV`，再加载对应的 `configs/.env.{APP_ENV}`，最后由 `.secrets` 覆盖同名配置。

初始化方式：

```bash
cp configs/.secrets.example configs/.secrets
```

最小必填配置分为两类。

`configs/.secrets`：

```env
APP_ENV=local
AES_SECRET=your_aes_secret_key
JWT_SECRET=your_jwt_secret_key
DB_PASSWORD=your_db_password
REDIS_PASSWORD=
```

`configs/.env.local`：

```env
DEBUG=true
LOG_FORMAT=text
CONFIG_ECHO_REVEAL_SECRETS=false
JWT_ALGORITHM=HS256

DB_TYPE=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USERNAME=root
DB_DATABASE=app_db

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
```

说明：

- 仓库已经提供 `configs/.env.local`、`configs/.env.dev`、`configs/.env.test`、`configs/.env.prod`
- 数据库、Redis 等密码类配置放在 `configs/.secrets`，不要写入 `configs/.env.*`
- `CONFIG_ECHO_REVEAL_SECRETS=true` 会在配置回显中显示敏感值，仅限本地诊断临时使用
- 配置优先级为初始化参数 > `configs/.env.{APP_ENV}` / `configs/.secrets` > 外部 shell 环境变量；shell 环境变量只作为缺省兜底，不覆盖配置文件
- 如果 `configs/.secrets` 缺失，或 `APP_ENV` 对应的 `.env` 文件不存在，应用会在启动阶段直接失败
- `DB_PASSWORD`、`DB_READ_PASSWORD`、`REDIS_PASSWORD` 支持 `ENC(...)` 格式，运行时会用 `AES_SECRET` 解密

### 3. 启动 API 服务

开发模式：

```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

非热重载模式：

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

当 `DEBUG=true` 时，FastAPI 文档地址可用：

- `/docs`
- `/redoc`

### 4. 启动 Celery Worker / Beat

推荐直接使用 `uv run`：

```bash
uv run celery -A internal.utils.celery.celery_app worker -l info -Q default,celery_queue,cron_queue
uv run celery -A internal.utils.celery.celery_app beat -l info
```

也可以使用仓库脚本启动 Worker：

```bash
./scripts/run_celery_worker.sh
```

脚本支持以下环境变量：

- `CELERY_LOG_LEVEL`
- `CELERY_CONCURRENCY`
- `CELERY_QUEUES`

## API 与认证约定

当前路由前缀如下：

| 前缀 | 说明 | 默认认证策略 |
| --- | --- | --- |
| `/v1` | 业务 API | Token |
| `/v1/public` | 公共接口 | 无 |
| `/v1/internal` | 内部接口 | 签名认证 |

当前仓库中已实现或已挂载的典型接口包括：

- `/v1/auth/login`
- `/v1/auth/register`
- `/v1/auth/logout`
- `/v1/auth/me`
- `/v1/auth/wechat/login`
- `/v1/user/hello-world`
- `/v1/public/test/*`

认证中间件的默认规则：

- `/v1/public/**` 直接放行
- `/v1/internal/**` 走签名认证
- 其他 HTTP 接口默认要求 `Authorization` Token
- 白名单中包含 `/auth/login`、`/auth/register`、`/auth/wechat/login`、`/docs`、`/openapi.json`

统一成功响应由 `pkg.toolkit.response` 提供，结构为：

```json
{
  "code": 20000,
  "message": "",
  "data": {}
}
```

## 项目结构

```text
.
├── main.py                     # 应用入口
├── internal/                   # 业务应用代码
│   ├── app.py                  # FastAPI 应用组装与 lifespan
│   ├── config.py               # 配置加载与 Settings
│   ├── controllers/            # API 路由
│   │   ├── api/                # /v1
│   │   ├── public/             # /v1/public
│   │   └── internal/           # /v1/internal
│   ├── middlewares/            # 认证、请求记录等中间件
│   ├── infra/                  # 数据库、Redis 连接初始化
│   ├── services/               # 业务逻辑
│   ├── dao/                    # 数据访问层
│   ├── models/                 # ORM 模型
│   ├── schemas/                # 请求响应 Schema
│   ├── tasks/                  # Celery 任务与调度表
│   └── utils/                  # 应用内工具
├── pkg/                        # 可复用基础包
│   ├── database/               # ORM 基础设施
│   ├── logger/                 # 日志初始化
│   ├── crypter/                # AES 加解密
│   ├── oss/                    # OSS / S3 抽象
│   ├── third_party_auth/       # 第三方登录策略
│   ├── toolkit/                # 通用工具集
│   └── vectors/                # 向量检索抽象与后端实现
├── configs/                    # 环境配置与密钥文件
├── docs/                       # 补充文档
├── scripts/                    # 辅助脚本
├── tests/                      # 测试
├── ddl/                        # DDL SQL
└── dml/                        # DML SQL
```

## 向量与扩展能力

仓库内已经包含通用向量抽象和两个 backend：

- `pkg.vectors.backends.milvus`
- `pkg.vectors.backends.zvec`

如果你要接入 RAG、检索增强问答或混合检索，建议先读对应子文档：

- `pkg/vectors/backends/milvus/README.md`
- `pkg/vectors/backends/zvec/README.md`

第三方登录目前以微信登录为主，详细接入说明见：

- `docs/third_party_login_guide.md`

## 测试与质量检查

运行测试：

```bash
uv run pytest
```

运行单个测试文件：

```bash
uv run pytest tests/api/test_auth.py -v
```

运行向量相关测试前，请先准备对应外部依赖，例如 Redis、Milvus 或其他测试所需服务。

代码质量检查：

```bash
uv run ruff check .
uv run ruff format .
uv run mypy .
```

## Docker

```bash
docker build -t fastapi-backend .
docker run --rm -p 8000:8000 fastapi-backend
```

容器运行前仍需保证配置文件和外部依赖可用。

## 相关文档

- `docs/auth_module_guide.md`
- `docs/third_party_login_guide.md`
- `docs/uv_use_guide.md`
- `docs/dataclass_use_guide.md`
- `docs/md_use_guide.md`

## License

MIT
