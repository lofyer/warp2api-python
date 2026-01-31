# Warp2API

将 Warp AI 转换为 OpenAI 兼容 API 的代理服务。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置账户

在 `config/accounts/warp/` 目录下创建账户文件，每个账户一个 JSON 文件：

```json
{
  "name": "account_1",
  "refresh_token": "your_refresh_token_here",
  "enabled": true
}
```

### 3. 启动服务

```bash
python server.py
```

服务默认运行在 `http://0.0.0.0:9980`

### 4. 使用 API

```bash
curl http://localhost:9980/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

## 配置说明

### settings.json

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `server.host` | 监听地址 | `0.0.0.0` |
| `server.port` | 监听端口 | `9980` |
| `account_strategy` | 账户轮询策略 | `round-robin` |
| `retry_429_interval` | 429 限流后重试间隔（分钟） | `60` |
| `auto_save_tokens` | 自动保存 token 状态 | `true` |
| `disable_warp_tools` | 禁用 Warp 内置工具 | `true` |

### 账户状态码

| 状态码 | 说明 |
|--------|------|
| `403` | 账户被封禁，不可用 |
| `429` | 限流，超过重试间隔后自动恢复 |
| `quota_exceeded` | 配额用尽，月初自动重置 |

## 管理界面

访问 `http://localhost:9980/admin` 可使用 Web 管理界面：

- 查看账户状态
- 新增/删除账户
- 测试连接
- 批量删除封禁账户

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | OpenAI 兼容聊天接口 |
| `/v1/models` | GET | 获取可用模型列表 |
| `/stats` | GET | 获取账户统计信息 |
| `/accounts/add` | POST | 新增账户 |
| `/accounts/reload` | POST | 重载账户配置 |
| `/accounts/delete-blocked` | POST | 删除所有封禁账户 |

## 目录结构

```
warp2api-python/
├── server.py              # 主服务
├── config/
│   ├── settings.json      # 服务配置
│   └── accounts/
│       └── warp/          # 账户文件目录
│           ├── account_1.json
│           └── account_2.json
├── core/
│   ├── account_manager.py # 账户管理
│   ├── warp_client.py     # Warp 客户端
│   └── openai_adapter.py  # OpenAI 格式转换
├── frontend/
│   └── index.html         # Web 管理界面
└── logs/
    └── warp_api.log       # 日志文件
```

#如果从旧版本升级，运行迁移脚本：

```bash
python migrate_accounts.py
```

这会将 `config/accounts.json` 迁移到新的单文件目录结构。

## 赞助商

感谢 https://pay.ldxp.cn/shop/I8L3F7PJ 对本项目的支持！
