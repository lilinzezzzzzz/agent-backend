# 数据库迁移指南

PostgreSQL 的建库和升级由 [Alembic revision](../../migrations/versions/) 管理。
[ORM 模型](../../internal/models/) 描述当前期望结构，revision 固化每一步结构变化，
仓库不维护完整建库 SQL 快照；需要 SQL 时从迁移历史按需生成。

当前 initial revision 为 `20261104_0001`，创建现有 11 张业务表、约束、索引和注释。
本次接入只覆盖全新数据库，不提供已有数据库的差异核对、版本登记或数据迁移流程。
仓库按脚手架维护，不增加历史实现兼容层。
项目仅支持 PostgreSQL 与 SQLite；SQLite 限于测试和轻量本地存储。
本目录 Alembic 迁移、部署及强并发能力仍只支持 PostgreSQL，在线迁移遇到 SQLite 会明确失败。

## 配置与新库初始化

所有命令在仓库根目录运行。先执行 `uv sync --group dev`；运行镜像只安装运行时依赖时也包含 Alembic。

先在 PostgreSQL 创建一个**空数据库**，使用现有配置入口指定目标：

- 根目录 `.env` 的 `APP_ENV` 选择 `configs/app.{APP_ENV}.toml`。
- TOML 的 `[db]` 声明主库地址、端口、用户名和库名，密码来自根目录 `.secrets` 的 `DB_PASSWORD`。
- 在线命令通过 `internal.config.get_settings()` 使用完整应用配置校验，因此其他必填配置也必须齐全。
  迁移不连接 Redis、Milvus、模型服务或只读副本。
- `alembic.ini` 不保存连接串；不新增 `DATABASE_URL` 或其他数据库配置环境变量。

配置入口统一构造 SQLAlchemy 原生 `URL` 对象：主库属性 `sqlalchemy_database_uri` 返回 `URL`，
只读副本属性 `sqlalchemy_read_database_uri` 返回 `URL | None`。对象直接交给 engine 或 Alembic，
用户名、密码、库名和 SQLite 文件名中的特殊字符不需要手工 URL 编码，也不要先渲染成字符串再传回 engine。

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

## SQLite 轻量本地存储

保留现有应用配置的其他必填字段，仅将数据库分组替换为：

```toml
[db]
type = "sqlite"
database = "runtime/local.sqlite3"
echo = false
```

`database` 可用 `:memory:`、绝对路径或相对路径；相对路径统一基于仓库根目录解析。
SQLite 文件名中的 `?`、`#`、百分号、空格和中文保持字面值，不会被当作连接查询参数。
文件名仍须符合宿主操作系统规则，例如 Windows 不允许 `?`。

文件模式需先创建父目录（例如 `mkdir -p runtime`）；SQLite 不使用 host、port、username 或数据库密码，
不支持 `db.read_replica`。连接使用 `sqlite+aiosqlite`，内存模式使用方言默认连接池。
配置仍由 `.env` 选择 TOML 和根 `.secrets` 加载，不新增连接串环境变量；SQLite 的 `DB_PASSWORD=` 可留空，
密钥模板中的键仍需保留；SQLite 不使用网络数据库凭据，
其他应用功能的配置与凭据要求不变。

SQLite 必须关闭现有 `[celery]` 分组中的这两个 PostgreSQL 专用开关，保留该分组的其余必填参数：

```toml
[celery]
idempotency_enabled = false
reconciler_beat_enabled = false
```

配置校验会拒绝 SQLite 下启用这些能力。SQLite 适合隔离测试中的 ORM/DAO 和本地轻量数据读写；
测试或独立本地脚本可通过 ORM metadata 初始化自己新建的数据库。API/Worker 启动不会自动建表，
不要对 SQLite 运行本仓库的 PostgreSQL initial revision，也不能把 SQLite 测试作为行锁、
`SKIP LOCKED`、多实例并发或持久化部署的验收。

`JSONType` 在 PostgreSQL 使用 JSONB，在 SQLite 使用原生 JSON；保留 `none_as_null`、JSON null、
顶层 Mutable 追踪及 SQLite 历史双重编码读取行为。其他方言被明确拒绝，没有 TEXT/CLOB 降级路径。
SQLite 文件不作为业务数据快照提交到仓库。

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
6. 检查迁移图与离线 SQL contract：

   ```bash
   uv run alembic heads
   uv run pytest tests/orm/test_postgresql_ddl.py tests/dao/test_celery_task_dao.py -q
   ```

revision 只使用当时固化的 SQLAlchemy 类型与操作，不导入当前模型或调用 `Base.metadata.create_all()`。
`env.py` 会把应用的 `JSONType` 渲染成 PostgreSQL `JSONB`，使历史 revision 不依赖应用类型实现。
已在共享环境执行的 revision 不改写；修正通过新增 revision 完成。

仓库要求单一 head。并行开发合并后先检查 `uv run alembic heads`；出现多 head 时审查变更顺序和冲突，
按实际依赖创建合并 revision，不随意修改已执行 revision 的 `down_revision`。

`alembic check` 比较数据库与 ORM，启用类型与 server default 比较，但沿用 autogenerate 的检测限制。
它不能证明数据回填正确，也不能全面检查所有 CHECK、主键等约束变化；相关变更需额外测试。

## DDL、DML 的生成与导出

DDL 是表、列、索引、约束等结构变更；DML 是业务数据的插入、更新和删除。两者都可以写入 revision，
再由 Alembic 在线执行或离线生成 SQL。`--autogenerate` 只辅助生成结构变更候选，不会推断业务数据修复。

### 输出目录约定

按用途将生成文件放在 `migrations/exports/`，不要放进 `migrations/versions/`：

```text
migrations/
  versions/       # 提交到 Git 的 revision，唯一的版本化执行依据
  exports/        # 按需生成的 SQL，已被 Git 忽略
    ddl/          # 结构变更为主的 SQL
    dml/          # 数据变更为主的 SQL
```

导出前按需创建目录；无需提交空目录或 `.gitkeep`。生成文件用于本地审阅或单独交付，不手工维护，
也不随每次 migration 更新提交快照。正式交付应使用明确的起止 revision 命名，记录适用数据库版本与执行条件。
不要把真实业务数据、凭据或数据库导出放入这些文件。

目录名只表示用途，**不会让 Alembic 自动分离 DDL 和 DML**。所选范围内的全部迁移操作、事务边界及
`alembic_version` 登记都会输出；即使结构迁移也会包含版本登记的 DML。混合迁移应整体审阅和执行，
不要用文本过滤拆分 SQL，以免破坏执行顺序或版本登记。

### DDL：生成结构迁移并导出

先按“开发结构变更”一节，在隔离开发数据库上升级到当前 head、修改 ORM，再生成候选 revision：

```bash
uv run alembic revision --autogenerate -m "describe schema change"
```

这一步需要 PostgreSQL 连接，仍通过 `.env`、`configs/app.{APP_ENV}.toml` 和 `.secrets` 加载配置。
审查生成内容后再执行迁移；不在 `alembic.ini` 中添加连接串，也不新增 `DATABASE_URL` 等环境变量入口。

离线导出则不读取应用配置或凭据，不连接数据库。下面的命令可直接用于当前仓库，将 base 到 head 的
完整 SQL 保存到输出目录：

```bash
mkdir -p migrations/exports/ddl
uv run alembic upgrade base:head --sql > migrations/exports/ddl/base-to-head.sql
```

`head` 会随新增 revision 改变。当前 head 是 `20261104_0001`，固定版本的等价示例为：

```bash
uv run alembic upgrade base:20261104_0001 --sql > migrations/exports/ddl/base-to-20261104_0001.sql
```

从 base 生成的完整 SQL 仅适用于空数据库，不能对已有库重复执行。若只审阅某段增量，明确指定起止版本；
下面的 `START_REV`、`END_REV` 是占位符，必须先替换为迁移历史中的实际 revision ID：

```bash
uv run alembic upgrade START_REV:END_REV --sql > migrations/exports/ddl/START_REV-to-END_REV.sql
```

范围不重复执行起点 revision，而是从其后推进到终点。离线模式不读取目标数据库的 `alembic_version`，
不能自动判断应从哪个版本开始。执行交付 SQL 前必须另行核对目标库版本与结构；当前仍不提供已有数据库的自动接管。

### DML：编写数据迁移并导出

与 schema 版本绑定、范围明确的小规模参考数据或数据修正，可以创建不带 `--autogenerate` 的 revision：

```bash
uv run alembic revision -m "normalize demo username"
```

这个命令只生成 migration 文件，数据逻辑需要手写。保留生成文件的 revision 元数据，并实现
`upgrade()`、`downgrade()`。例如，下面使用当前 `user.username` 字段演示一条固定条件的数据更新；
**仅用于隔离测试库教学，不是当前项目需要执行的数据修复，也不包含真实用户数据**：

```python
import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    user_table = sa.table("user", sa.column("username", sa.String(length=64)))
    op.execute(
        user_table.update()
        .where(user_table.c.username == op.inline_literal(" demo_user "))
        .values(username=op.inline_literal("demo_user"))
    )


def downgrade() -> None:
    raise NotImplementedError("该示例未保留原始值，不支持自动回退")
```

`sa.table()` 在 migration 中固化所需字段，不依赖当前应用 ORM；SQLAlchemy 会处理 PostgreSQL
`user` 表名的引用。`op.inline_literal()` 使固定值能渲染到离线 SQL 中。少量固定行插入也可使用
`op.bulk_insert()`，但须处理重复执行、约束及回退语义，不能把真实用户或凭据写进 revision。
上述回退明确拒绝执行，因为无法区分原有的 `demo_user` 与更新后的值；实际迁移需单独设计可恢复性。

确认数据 revision 的父版本与自身 ID 后导出。下面的 `PARENT_REV`、`DATA_REV` 同样必须替换为实际 ID：

```bash
mkdir -p migrations/exports/dml
uv run alembic upgrade PARENT_REV:DATA_REV --sql > migrations/exports/dml/PARENT_REV-to-DATA_REV.sql
```

该范围只选择父版本之后到数据版本的变更；如果只有一条数据 revision，输出主要是它的 DML 与版本登记。
当前仓库只有 initial schema revision，尚无业务数据 migration；需要先编写并验证数据 revision，
才能导出相应的业务 DML。

### 适用边界与执行方式

| 需求 | 处理方式 |
| --- | --- |
| 新库初始化及后续版本升级 | 标准入口为 `uv run alembic upgrade head`，显式配置并核对目标数据库 |
| 审阅或交付结构变更 | 按明确 revision 范围离线导出到 `migrations/exports/ddl/` |
| 随版本交付的小规模数据变更 | 手写 data revision，离线导出到 `migrations/exports/dml/` 或在线随版本执行 |
| 开发或测试样例数据 | 使用 fixture 或明确的初始化命令，不作为所有环境都必须执行的 migration |
| 大批量回填、长时间数据修复 | 使用独立的有界、可续跑任务，明确锁、审计、幂等、失败恢复与回滚，不塞进长事务 migration |
| 读取现有数据后再计算修复结果 | 需要在线连接或独立脚本；普通离线导出无法执行 SELECT 并取得结果 |

`--sql` 会运行 revision 中的 Python 代码，因此新增 migration 也必须兼容离线模式；不能只依赖
`op.get_bind().execute(...).fetchall()` 等查询结果。固定 SQL 可以通过 `op.execute()` 输出，参数值
需可安全内联；不要把“成功导出 SQL”视为数据修复或真实数据库执行已通过。

离线 SQL 不会备份数据，也不是现有数据的 INSERT dump。若选择交付 SQL 执行，不要再把同一脚本作为
第二套初始化步骤；其版本登记完成后，后续仍由 Alembic 管理。增量升级、回退与数据迁移需在匹配目标版本的
隔离 PostgreSQL 中验证。导出命令失败时可能留下空文件或部分 SQL，必须检查命令退出状态并完整审阅后再交付。

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

不依赖数据库的检查包括单一 base / head、模型注册完整性、离线运行、事务边界和版本登记：

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

结构一致性在真实 PostgreSQL 中验证：一个测试数据库执行在线迁移或按需生成的离线 SQL，另一个数据库从当前
注册的 ORM 模型创建期望结构。比较最终的表、列类型、nullable、server default、主键、唯一约束、
CHECK、外键、索引和注释；不依赖历史 `CREATE TABLE` 文本，也不禁止迁移 SQL 中的 `ALTER` / `DROP`。

集成测试覆盖：

- 空库升级、重复升级保留数据、User ORM / DAO 读写、动态 head 核对、漂移检测、回退到 base 后再升级。
- Alembic 按需生成的离线 SQL 实际建库，以及 autogenerate / revision 模板 / JSONB 渲染。
- 在临时复制的迁移目录中，向当前 head 追加测试专用 revision，验证跨版本升级、回退、再升级的数据保留，
  以及包含增量 DDL 的完整 SQL 能建成与目标 ORM 一致的最终结构。
- 在测试 revision 已执行 `ADD COLUMN` 或 `DROP COLUMN` 后注入故障，核对表结构、版本登记和原有数据
  都随事务回滚，并验证失败后可以重新执行。回退失败还检查被删除列中已写入的值确实恢复。

故障开关仅存在于测试临时生成的 revision 中，不进入应用迁移。head 从迁移图动态读取，新增正常
revision 不需要手工更新测试中的版本号。
未提供开关时集成测试明确跳过；缺少 Docker CLI 或 daemon 时也会说明原因，镜像需提前准备。

CI 可复用上述一次性容器测试；本次不新增 CI 平台配置。面向具体变更，还需在与目标环境匹配的
PostgreSQL 版本上验证历史数据、锁和部署顺序。

### 2026-11-12 本地集成验证记录

本次在一次性 PostgreSQL 容器上实际执行了完整迁移集成文件，结果如下：

| 项目 | 实测记录 |
| --- | --- |
| 测试日期 | 2026-11-12（Asia/Shanghai） |
| 测试提交 | `706adad73b7a54da1650ca01a924608b1df99351` |
| 测试文件 | [`tests/orm/test_migrations_integration.py`](../../tests/orm/test_migrations_integration.py) |
| 环境 | 本地 macOS，Docker Engine `29.9.3`，本地 `postgres:17` 镜像 |
| PostgreSQL 镜像版本 | `PG_VERSION=17.11-1.pgdg13+2`（镜像元数据） |
| 结果 | **9 passed，0 failed，0 skipped** |
| 耗时 | **48.62 秒** |

使用的本地镜像 ID 为 `sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1`。
以下为仓库根目录的复现命令；本次执行额外指定了独立的 pytest 临时目录：

```bash
uv run --no-sync --group dev pytest \
  tests/orm/test_migrations_integration.py \
  --run-postgresql-migrations -q -ra -x
```

本次通过的范围：

- **7 项迁移用例**：空库升级、重复升级与数据保留、ORM/DAO 读写和漂移检测、离线 SQL 建库、
  跨版本升级/回退与数据保留、升级及回退失败时的结构/版本/数据回滚，以及 autogenerate、revision 模板和 JSONB 渲染。
- **2 项事务锁竞争用例**：分别覆盖锁身份行首次插入尚未提交、身份行已提交后再次持锁两种场景。
  竞争者的 TRY 获取返回 `False` 后仍能执行 `SELECT 1`，`lock_timeout` 恢复为原配置 `0`；
  原持有者回滚释放锁后，竞争者重试获取成功。

**隔离确认**：每个用例创建一个随机命名的 `agent-backend-migrations-<uuid>` 容器，共 9 个。
容器均仅发布到 `127.0.0.1` 的随机端口；已核对 9 份临时 TOML 使用 9 个不同的 loopback 端口、
测试专用数据库与凭据。Alembic CLI 使用临时配置根目录，直接连接和 ORM 结构比较也只使用测试容器；
未连接任何现有数据库，未使用工作区数据库连接配置或真实数据库凭据。

**清理确认**：除 fixture 的 `--rm` 和删除自身 volume 的清理流程外，还读取 Docker 事件核对了销毁结果：
本次 **9 个测试容器和对应的 9 个匿名 volume 均已销毁**；按测试名称前缀查询无容器残留。
本次测试没有修改代码，结束时工作区和暂存区干净；临时配置、日志与测试产物随后已清理。

该记录补齐了上述提交在该 PostgreSQL 镜像上的迁移与 TRY 锁竞争验证，不包含现有数据库接管、
生产数据回填、生产部署或其他外部服务的验收，也不代表所有 PostgreSQL 版本或 WAIT 模式阻塞竞争都已验证。

官方说明：[Autogenerate 与检测限制](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)、
[异步引擎接入](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)、
[离线 SQL](https://alembic.sqlalchemy.org/en/latest/offline.html)、
[数据迁移](https://alembic.sqlalchemy.org/en/latest/cookbook.html#data-migrations-general-techniques)。
