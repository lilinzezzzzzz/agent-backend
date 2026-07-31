# AGENTS.md

`pkg/logger/` 通过 lazy proxy 暴露统一 Loguru logger。

- `init_logger()` 由应用 lifespan 初始化；模块 import 时不得写日志或绑定尚未初始化的 logger。
- 日志格式和输出目标集中在 `LoggerHandler`，业务代码不散落自定义 format string。
- 应用内不实现 rotation/retention/compression；日志生命周期由部署层管理。
- logger 名称、初始化签名、文件路径和输出目标是共享运行 contract，变更时同步调用方、配置和运维文档。
- message 和 extra 必须脱敏，不记录凭据、连接串或敏感 payload。
