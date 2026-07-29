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
- 新字段要明确 nullable、默认值和全新建库时的初始化语义。

## Schema 同步要求

- 当前项目是未部署脚手架，不保留旧 schema 兼容或历史数据迁移；修改字段、索引、约束或表名时直接同步
  `ddl/<dialect>/init.sql`、DAO 字段引用和测试 fixture。
- 只有任务明确面向已存在环境时，才评估历史数据、外部依赖、回填、部署顺序和回滚方案。

## 验证重点

- ORM 映射能建表、查询和写入。
- 与 DAO 的字段引用保持一致。
- 涉及 JSON、时间、枚举、软删除的字段要有边界测试。
