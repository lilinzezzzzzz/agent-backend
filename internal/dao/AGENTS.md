# AGENTS.md

适用于 `internal/dao/`。

## 层职责

DAO 层封装 ORM 数据访问：包含持久化查询、写入和基础数据访问策略。业务规则留在 Service 层。

Redis / 缓存访问不放在本层，见 `internal/cache/AGENTS.md`。

## 编码约定

- ORM DAO 优先继承 `pkg.database.dao.BaseDao`。
- 模型类型通过 `_model_cls` 声明。
- 读操作优先从 `select_stmt()` 或 `count_stmt()` 获取已应用基础策略的 statement，再使用原生 SQLAlchemy
  API 追加业务条件、排序和分页。
- 写后读一致性需要时显式使用写库 `session_provider`。
- DAO 方法返回 ORM model、基础值或明确类型，不返回含义不清的临时 dict。
- 通用分层命名规则见根目录 `AGENTS.md`，DAO 层不重复定义。

## 代码最小正确形态

一个合格 ORM DAO 的最小形态：

```python
from internal.infra.database import get_read_session, get_session
from internal.models.user import User
from pkg.database.dao import BaseDao


class UserDao(BaseDao[User]):
    _model_cls: type[User] = User

    async def get_by_id(self, user_id: int) -> User | None:
        statement = self.select_stmt().where(self.model_cls.id == user_id)
        return await self.fetch_one(statement)

    async def get_by_ids(self, user_ids: list[int]) -> list[User]:
        if not user_ids:
            return []
        statement = self.select_stmt().where(self.model_cls.id.in_(user_ids))
        return await self.fetch_all(statement)


# 全局单例（懒加载）
_user_dao: UserDao | None = None


def new_user_dao() -> UserDao:
    global _user_dao
    if _user_dao is None:
        _user_dao = UserDao(
            session_provider=get_session,
            read_session_provider=get_read_session,
        )
    return _user_dao
```

最低要求：

- DAO 只表达数据访问意图，不做业务状态判断。
- 查询字段通过 `self.model_cls` 引用，减少继承和测试替换风险。
- 单条查询返回 `Model | None`，批量查询返回 `list[Model]`。
- 批量接口接收集合参数，一次查询完成，不让调用方循环查库。
- DAO statement factory 只构造 statement；普通实体查询优先通过 `fetch_first()` / `fetch_one()` /
  `fetch_all()` 执行，调用方持有 session 时通过同名方法的 `session` 参数显式传入；`update_stmt()` 生成的
  单语句批量更新通过 `execute_update()` 执行，跨语句原子写入才使用显式事务 session。
- factory 按现有 `_xxx_dao` 懒加载单例模式实现，并绑定已有 session provider。
- 不在 DAO 内创建 engine/session。

## 数据库约束

“禁止在循环中执行 ORM/query/session 调用”、“避免 N+1”、“批量读写”等数据库硬约束见全局 `~/.qoder/AGENTS.md` Database / Performance 章节。项目特有约束：

- 列表查询必须考虑 limit、排序和必要的过滤条件。
- 不要在 DAO 中吞掉数据库异常；需要转换时保留足够上下文并交给 Service 决定业务错误。

## 验证重点

- DAO 变更优先使用隔离的数据库 fixture 覆盖查询条件、空结果、重复结果、软删除和批量行为。
