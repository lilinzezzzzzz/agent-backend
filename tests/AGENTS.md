# AGENTS.md

`tests/` 使用 pytest、pytest-asyncio 和 AnyIO；共享 fixture 优先复用 `tests/conftest.py`。

- 测试放在主要被测领域对应的子目录，局部 fixture 留在对应测试文件或目录；跨领域 fixture 才放根
  `conftest.py`。
- 异步测试沿用现有 fixture，不创建冲突的 event loop；测试名描述可观察行为。
- 默认不依赖真实数据库、Redis、Celery Worker、Milvus、模型或第三方账号；需要外部服务的测试标记
  `integration`，缺少前置条件时明确跳过。
- 不使用真实密钥、生产连接串或生产数据。
- 保留真实 `pkg.logger`、`pkg.request_context`、`pkg.ids` 及父包引用；替身使用局部 fixture、`monkeypatch`
  或 `dependency_overrides`，不在收集阶段覆盖 `sys.modules`，不根据 pytest 命令行选择模块实现。
- 配置缓存独立 reset；应用 factory 的测试清理注册集中在 `tests/support/factory_cache.py`。
  `tests/support/__init__.py` 不预加载应用依赖；fixture 只通过 `sys.modules` 清理已加载模块，不为清理主动导入。
  测试单独运行与完整收集后的行为应一致，涉及全局状态时验证受影响顺序及辅助模块已加载/未加载两种路径。
- PostgreSQL 迁移集成测试沿用 `--run-postgresql-migrations` 和一次性容器，不连接已有数据库；SQLite
  只覆盖基础存储，不能证明行锁、fencing 或多实例竞争正确。
- 使用真实 Redis fixture 前核对测试目标：根 fixture 收尾会执行 `flushdb()`，默认库号不是隔离证明。
  仅对获准且可丢弃的专用实例/数据库运行；不对共享库或未核实地址运行。
- 新增非 package 测试目录或移动测试时，确保模块名不冲突并用 `pytest --collect-only` 检查收集。
- 优先运行能覆盖改动的最小测试；共享基础设施或公开 contract 变化再扩大范围。
