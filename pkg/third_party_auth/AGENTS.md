# AGENTS.md

`pkg/third_party_auth/` 提供第三方登录策略和 factory，不读取应用全局配置。

- 新平台通过 `ThirdPartyPlatform`、平台配置、策略实现和 factory 注册接入，必要类型从包入口导出。
- 凭据通过构造函数注入；HTTP client 必须可关闭，平台响应归一为 `ThirdPartyUserInfo` 后再返回上层。
- `ThirdPartyPlatform` 枚举值可能持久化，改名或删除需要数据迁移；策略接口和用户字段变化需同步所有调用方。
- 测试使用 mock HTTP client，不访问真实第三方服务或账号。
