# AGENTS.md

`pkg/redis/` 封装 Redis 原语、JSON 编解码和 owner-token 分布式锁，不承载业务 key 命名。

- 连接由 provider 注入，不创建隐藏连接池；missing、bytes 解码、JSON 和返回类型是共享 contract。
- Redis 错误保留 cause 并转换为 `RedisOperationError`，不得在错误中包含 value 或凭据。
- 锁使用 `SET NX PX` 获取，只允许 owner 通过原子 compare-and-delete 释放；TTL、等待和重试参数为正且有界。
- 锁过期不表示业务完成，关键写入仍需数据库约束或 fencing；取消停止重试并向上传播。
- 批量接口空输入不访问 Redis，非空输入使用批量命令。
