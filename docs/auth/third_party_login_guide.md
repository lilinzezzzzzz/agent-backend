# 第三方登录集成指南

## 📋 功能概述

基于**策略模式 + 工厂模式**设计的第三方登录系统，支持快速扩展多种登录方式：
- ✅ 微信登录（已实现）
- 🔲 支付宝登录（预留）
- 🔲 Google 登录（可扩展）
- 🔲 GitHub 登录（可扩展）

## 🏗️ 架构设计

```
┌─────────────────────────────────────────────────────┐
│                  Controller Layer                    │
│  (internal/controllers/api/auth.py)                 │
│  - /auth/wechat/login                               │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│                   Service Layer                      │
│  (internal/services/user.py)                        │
│  - get_or_create_user_by_third_party()             │
│  - bind_third_party_account()                       │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│              Third-Party Auth Factory                │
│  (internal/utils/third_party_auth/factory.py)       │
│  - get_strategy(platform)                           │
└──────────────────┬──────────────────────────────────┘
                   │
    ┌──────────────┴──────────────┐
    │                             │
┌───▼──────────┐          ┌──────▼────────┐
│ WeChatStrategy│          │ AlipayStrategy│
│ (已实现)      │          │ (待扩展)       │
└──────────────┘          └───────────────┘
```

## 🚀 使用步骤

### 1️⃣ 配置密钥

在 `configs/.secrets` 文件中添加微信配置：

```bash
# 微信开放平台配置
WECHAT_APP_ID=wx_xxxxxxxxxxxxxx
WECHAT_APP_SECRET=your_app_secret_here
WECHAT_GRANT_TYPE=authorization_code
```

> ⚠️ **重要**: 需要在 [微信开放平台](https://open.weixin.qq.com/) 申请应用获取 AppID 和 AppSecret

### 2️⃣ 数据库迁移

执行 SQL 添加第三方登录字段：

```sql
ALTER TABLE user
ADD COLUMN wechat_openid VARCHAR(128) COMMENT '微信 OpenID',
ADD COLUMN wechat_unionid VARCHAR(128) COMMENT '微信 UnionID',
ADD COLUMN wechat_avatar VARCHAR(512) COMMENT '微信头像 URL',
ADD COLUMN wechat_nickname VARCHAR(128) COMMENT '微信昵称',
ADD INDEX idx_wechat_openid (wechat_openid);
```

### 3️⃣ 前端调用示例

#### H5 网页微信登录

```javascript
// 1. 引导用户跳转到微信授权页面
const wechatAuthUrl = `https://open.weixin.qq.com/connect/oauth2/authorize?appid=${WECHAT_APP_ID}&redirect_uri=${encodeURIComponent(window.location.origin + '/wechat-callback')}&response_type=code&scope=snsapi_userinfo#wechat_redirect`

window.location.href = wechatAuthUrl

// 2. 微信回调后，提取 code
const urlParams = new URLSearchParams(window.location.search)
const code = urlParams.get('code')

// 3. 发送到后端
const response = await fetch('/v1/auth/wechat/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ code })
})

const { user, token } = await response.json()

// 4. 存储 token
localStorage.setItem('token', token)
```

#### 微信小程序登录

```javascript
// 小程序端调用 wx.login
wx.login({
  success: async (res) => {
    if (res.code) {
      // 将 code 发送到后端
      const response = await fetch('/v1/auth/wechat/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: res.code })
      })

      const { user, token } = await response.json()
      wx.setStorageSync('token', token)
    }
  }
})
```

## 🔧 扩展新平台（以支付宝为例）

### Step 1: 创建支付宝策略

```python
# internal/utils/third_party_auth/alipay.py
from .base import BaseThirdPartyAuthStrategy, ThirdPartyUserInfo

class AlipayAuthStrategy(BaseThirdPartyAuthStrategy):
    """支付宝 OAuth2.0 认证策略"""

    ACCESS_TOKEN_URL = "https://openapi.alipay.com/gateway.do"
    USER_INFO_URL = "https://openapi.alipay.com/gateway.do"

    def __init__(self):
        self.app_id = settings.ALIPAY_APP_ID
        self.private_key = settings.ALIPAY_PRIVATE_KEY
        # ... 初始化

    async def get_access_token(self, auth_code: str) -> dict:
        # 调用支付宝 API 获取 access_token
        pass

    async def get_user_info(self, access_token: str, open_id: str) -> ThirdPartyUserInfo:
        # 获取支付宝用户信息
        pass

    def get_platform_name(self) -> str:
        return "alipay"
```

### Step 2: 注册到工厂

```python
# internal/utils/third_party_auth/factory.py
from .alipay import AlipayAuthStrategy

class ThirdPartyPlatform(str, Enum):
    WECHAT = "wechat"
    ALIPAY = "alipay"  # 新增

class ThirdPartyAuthFactory:
    _strategies: dict[ThirdPartyPlatform, Type[BaseThirdPartyAuthStrategy]] = {
        ThirdPartyPlatform.WECHAT: WeChatAuthStrategy,
        ThirdPartyPlatform.ALIPAY: AlipayAuthStrategy,  # 新增
    }
```

### Step 3: 添加 API 接口

```python
# internal/controllers/api/auth.py
@router.post("/alipay/login", response_model=UserLoginRespSchema, summary="支付宝登录")
async def alipay_login(req: AlipayLoginReqSchema, user_service: UserServiceDep):
    strategy = ThirdPartyAuthFactory.get_strategy(ThirdPartyPlatform.ALIPAY)
    # ... 类似微信登录逻辑
```

## 📊 API 接口文档

### POST /v1/auth/wechat/login

**请求示例:**
```json
{
  "code": "071xxx...xxx"
}
```

**响应示例:**
```json
{
  "code": 20000,
  "message": "success",
  "data": {
    "user": {
      "id": 123456,
      "name": "微信用户",
      "phone": ""
    },
    "token": "tk_abc123def456..."
  }
}
```

## 🔐 安全特性

- ✅ 使用 HTTPS 传输
- ✅ Token 有效期控制（30 分钟）
- ✅ Redis 双 Key 结构管理
- ✅ 自动清理过期 token
- ✅ 支持多设备同时登录

## 📝 注意事项

1. **测试环境**: 使用微信测试号进行测试
   - 测试号地址：https://mp.weixin.qq.com/debug/cgi-bin/sandboxinfo

2. **生产环境**:
   - 确保配置正确的回调域名
   - 使用正式的应用 AppID 和 AppSecret

3. **UnionID 机制**:
   - 如果有多个应用（公众号、小程序、APP），使用 UnionID 识别同一用户

4. **账号绑定**:
   - 第三方登录默认无手机号
   - 可后续开发绑定手机号功能

## 🎯 下一步优化建议

- [ ] 添加短信验证码绑定手机号功能
- [ ] 添加账号解绑功能
- [ ] 添加登录日志记录
- [ ] 添加异常登录检测
- [ ] 支持更多第三方平台（Google、GitHub 等）
