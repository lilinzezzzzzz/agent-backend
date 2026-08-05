# AGENTS.md

`pkg/identity_provider/` 提供外部身份提供方适配器和 registry，不读取应用全局配置。

- 新平台通过 `IdentityProviderType`、平台配置、provider 实现和 registry 注册接入，必要类型从包入口导出。
- 凭据通过构造函数注入；HTTP client 必须可关闭，平台响应归一为 `ExternalIdentityProfile` 后再返回上层。
- `IdentityProviderType` 枚举值可能持久化，改名或删除需要数据迁移；provider 接口和身份资料字段变化需同步所有调用方。
- 测试使用 mock HTTP client，不访问真实第三方服务或账号。
