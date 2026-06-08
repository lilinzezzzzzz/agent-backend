# Python `@dataclass` 实战指南

**核心定义：**
`@dataclass` (Python 3.7+) 是一个代码生成器。它自动为你生成 `__init__`, `__repr__`, `__eq__` 等样板代码，让你专注于**数据定义**而非类本身的基础设施构建。

-----

## 1\. 核心使用场景 (When to use)

* **数据传输对象 (DTOs):** 用于在系统各层之间（如 API 响应、数据库记录、RPC 消息）传递结构化数据。
* **配置管理:** 替代复杂的字典或硬编码的变量，提供类型提示和自动完成。
* **结构化日志/调试:** 利用自动生成的 `__repr__` 快速打印清晰的对象状态。
* **值对象 (Value Objects):** 当对象的相等性取决于其**内容**而非内存地址时（例如：坐标点、复数、颜色值）。

## 2\. 最佳实践 (Best Practices)

### A. 拥抱不可变性 (Immutability)

如果对象创建后不应修改，务必使用 `frozen=True`。这使对象可哈希（可作为字典键），且线程安全。

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Point:
    x: float
    y: float
    
# p = Point(1, 2)
# p.x = 3  # 抛出 FrozenInstanceError
```

### B. 处理可变默认值 (The Mutable Default Trap)

**永远不要**直接将列表或字典作为默认值。使用 `field(default_factory=...)`。

```python
from dataclasses import dataclass, field
from typing import List

@dataclass
class User:
    id: int
    # 错误做法: tags: List[str] = [] 
    # 正确做法:
    tags: List[str] = field(default_factory=list)
```

### C. 利用 `__post_init__` 进行验证

由于 `__init__` 是自动生成的，复杂的初始化逻辑或数据校验应放在 `__post_init__` 中。

```python
@dataclass
class Rectangle:
    width: float
    height: float

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Dimensions must be positive")
```

### D. 开启 `slots=True` (Python 3.10+)

如果你的对象非常多（数百万个），使用 `slots=True` 可以显著减少内存占用并提升属性访问速度。

```python
@dataclass(slots=True)
class Pixel:
    r: int
    g: int
    b: int
```

-----

## 3\. 不适合的场景 (When NOT to use)

* **复杂的初始化逻辑:** 如果你的 `__init__` 需要接受与字段不对应的参数，或者需要进行复杂的参数解析/转换，手写 `__init__` 比试图扭曲 `dataclass` 更清晰。
* **行为重于数据:** 如果一个类主要是一堆复杂的方法，只有极少量的状态，或者状态主要是私有的且依赖复杂的 Getters/Setters，普通类更合适。
* **需要兼容旧版 Python:** 3.6 及以下版本不支持（虽然有 backports，但不推荐用于新项目）。
* **极度敏感的性能场景:** 虽然 `slots=True` 很快，但在极端的性能热点路径上，原始的 `NamedTuple` 或 Cython/C 扩展可能仍略胜一筹（需基准测试验证）。

-----

## 4\. 快速决策表

| 特性需求 | 推荐方案 |
| :--- | :--- |
| **主要存数据，需要修改字段** | `@dataclass` |
| **主要存数据，只读，内存敏感** | `NamedTuple` 或 `@dataclass(frozen=True, slots=True)` |
| **主要存数据，需要作为字典 Key** | `@dataclass(frozen=True)` |
| **逻辑极其复杂，数据仅仅是辅助** | 普通 `class` |
| **需要兼容 Python 2 或 3.6-** | 普通 `class` 或 `collections.namedtuple` |

## 5\. **Python Dataclass 通用模板**

它包含了：

1. **高级 Field 配置**：处理可变默认值、隐藏敏感字段、添加元数据。
2. **`__post_init__`**：用于数据校验和属性处理。
3. **JSON 序列化/反序列化**：原生支持转 JSON 字符串和从 JSON 加载。

## 核心代码模板

```python
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional

@dataclass
class UserProfile:
    """
    用户配置数据类模板
    """
    # 1. 基础字段
    user_id: int
    username: str
    
    # 2. Field 配置: metadata (用于文档或第三方库)
    email: str = field(
        metadata={"help": "User's primary email address"}
    )
    
    # 3. Field 配置: 处理可变默认值 (必须用 default_factory)
    roles: list[str] = field(default_factory=lambda: ["user"])
    
    # 4. Field 配置: 敏感数据 (repr=False 在打印日志时不显示该字段)
    api_key: str | None = field(default=None, repr=False)

    # 5. 校验与初始化逻辑
    def __post_init__(self):
        # 校验逻辑
        if self.user_id <= 0:
            raise ValueError(f"User ID must be positive, got {self.user_id}")
        
        if "@" not in self.email:
            raise ValueError("Invalid email format")
            
        # 数据清洗/转换 (例如: 统一转小写)
        self.username = self.username.strip().lower()

    # 6. 序列化方法 (转字典)
    def to_dict(self) -> dict:
        return asdict(self)

    # 7. 序列化方法 (转 JSON 字符串)
    def to_json(self) -> str:
        # ensure_ascii=False 允许正确输出中文
        return json.dumps(self.to_dict(), ensure_ascii=False)

    # 8. 反序列化方法 (工厂方法)
    @classmethod
    def from_json(cls, json_str: str) -> 'UserProfile':
        data = json.loads(json_str)
        # 注意：如果是嵌套的 dataclass，这里需要更复杂的处理或使用专门的库
        return cls(**data)

# --- 使用示例 ---

if __name__ == "__main__":
    try:
        # 1. 实例化 (触发 __post_init__)
        user = UserProfile(
            user_id=101, 
            username="  Admin_User  ", # 会自动被 strip 和 lower
            email="admin@example.com",
            api_key="secret_12345"
        )

        # 2. 打印 (注意 api_key 不会被打印)
        print("Obj Repr:", user) 
        # 输出: UserProfile(user_id=101, username='admin_user', email='admin@example.com', roles=['user'])

        # 3. 转 JSON
        json_output = user.to_json()
        print("JSON Out:", json_output)
        # 输出: {"user_id": 101, "username": "admin_user", ...}

        # 4. 从 JSON 加载
        new_user = UserProfile.from_json(json_output)
        print("Is Equal:", user == new_user) # 输出: True

        # 5. 触发校验错误
        # bad_user = UserProfile(user_id=-1, username="test", email="bad_email")
        
    except ValueError as e:
        print(f"Validation Error: {e}")
```

-----

## 关键点解析

1. **`field(default_factory=...)`**:

      * **作用**: 解决 Python "可变默认参数陷阱"。
      * **解释**: 如果你直接写 `roles: List[str] = []`，所有实例将共享同一个列表对象，修改一个会影响所有。使用 `factory` 确保每个实例都有一个新的列表。

2. **`field(repr=False)`**:

      * **作用**: 安全性与日志清晰度。
      * **解释**: 这里的 `api_key` 包含敏感信息，设置 `False` 后，当你 `print(user)` 或打 Log 时，该字段会自动被隐藏，防止敏感信息泄露到日志中。

3. **`__post_init__`**:

      * **作用**: 弥补 `__init__` 被自动接管后的逻辑空缺。
      * **解释**: 在这里进行任何需要在初始化后立即执行的校验（如 ID \> 0）或数据处理（如字符串去空格）。

4. **`asdict(self)`**:

      * **作用**: 标准库提供的转换工具。
      * **解释**: 它不仅转换当前对象，还会递归地将内部嵌套的 Dataclass 也转换为字典，非常适合做 JSON 序列化的前置步骤。
