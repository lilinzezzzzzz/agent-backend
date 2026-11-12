# AGENTS.md

`migrations/versions/` 是 PostgreSQL 建库和升级的版本化执行依据。

- revision 固化当时的表、列、索引和约束，不导入应用模型、应用配置或可变的自定义类型实现，
  不调用当前 `Base.metadata.create_all()`；自定义 JSON 类型通过 `env.py` 渲染为 PostgreSQL `JSONB`。
- 新模型必须在 `internal/models/__init__.py` 注册。审查自动生成内容，尤其是重命名、数据回填、默认值、
  约束和有损回退；保持单一 head，不能通过改写已在共享环境执行的历史 revision 消除分叉。
- 在线迁移沿用 `internal.config` 的主库配置；不另设连接串环境变量或在 `alembic.ini` 保存凭据。
- 主库配置生成的 SQLAlchemy `URL` 对象直接传给 engine，不经过字符串渲染和 INI 插值；离线 SQL 分支
  不读取应用配置或凭据，不初始化外部连接。
- 迁移是独立命令，不挂到 API / Worker lifespan。新库升级、可逆变更回退和结构比较在隔离 PostgreSQL
  中验证；测试不读取工作区的真实数据库配置。
- 按需生成的 SQL 统一放在 `migrations/exports/ddl/` 或 `migrations/exports/dml/`，不提交到 Git，
  不放进 `versions/`，不作为独立维护的结构或数据来源。
- 导出目录只表示用途，不会自动拆分 DDL、DML 或版本登记；按明确 revision 范围整体审阅，不用文本过滤
  切开混合迁移。失败命令留下的部分 SQL 不得作为交付结果。
- 不维护独立建库 SQL 快照；需要审阅或交付 SQL 时，使用 `uv run alembic upgrade head --sql` 按需生成。
  修改 revision 后验证离线 SQL 与隔离数据库迁移；具体命令见 `docs/development/database_migrations.md`。
- PostgreSQL 集成验证沿用 `tests/orm/` 的显式开关及一次性 Docker 容器；新增 revision 的测试从迁移图解析
  head，不把当前 revision ID 或历史测试结果写成永久验收条件。
