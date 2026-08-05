# 第三方登录集成指南

## 能力边界

当前脚手架只使用第三方身份完成登录，不代表用户调用第三方 API，也不保存
`access_token`、`refresh_token`、授权 scope 或原始用户信息。微信登录是当前唯一实现，其他提供方可沿用同一身份映射 contract 扩展。

## 身份模型

`internal/models/external_identity.py` 使用以下三元组唯一定位外部身份：

```text
(provider, connection_key, subject)
```

- `provider`：认证提供方，例如 `wechat`、`google`、`github`。
- `connection_key`：代码或配置中稳定的连接别名，不包含 client secret。当前微信连接为
  `wechat_default`。
- `subject`：该连接返回的稳定、大小写敏感用户标识；微信使用 OpenID。

邮箱、昵称、头像和 UnionID 都不是登录主键。当前保存邮箱、昵称、头像和 UnionID 快照，供创建本地用户和后续展示使用；邮箱统一视为未经本系统验证，不能据此自动合并用户或跳过邮箱验证码。

软删除身份再次登录时恢复原记录，不创建新映射或静默转移到其他用户。首次登录的本地用户和身份映射在同一数据库事务中创建；并发首次登录由三元组唯一约束收敛到同一用户。

## 微信登录流程

```text
POST /v1/auth/wechat/login
  -> AuthService.wechat_login()
  -> WeChatAuthStrategy 获取 token 和用户资料
  -> UserService.get_or_create_user_by_external_identity()
  -> 创建应用会话 token
```

第三方 token 只在请求期间用于读取微信用户资料，请求完成后不会持久化。

## 配置

在根目录 `.secrets` 中配置凭据：

```env
WECHAT_APP_ID=your_wechat_app_id
WECHAT_APP_SECRET=your_wechat_app_secret
```

在当前环境的 `configs/.env.{APP_ENV}` 中配置非敏感授权参数：

```env
WECHAT_GRANT_TYPE=authorization_code
```

根目录 `.env` 只用于选择 `APP_ENV`，不要在其中放置微信凭据。

## API 示例

```http
POST /v1/auth/wechat/login
Content-Type: application/json

{
  "code": "wechat_authorization_code"
}
```

成功响应继续使用项目统一的 `BaseResponse` 信封，`data` 中包含本地用户信息和应用会话 token。

## 扩展新认证连接

扩展其他提供方或新增同一提供方的第二个 client 时，需要同步完成：

1. 实现对应的 `BaseThirdPartyAuthStrategy`。
2. 为连接分配不可复用的稳定 `connection_key`。
3. 校验提供方响应中的 subject 与授权流程返回的 subject 一致。
4. 将标准化后的 subject 和非身份快照传给
   `UserService.get_or_create_user_by_external_identity()`。

如果未来需要代表用户调用第三方 API，应单独设计加密 credential 存储、scope、过期和刷新并发语义，不向 `external_identity` 增加明文 token 字段。
