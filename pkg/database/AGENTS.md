# AGENTS.md

适用于 `pkg/database/`。

## 层职责

本目录提供 SQLAlchemy async 基础设施、模型 Mixin、DAO 基类和数据库类型封装。

## 编码约定

- 保持 SQLAlchemy 2.x typed API 风格。
- DAO statement factory 和 session provider 是共享基础设施，变更前全仓检索调用方。
- `BaseDao.select_stmt()`、`count_stmt()` 和 `update_stmt()` 只组合 SQL，不持有 session，也不执行 statement。
- 普通实体查询通过 `fetch_first()` / `fetch_one()` / `fetch_all()` 执行；需要自定义结果形态时直接通过
  `read_session_provider()` 获取 session 并执行 statement。`update_stmt()` 生成的单语句批量更新通过
  `execute_update()` 执行；需要跨语句原子性时才通过 `DAO.transaction()` 获取同一个 `AsyncSession`。
- 查询条件直接使用 ORM column expression；`update_stmt()` 必须拒绝未知或受管理字段，并要求至少一个显式
  业务条件，不能仅依赖软删除策略限定更新范围。
- 创建和更新审计统一传 `AuditActor`；无请求用户上下文时默认为 system，异步任务应显式使用 task
  或 service actor。
- 类型封装要兼容当前支持的 MySQL、PostgreSQL、Oracle，以及测试中的 SQLite。
- 当前脚手架不保留旧数据库基础 API 的兼容层；破坏性调整应一次性同步仓库内调用方和测试。DAO statement
  factory 直接返回 SQLAlchemy statement，不提供自定义 builder 或独立 executor 抽象；通用 `fetch_*` 与
  `execute_update()` 只封装 session 生命周期和结果提取，显式传入 session 时不得 commit、rollback 或关闭该
  session。

## 数据库约束

通用数据库硬约束（禁循环内 ORM 调用、批量优先、不吞异常）见全局 `~/.qoder/AGENTS.md` Database / Performance 章节。本包特有约束：

- 批量操作应提供明确的批量接口，不鼓励调用方逐条执行。

## 验证重点

- 运行 ORM 和数据库类型相关测试，例如 `tests/orm/`、`tests/test_json_type.py`。
- DAO 基类变更还要运行依赖 `BaseDao` 的业务 DAO 测试。
