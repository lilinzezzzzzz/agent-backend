# 认证模块使用指南

## 概述

本项目使用基于 Redis 的 Token 认证机制，而非 JWT。Token 存储在 Redis 中，支持：

- 用户登录/登出
- Token 自动过期（默认 30 分钟）
- 用户元数据缓存
- Token 列表管理（支持批量登出）

## API 端点

### 1. 用户登录

**端点**: `POST /v1/auth/login`

**请求体**:

```json
{
  "username": "testuser",
  "password": "password123"
}
```

**响应**:

```json
{
  "user": {
    "id": 1,
    "name": "testuser",
    "phone": "13800138000"
  },
  "token": "tk_abc123def456..."
}
```

**说明**:

- Token 有效期为 30 分钟
- Token 会存储在 Redis 中，key 格式：`token:{token}`
- 用户的 token 列表也会存储，key 格式：`token_list:{user_id}`

---

### 2. 用户登出

**端点**: `POST /v1/auth/logout`

**请求头**:

```text
Authorization: Bearer tk_abc123def456...
```

**响应**:

```json
{
  "message": "登出成功"
}
```

**说明**:

- 从 Redis 删除 token
- 从用户的 token 列表中移除该 token

---

### 3. 获取当前用户信息

**端点**: `GET /v1/auth/me`

**请求头**:

```text
Authorization: Bearer tk_abc123def456...
```

**响应**:

```json
{
  "id": 1,
  "name": "testuser",
  "phone": "13800138000"
}
```

**说明**:

- 通过 auth 中间件自动验证 token
- 从上下文获取 user_id
- TODO: 可从数据库或缓存获取完整用户信息

---

## Redis 数据结构

### Token 存储

```
Key: token:{token_value}
Type: String (JSON)
Value: {
  "id": 1,
  "username": "testuser",
  "phone": "13800138000",
  "created_at": 1234567890
}
TTL: 1800 秒 (30 分钟)
```

### 用户 Token 列表

```
Key: token_list:{user_id}
Type: List
Value: ["token1", "token2", ...]
```

---

## 实现细节

### Token 生成

```python
def generate_token() -> str:
    """生成随机 token"""
    return f"tk_{uuid.uuid4().hex}"
```

### Token 验证流程

1. Auth 中间件拦截请求
2. 从 Redis 读取 token 对应的用户元数据
3. 检查 token 是否在用户的 token 列表中
4. 验证通过后，将 user_id 设置到上下文中

### 密码验证

当前实现中，密码验证逻辑标记为 TODO。实际部署时应：

- 使用 bcrypt 或 argon2 等加密算法存储密码
- 在登录时校验密码哈希值

---

## 安全建议

1. **密码存储**: 使用 bcrypt/argon2 加密
2. **HTTPS**: 生产环境必须使用 HTTPS
3. **Token 刷新**: 可实现 refresh token 机制
4. **限流**: 对登录接口添加限流保护
5. **审计日志**: 记录登录/登出操作日志

---

## 扩展功能建议

- [ ] 实现密码加密验证
- [ ] 添加 refresh token 机制
- [ ] 实现记住我功能（长期 token）
- [ ] 添加登录失败次数限制
- [ ] 实现多设备管理（查看已登录设备）
- [ ] 添加异地登录检测
