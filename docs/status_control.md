# 账户状态控制流程

## 状态机概览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              请求处理流程                                    │
└─────────────────────────────────────────────────────────────────────────────┘

请求到达 (/v1/chat/completions 或 /v1/messages)
    │
    ▼
┌─────────────────────────────────────┐
│  get_next_account()                 │
│  ├─ 检查 enabled                    │
│  ├─ 检查 status_code (403/429/quota)│
│  └─ 429: 检查是否超过重试间隔        │
└─────────────────────────────────────┘
    │
    ├── 无可用账户 ──→ 返回 503 NoAvailableAccountError
    │
    ▼
┌─────────────────────────────────────┐
│  ensure_ready()                     │
│  ├─ 检查 JWT 是否过期               │
│  └─ 检查是否已登录                  │
└─────────────────────────────────────┘
    │
    ├── JWT 过期 ──→ refresh_token()
    │                   │
    │                   ├── 成功 ──→ 继续
    │                   ├── 403 ──→ 标记 status_code=403, 抛出异常
    │                   ├── 429 ──→ 标记 status_code=429, 抛出异常
    │                   └── 超时/网络错误 ──→ 不标记状态, 抛出异常
    │
    ├── 未登录 ──→ login()
    │               │
    │               ├── 成功 (204) ──→ 继续
    │               ├── 403 ──→ 标记 status_code=403, 返回 false
    │               ├── 429 ──→ 标记 status_code=429, 返回 false
    │               └── 超时/网络错误 ──→ 不标记状态, 返回 false
    │
    ▼
┌─────────────────────────────────────┐
│  chat_completion()                  │
│  发送 Protobuf 请求到 Warp API      │
└─────────────────────────────────────┘
    │
    ├── 成功 ──→ 返回响应流
    │
    ├── 403 ──→ 标记 status_code=403, 重试下一个账户
    │
    ├── 429 ──→ 标记 status_code=429, 重试下一个账户
    │
    ├── quota_exceeded ──→ 标记 status_code=quota_exceeded
    │
    └── 其他错误 ──→ 记录错误, 重试或返回 500
```

## 账户状态码 (status_code)

| 状态码 | 含义 | 是否可用 | 恢复条件 |
|--------|------|----------|----------|
| `null` | 正常 | ✅ 可用 | - |
| `403` | 账户被封禁 | ❌ 不可用 | 手动处理 |
| `429` | 请求限流 | ⏳ 等待后可用 | 超过 `retry_429_interval` 分钟后自动恢复 |
| `quota_exceeded` | 配额用尽 | ❌ 不可用 | 月初自动重置 |

## 关键控制点

### 1. 账户选择 (`is_available()`)

```python
def is_available(self) -> bool:
    # 1. 未启用 → 不可用
    if not self.enabled:
        return False
    
    # 2. 配额用尽检查月初重置
    if self.status_code == "quota_exceeded" and self.quota_reset_date:
        if datetime.now() >= self.quota_reset_date:
            self.status_code = None  # 自动重置
    
    # 3. 403 封禁 → 不可用
    if self.status_code == "403":
        return False
    
    # 4. 429 限流 → 检查重试间隔
    if self.status_code == "429":
        elapsed = (datetime.now() - self.last_attempt).total_seconds() / 60
        if elapsed >= self.retry_429_interval:
            self.status_code = None  # 允许重试
            return True
        return False
    
    return True
```

### 2. Token 刷新 (`refresh_token()`)

```python
async def refresh_token(self) -> bool:
    response = await client.post(REFRESH_URL, ...)
    
    if response.status_code == 200:
        # 更新 JWT token 和过期时间
        return True
    elif response.status_code == 403:
        self.account.mark_blocked(403, "Blocked")
        return False
    elif response.status_code == 429:
        self.account.mark_blocked(429, "Too Many Requests")
        return False
    # 网络超时不标记状态（可能是临时问题）
```

### 3. 请求重试 (`handle_chat_completion()`)

```python
max_retries = 3

for attempt in range(max_retries):
    account = await account_manager.get_next_account()
    
    try:
        # 发送请求...
        return response
    
    except Exception as e:
        if "403" in str(e):
            account.mark_blocked(403, "Blocked")
            continue  # 重试下一个账户
        
        if "429" in str(e):
            account.mark_blocked(429, "Too Many Requests")
            continue  # 重试下一个账户
        
        if "failed to prepare" in str(e):
            continue  # 重试下一个账户
        
        raise  # 其他错误直接抛出
```

## 配置参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `retry_429_interval` | 429 限流后重试间隔（分钟） | 60 |
| `auto_save_tokens` | 自动保存 token 状态 | true |
| `max_retries` | 请求最大重试次数 | 3 |

## 持久化字段

账户配置文件 (`config/accounts/warp/*.json`) 中持久化的字段：

```json
{
  "name": "account_1",
  "refresh_token": "xxx",
  "enabled": true,
  "status_code": "403",
  "last_refreshed": "2024-01-15T10:30:00",
  "last_attempt": "2024-01-15T10:35:00"
}
```

**注意**: `jwt_token` 和 `jwt_expires_at` 只在内存中，不持久化。每次启动服务时会重新刷新 token。

## 错误处理策略

### 网络错误（超时、连接失败）

- **不标记** `status_code`（可能是临时网络问题）
- 记录错误日志
- 重试下一个账户

### API 错误（403、429）

- **标记** `status_code`
- 记录 `last_attempt` 时间
- 保存配置到文件
- 重试下一个账户

### 配额错误

- 标记 `status_code=quota_exceeded`
- 计算下月重置日期
- 月初自动恢复
