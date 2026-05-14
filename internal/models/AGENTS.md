# AGENTS.md

适用于 `internal/models/`。

## 层职责

本层定义 SQLAlchemy ORM 模型和表结构映射，不承载业务流程。

## 编码约定

- 使用 SQLAlchemy 2.x typed patterns，例如 `Mapped[...]` 和 `mapped_column(...)`。
- 继承项目已有 Base/Mixin，保持 `id`、时间字段、软删除字段等约定一致。
- 字段类型、nullable、default、index、unique、constraint 要与数据库 DDL 保持一致。
- 表名、索引名和约束名要稳定，避免随意重命名。
- 模型中只保留与持久化强相关的轻量方法，不写依赖外部服务的业务逻辑。
- 通用分层命名规则见根目录 `AGENTS.md`，Model 层不重复定义。

## 代码最小正确形态

一个合格 ORM Model 的最小形态：

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from pkg.database.base import ModelMixin


class User(ModelMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
```

最低要求：

- 只描述表结构、字段约束、索引和必要关系。
- 字段类型、长度、nullable、default、unique、index 与 DDL/迁移一致。
- 不导入 Controller、Service、DAO、Redis、HTTP client 或配置单例。
- 不在 model property 中触发数据库查询或外部 I/O。
- 新字段要能回答默认值、历史数据、读写兼容和回滚策略。

## 兼容性要求

- 修改字段、索引、约束或表名时，同步评估 `ddl/`、迁移、已有数据、DAO 查询、测试 fixture。
- 删除或重命名字段前确认 API、缓存、任务、历史数据和外部报表是否依赖。
- 新增非空字段时说明默认值、回填策略、部署顺序和回滚方案。

## 验证重点

- ORM 映射能建表、查询和写入。
- 与 DAO 的字段引用保持一致。
- 涉及 JSON、时间、枚举、软删除的字段要有边界测试。
