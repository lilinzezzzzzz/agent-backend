# AGENTS.md

适用于 `tests/`。

## 层职责

测试目录覆盖 API、Service/DAO、ORM、logger、toolkit、LLM、concurrency、vector 和 Celery 等行为。

## 编码约定

通用测试金字塔、mock 范围、回归测试、“不谎称测试通过”等规则见全局 `~/.qoder/AGENTS.md` Verification / Python tests 章节。项目特有约束：

- 使用 pytest、pytest-asyncio 和 anyio。
- `tests/conftest.py` 已提供配置 mock、logger mock、数据库 fixture、Redis fixture 和 FastAPI 测试客户端，优先复用。
- 共享 fixture 优先放在 `tests/conftest.py`，局部 fixture 放在对应测试文件或子目录。
- 异步测试遵循现有 fixture，不自行创建冲突的 event loop。
- 集成测试显式标记 `integration`。
- 测试名表达行为，不只描述实现细节。

## 目录组织

- 测试用例必须放在主要被测领域对应的子目录中，`tests/` 根目录不新增 `test_*.py`。
- 目录优先对应被测代码的业务领域或稳定组件，例如 `api/`、`services/`、`dao/`、`llm/`、`tasks/` 和 `orm/`。
- 一个测试文件涵盖多个领域时，优先按主要被测对象归类；职责相互独立时拆分到各自领域目录。
- 不按临时需求、开发者姓名或笼统的 `misc/`、`common/` 目录归类。
- 跨领域共享 fixture 放在 `tests/conftest.py`；仅服务单一领域的 fixture 放在对应领域目录的 `conftest.py`。
- 非 package 子目录之间的测试模块名必须全局唯一；新增目录或移动测试后使用 `pytest --collect-only` 验证收集结果。

## 外部依赖

- 默认不要假设 Redis、Celery Worker、Milvus 或真实数据库已运行。
- 依赖外部服务的测试必须标记 `integration`，在前置条件不满足时显式跳过并说明原因。
- 不要在测试中使用真实密钥、真实三方账号或生产连接串。

## 验证策略

- 修改代码时优先运行最小相关测试文件；共享基础设施变更再扩大到相关目录测试。
- 不要用快照式大断言掩盖关键行为；优先断言错误码、字段、查询结果和副作用。
