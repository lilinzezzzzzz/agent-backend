# 数据库迁移指南

PostgreSQL 的建库和升级由 [Alembic revision](../../migrations/versions/) 管理。
[ORM 模型](../../internal/models/) 描述当前期望结构，revision 固化每一步结构变化，
[`ddl/postgresql/init.sql`](../../ddl/postgresql/init.sql) 是从迁移历史导出的 SQL，不再独立手工维护。

当前 initial revision 为 `20261104_0001`，创建现有 11 张业务表、约束、索引和注释。
本次接入只覆盖全新数据库，不提供已有数据库的差异核对、版本登记或数据迁移流程。
仓库按脚手架维护，不增加历史实现兼容层。
配置层虽可构造 MySQL DSN，但本目录迁移只支持 PostgreSQL，在线命令遇到其他方言会明确失败。

## 配置与新库初始化

所有命令在仓库根目录运行。先执行 `uv sync --group dev`；运行镜像只安装运行时依赖时也包含 Alembic。

先在 PostgreSQL 创建一个**空数据库**，使用现有配置入口指定目标：

- 根目录 `.env` 的 `APP_ENV` 选择 `configs/app.{APP_ENV}.toml`。
- TOML 的 `[db]` 声明主库地址、端口、用户名和库名，密码来自根目录 `.secrets` 的 `DB_PASSWORD`。
- 在线命令通过 `internal.config.get_settings()` 使用完整应用配置校验，因此其他必填配置也必须齐全。
  迁移不连接 Redis、Milvus、模型服务或只读副本。
- `alembic.ini` 不保存连接串；不新增 `DATABASE_URL` 或其他数据库配置环境变量。

确认目标库为空且配置正确后执行：

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

`upgrade head` 执行迁移并登记 `alembic_version`，重复执行到同一个 head 不重建表。
`current` 显示当前 revision，`check` 成功时报告没有新的 upgrade operations。
迁移用户需要目标 schema 的建表、索引、约束和注释等 DDL 权限。

迁移由一个独立步骤串行执行，完成后再启动依赖该结构的 API、Worker 和 Beat。
应用启动不自动迁移，也不负责并发迁移协调。这里不增加部署编排或执行远端数据库变更。

已有表的数据库不适用上述初始化流程；不要直接运行 initial revision，也不要通过 `stamp head`
声明它已经一致。已有数据库的接管需要单独核对结构、历史数据和兼容要求。

## 开发结构变更

1. 在专用开发数据库上先执行 `uv run alembic upgrade head`，使数据库处于当前 revision。
2. 修改 `internal/models/`；新增模型在 `internal/models/__init__.py` 注册，保证 metadata 包含全部业务表。
3. 生成候选迁移：

   ```bash
   uv run alembic revision --autogenerate -m "describe schema change"
   ```

4. 审查并补齐 `upgrade()` 和 `downgrade()`。表或列重命名需要显式保留数据；默认值、约束、JSON 类型、
   历史数据回填和升级顺序均不能只依赖 autogenerate。涉及已有数据时评估锁表、混合版本和有损回退。
5. 在隔离 PostgreSQL 中执行升级、`alembic check` 及相关 ORM / DAO 测试；可逆变更还需演练回退和再升级。
6. 更新 SQL 导出并检查改动：

   ```bash
   uv run python scripts/export_postgresql_schema.py
   uv run pytest tests/orm/test_postgresql_ddl.py -q
   ```

revision 只使用当时固化的 SQLAlchemy 类型与操作，不导入当前模型或调用 `Base.metadata.create_all()`。
`env.py` 会把应用的 `JSONType` 渲染成 PostgreSQL `JSONB`，使历史 revision 不依赖应用类型实现。
已在共享环境执行的 revision 不改写；修正通过新增 revision 完成。

仓库要求单一 head。并行开发合并后先检查 `uv run alembic heads`；出现多 head 时审查变更顺序和冲突，
按实际依赖创建合并 revision，不随意修改已执行 revision 的 `down_revision`。

`alembic check` 比较数据库与 ORM，启用类型与 server default 比较，但沿用 autogenerate 的检测限制。
它不能证明数据回填正确，也不能全面检查所有 CHECK、主键等约束变化；相关变更需额外测试。

## 离线 SQL

导出当前完整建库 SQL，无需 `.env`、`.secrets` 或数据库连接：

```bash
uv run python scripts/export_postgresql_schema.py
```

导出脚本包含固定生成说明、从 base 到 head 的 SQL、事务边界和 Alembic 版本登记，并移除行尾空格，
便于 Git review。测试会检查提交的 SQL 与导出结果完全一致。
需要导出到其他位置时使用 `--output /path/to/init.sql`。

也可直接查看 Alembic 原始离线输出：

```bash
uv run alembic upgrade head --sql
```

完整导出仅适用于空数据库。执行完整 SQL 与 `alembic upgrade head` 是两种建库入口，只选一种；
SQL 已登记版本，之后可以继续通过 Alembic 升级。不要对已有库重复执行完整 SQL。

## 回退边界

initial revision 的 `downgrade()` 删除全部 11 张业务表及其数据，仅在可丢弃的隔离测试数据库中演练：

```bash
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic check
```

这个回退不恢复被删除的数据，也不等于业务恢复或生产回滚方案。
之后的迁移应说明可逆范围；大批量数据回填需要单独设计分批、续跑和验证方式。

## 验证

不依赖数据库的检查包括单一 base / head、模型注册完整性、离线运行和可重复导出：

```bash
uv run pytest tests/orm/test_postgresql_ddl.py -q
```

集成测试需要运行中的 Docker daemon 和本地 `postgres:17` 镜像。先准备镜像，再显式运行：

```bash
docker pull postgres:17
uv run pytest tests/orm/test_migrations_integration.py --run-postgresql-migrations -q
```

测试创建只绑定本机随机端口的一次性容器，在 pytest 临时目录写入专用 TOML 和测试凭据，
通过真实配置加载器执行 Alembic CLI，不使用工作区 `.env`、`.secrets` 或任何已有数据库。
密码包含 URL 特殊字符，以验证凭据不会经过 INI 插值。测试结束后清理自身容器和匿名 volume。

结构一致性在真实 PostgreSQL 中验证：一个测试数据库执行完整迁移或导出 SQL，另一个数据库从当前
注册的 ORM 模型创建期望结构。比较最终的表、列类型、nullable、server default、主键、唯一约束、
CHECK、外键、索引和注释；不依赖历史 `CREATE TABLE` 文本，也不禁止迁移 SQL 中的 `ALTER` / `DROP`。

集成测试覆盖：

- 空库升级、重复升级保留数据、User ORM / DAO 读写、动态 head 核对、漂移检测、回退到 base 后再升级。
- 仓库 SQL 导出实际建库，以及 autogenerate / revision 模板 / JSONB 渲染。
- 在临时复制的迁移目录中，向当前 head 追加测试专用 revision，验证跨版本升级、回退、再升级的数据保留，
  以及包含增量 DDL 的完整 SQL 能建成与目标 ORM 一致的最终结构。
- 在测试 revision 已执行 `ADD COLUMN` 或 `DROP COLUMN` 后注入故障，核对表结构、版本登记和原有数据
  都随事务回滚，并验证失败后可以重新执行。回退失败还检查被删除列中已写入的值确实恢复。

故障开关仅存在于测试临时生成的 revision 中，不进入应用迁移。head 从迁移图动态读取，新增正常
revision 不需要手工更新测试中的版本号。
未提供开关时集成测试明确跳过；缺少 Docker CLI 或 daemon 时也会说明原因，镜像需提前准备。

CI 可复用上述一次性容器测试；本次不新增 CI 平台配置。面向具体变更，还需在与目标环境匹配的
PostgreSQL 版本上验证历史数据、锁和部署顺序。

官方说明：[Autogenerate 与检测限制](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)、
[异步引擎接入](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)。
