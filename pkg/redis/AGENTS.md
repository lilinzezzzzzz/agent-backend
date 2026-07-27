# AGENTS.md

适用于 `pkg/redis/`。业务 key 与 TTL 规则见 `internal/cache/AGENTS.md`。

## 模块职责

本模块封装 Redis 原语、JSON 编解码和基于 owner token 的分布式锁，不承载具体业务 key 命名。

## 修改约束

- 连接通过 async session provider 注入；不创建隐藏连接池，不依赖 `internal.config`。
- `get_*` 的 missing 值、bytes 解码、JSON 格式和返回类型属于共享 contract，修改时全仓检查 cache
  调用方。
- Redis 异常统一保留 cause 并转换为 `RedisOperationError`；错误消息不得包含 value、凭据或完整敏感参数。
- 分布式锁使用 `SET NX PX` 获取，并只允许 owner identifier 通过原子 compare-and-delete 释放。
- lock TTL、等待 timeout 和 retry interval 必须为正且有界；锁过期不等于业务完成，关键写操作仍需
  数据库约束或 fencing。
- 取消应停止重试并传播；批量接口空输入不访问 Redis，非空输入使用一次批量命令。

## 验证重点

- 覆盖 hit/miss、序列化失败、异常转换、TTL、批量空输入、锁竞争/超时/错误 owner 和取消。
- 业务 key 或 TTL 变化在 `internal/cache/` 测试，不在本包测试中固化业务命名。
