# AGENTS.md

`migrations/versions/` 是 PostgreSQL 建库和升级的版本化执行依据。

- revision 固化当时的表、列、索引和约束，不导入应用模型、应用配置或可变的自定义类型实现，
  不调用当前 `Base.metadata.create_all()`；自定义 JSON 类型通过 `env.py` 渲染为 PostgreSQL `JSONB`。
- 新模型必须在 `internal/models/__init__.py` 注册。审查自动生成内容，尤其是重命名、数据回填、默认值、
  约束和有损回退；保持单一 head，不能通过改写已在共享环境执行的历史 revision 消除分叉。
- 在线迁移沿用 `internal.config` 的主库配置；不另设连接串环境变量或在 `alembic.ini` 保存凭据。
- 迁移是独立命令，不挂到 API / Worker lifespan。新库升级、可逆变更回退和结构比较在隔离 PostgreSQL
  中验证；测试不读取工作区的真实数据库配置。
- 修改 revision 后使用 `uv run python scripts/export_postgresql_schema.py` 更新 SQL 导出产物；
  具体开发与验证命令见 `docs/development/database_migrations.md`。
