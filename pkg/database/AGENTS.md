# AGENTS.md

`pkg/database/` 提供 SQLAlchemy async 基础设施、模型 Mixin、DAO 基类和数据库类型。

- 使用 SQLAlchemy 2.x typed API；statement factory 只组合 SQL，不持有 session 或执行语句。
- 普通实体查询使用现有 `fetch_*`，页码分页提供稳定唯一排序；复杂 join/聚合分页传入与列表语义一致的
  `count_statement`。
- `update_stmt()` 拒绝未知或受管理字段，并要求显式业务条件；批量场景提供批量接口，避免逐条 I/O。
- 调用方传入 session 时 helper 不得 commit、rollback 或关闭它；跨语句原子性由同一事务 session 保证。
- 仅支持 PostgreSQL 与 SQLite；JSON 类型使用 JSONB / JSON，不为其他方言降级为 TEXT/CLOB。
- 审计 actor、软删除和分页语义需兼容两种方言；SQLite 只验证基础存储行为，不替代 PostgreSQL 并发验证。
- 当前脚手架不维护已删除基础 API 的兼容层，但破坏性调整必须一次性更新仓库内调用方和测试。
