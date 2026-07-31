# AGENTS.md

`tests/` 使用 pytest、pytest-asyncio 和 AnyIO；共享 fixture 优先复用 `tests/conftest.py`。

- 测试放在主要被测领域对应的子目录，局部 fixture 留在对应测试文件或目录；跨领域 fixture 才放根
  `conftest.py`。
- 异步测试沿用现有 fixture，不创建冲突的 event loop；测试名描述可观察行为。
- 默认不依赖真实数据库、Redis、Celery Worker、Milvus、模型或第三方账号；需要外部服务的测试标记
  `integration`，缺少前置条件时明确跳过。
- 不使用真实密钥、生产连接串或生产数据。
- 新增非 package 测试目录或移动测试时，确保模块名不冲突并用 `pytest --collect-only` 检查收集。
- 优先运行能覆盖改动的最小测试；共享基础设施或公开 contract 变化再扩大范围。
