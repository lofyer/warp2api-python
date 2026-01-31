# Warp2OpenAI 简化版架构设计

## 核心理念

**一个服务器，多账号轮询，直接转发** - 去除复杂的Protobuf编解码层，直接使用现有的Warp API客户端逻辑。

---

## 架构对比

### 原版架构（复杂）
```
客户端 → OpenAI API (28889) → Protobuf Bridge (28888) → Warp API
         [格式转换]              [Protobuf编解码]         [Protobuf]
```

### 简化版架构
```
客户端 → OpenAI API (9980) → Warp API
         [格式转换 + 账号轮询]   [直接使用现有客户端]
```

---

## 目录结构

```
python/
├── docs/
│   ├── warp-api-reference.md      # Warp API接口文档
│   └── architecture.md             # 本文档
├── config/
│   └── accounts.json               # 账号配置文件
├── core/
│   ├── __init__.py
│   ├── account_manager.py          # 账号管理和轮询
│   ├── warp_client.py              # Warp API客户端（复用现有逻辑）
│   └── openai_adapter.py           # OpenAI格式适配器
├── server.py                       # FastAPI服务器
├── requirements.txt                # 依赖列表
└── README.md                       # 使用说明
```

---

## 核心模块设计

### 1. 账号管理器 (account_manager.py)

**职责**:
- 加载多个refresh_token配置
- 管理每个账号的JWT token和过期时间
- 实现轮询策略（轮询/随机/负载均衡）
- 自动刷新过期的token
- 跟踪每个账号的配额使用情况

**接口**:
```python
class AccountManager:
    def __init__(self, config_path: str)
    async def get_next_account() -> Account
    async def refresh_account_token(account: Account) -> bool
    async def mark_account_quota_exceeded(account: Account)
    def get_account_stats() -> dict
```

**轮询策略**:
- **Round-Robin**: 依次轮询所有账号
- **Random**: 随机选择可用账号
- **Least-Used**: 选择使用次数最少的账号
- **Quota-Aware**: 优先选择配额充足的账号

### 2. Warp客户端 (warp_client.py)

**职责**:
- 复用现有的Warp API通信逻辑
- 处理JWT认证
- 执行client_login
- 发送Protobuf请求
- 解析SSE响应流

**接口**:
```python
class WarpClient:
    def __init__(self, jwt_token: str)
    async def login() -> bool
    async def chat_completion(messages: list, model: str, stream: bool) -> AsyncGenerator
    async def get_models() -> list
    async def get_usage() -> dict
```

**复用现有代码**:
- `warp2protobuf/core/auth.py` - JWT管理
- `warp2protobuf/warp/login.py` - 客户端登录
- `warp2protobuf/warp/api_client.py` - API通信
- `warp2protobuf/core/protobuf.py` - Protobuf编解码

### 3. OpenAI适配器 (openai_adapter.py)

**职责**:
- 将OpenAI格式转换为Warp格式
- 将Warp响应转换为OpenAI格式
- 处理流式和非流式响应

**接口**:
```python
class OpenAIAdapter:
    @staticmethod
    def openai_to_warp(openai_request: dict) -> dict
    
    @staticmethod
    async def warp_to_openai_stream(warp_stream: AsyncGenerator) -> AsyncGenerator
    
    @staticmethod
    def warp_to_openai_response(warp_response: dict) -> dict
```

### 4. FastAPI服务器 (server.py)

**职责**:
- 提供OpenAI兼容的HTTP接口
- 处理认证（可选）
- 路由请求到对应的账号
- 返回响应

**端点**:
```python
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest)

@app.get("/v1/models")
async def list_models()

@app.get("/health")
async def health_check()

@app.get("/stats")
async def get_stats()  # 账号使用统计
```

---

## 配置文件格式

### accounts.json

```json
{
  "strategy": "round-robin",
  "accounts": [
    {
      "name": "账号1",
      "refresh_token": "AMf-vBxSRmdhveGGBYM69p05kDhIn1i7wscALEmC9fYD...",
      "enabled": true,
      "priority": 1
    },
    {
      "name": "账号2",
      "refresh_token": "AMf-vBxSRmdhveGGBYM69p05kDhIn1i7wscALEmC9fYD...",
      "enabled": true,
      "priority": 2
    },
    {
      "name": "匿名账号",
      "refresh_token": null,
      "enabled": true,
      "priority": 3,
      "comment": "使用匿名token"
    }
  ],
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "auth": {
    "enabled": false,
    "api_keys": [
      "sk-your-api-key-1",
      "sk-your-api-key-2"
    ]
  },
  "logging": {
    "level": "INFO",
    "file": "logs/warp2openai.log"
  },
  "_comments": {
    "strategy": "可选值: round-robin | random | least-used | quota-aware",
    "refresh_token": "设置为null表示使用匿名token"
  }
}
```

---

## 请求流程

### 1. 用户发起请求

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-4-sonnet",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 2. 服务器处理流程

```python
# 1. 接收OpenAI格式请求
openai_request = await request.json()

# 2. 获取下一个可用账号
account = await account_manager.get_next_account()

# 3. 确保账号已登录
if not account.is_logged_in:
    await warp_client.login(account.jwt_token)

# 4. 转换请求格式
warp_request = OpenAIAdapter.openai_to_warp(openai_request)

# 5. 发送到Warp API
warp_stream = await warp_client.chat_completion(
    messages=warp_request["messages"],
    model=warp_request["model"],
    stream=openai_request.get("stream", False)
)

# 6. 转换响应格式并返回
if openai_request.get("stream"):
    return StreamingResponse(
        OpenAIAdapter.warp_to_openai_stream(warp_stream),
        media_type="text/event-stream"
    )
else:
    warp_response = await warp_stream
    return OpenAIAdapter.warp_to_openai_response(warp_response)
```

---

## 账号轮询逻辑

### Round-Robin 示例

```python
class RoundRobinStrategy:
    def __init__(self, accounts: list):
        self.accounts = [acc for acc in accounts if acc.enabled]
        self.current_index = 0
    
    async def get_next(self) -> Account:
        # 跳过配额用尽的账号
        attempts = 0
        while attempts < len(self.accounts):
            account = self.accounts[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.accounts)
            
            if not account.quota_exceeded:
                return account
            
            attempts += 1
        
        raise NoAvailableAccountError("所有账号配额已用尽")
```

### Quota-Aware 示例

```python
class QuotaAwareStrategy:
    async def get_next(self) -> Account:
        # 获取所有可用账号的配额信息
        available_accounts = []
        for account in self.accounts:
            if account.enabled and not account.quota_exceeded:
                usage = await account.get_usage()
                remaining = usage["limit"] - usage["used"]
                available_accounts.append((account, remaining))
        
        if not available_accounts:
            raise NoAvailableAccountError("所有账号配额已用尽")
        
        # 选择剩余配额最多的账号
        available_accounts.sort(key=lambda x: x[1], reverse=True)
        return available_accounts[0][0]
```

---

## 错误处理

### 1. 账号级别错误

```python
try:
    response = await warp_client.chat_completion(...)
except QuotaExceededError:
    # 标记账号配额用尽
    await account_manager.mark_account_quota_exceeded(account)
    # 重试下一个账号
    account = await account_manager.get_next_account()
    response = await warp_client.chat_completion(...)
except TokenExpiredError:
    # 刷新token
    await account_manager.refresh_account_token(account)
    # 重试
    response = await warp_client.chat_completion(...)
```

### 2. 全局错误

```python
try:
    account = await account_manager.get_next_account()
except NoAvailableAccountError:
    return JSONResponse(
        status_code=429,
        content={"error": "所有账号配额已用尽，请稍后再试"}
    )
```

---

## 优势对比

### 简化版优势

✅ **架构简单**: 单服务器，无需Protobuf Bridge  
✅ **易于维护**: 代码量减少60%+  
✅ **多账号支持**: 内置账号轮询和负载均衡  
✅ **配额管理**: 自动跟踪和管理每个账号的配额  
✅ **高可用**: 一个账号失败自动切换到下一个  
✅ **易于部署**: 单个Python进程，配置文件管理  
✅ **性能更好**: 减少一层网络调用  

### 原版优势

✅ **功能完整**: 包含WebSocket监控、GUI界面等  
✅ **调试友好**: Protobuf Bridge可以独立测试  
✅ **扩展性强**: 可以轻松添加新的编解码功能  

---

## 实现计划

### Phase 1: 核心功能
1. ✅ 创建目录结构
2. ✅ 编写API文档
3. ✅ 实现AccountManager
4. ✅ 复用WarpClient逻辑
5. ✅ 实现OpenAIAdapter
6. ✅ 实现FastAPI服务器

### Phase 2: 增强功能
1. ✅ 添加配额监控
2. ✅ 实现多种轮询策略
3. ✅ 添加统计和日志
4. ✅ 错误重试机制

### Phase 3: 优化
1. ⏳ 性能优化
2. ⏳ 连接池管理
3. ⏳ 缓存机制
4. ⏳ 监控面板

---

## 使用示例

### 启动服务器

```bash
cd python
python server.py --config config/accounts.json
```

### 使用OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed"  # 如果未启用认证
)

response = client.chat.completions.create(
    model="claude-4-sonnet",
    messages=[{"role": "user", "content": "你好"}],
    stream=True
)

for chunk in response:
    print(chunk.choices[0].delta.content, end="")
```

### 查看统计信息

```bash
curl http://localhost:9980/stats
```

**响应**:
```json
{
  "total_accounts": 3,
  "active_accounts": 2,
  "accounts": [
    {
      "name": "账号1",
      "enabled": true,
      "quota_used": 45,
      "quota_limit": 300,
      "quota_remaining": 255,
      "last_used": "2026-01-23T19:30:00Z"
    },
    {
      "name": "账号2",
      "enabled": true,
      "quota_used": 12,
      "quota_limit": 300,
      "quota_remaining": 288,
      "last_used": "2026-01-23T19:25:00Z"
    }
  ],
  "total_requests": 57,
  "strategy": "round-robin"
}
```

---

## 总结

简化版通过以下方式实现更简洁的架构：

1. **去除Protobuf Bridge层** - 直接在OpenAI服务器中处理Protobuf
2. **复用现有代码** - 使用已有的Warp客户端逻辑
3. **内置多账号管理** - 账号轮询和负载均衡
4. **单一配置文件** - YAML格式，易于管理
5. **统一错误处理** - 自动重试和故障转移

这个架构更适合生产环境部署，维护成本更低，同时保持了完整的功能性。
