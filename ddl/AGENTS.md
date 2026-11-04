# AGENTS.md

`ddl/` 保存按数据库方言组织的 schema 导出产物，业务 seed 和数据修复属于 `dml/`。

- 表、字段、索引、约束、默认值和 nullable 必须与 ORM、DAO 及测试 fixture 一致；不要把某一方言特性
  无条件复制到其他方言。
- PostgreSQL 的结构来源为 `migrations/versions/`；使用
  `uv run python scripts/export_postgresql_schema.py` 更新 `postgresql/init.sql`，不要直接编辑生成文件。
- SQL 包含建表和 Alembic 版本登记，只用于全新数据库；已有库接管需单独设计并授权，不使用 SQL 或
  `stamp head` 跳过差异核对。
- 不包含真实业务数据、凭据、生产连接信息或不可审计的手工修复语句。
- 基线变更至少验证隔离数据库可建库及相关 ORM / DAO 行为；增量迁移还需演练升级和回滚。
