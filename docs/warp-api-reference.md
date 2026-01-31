# Warp API 接口文档

## 概述

Warp API 使用 Protobuf over HTTP/2 进行通信，主要包括认证、登录和AI对话三个核心接口。

---

## 1. JWT Token 刷新接口

### 请求

**URL**: `https://app.warp.dev/proxy/token?key=AIzaSyBdy3O3S9hrdayLJxJ7mriBR4qgUaUygAs`

**方法**: `POST`

**Headers**:
```
x-warp-client-version: v0.2025.08.06.08.12.stable_02
x-warp-os-category: Windows
x-warp-os-name: Windows
x-warp-os-version: 11 (26100)
content-type: application/x-www-form-urlencoded
accept: */*
accept-encoding: gzip, br
```

**Body** (URL-encoded):
```
grant_type=refresh_token&refresh_token={REFRESH_TOKEN}
```

### 响应

**成功 (200)**:
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjFjMzIxOTgzNGRhNTBlMjBmYWVhZWE3Yzg2Y2U3YjU1MzhmMTdiZTEiLCJ0eXAiOiJKV1QifQ...",
  "expires_in": "3600",
  "token_type": "Bearer",
  "refresh_token": "AMf-vBxSRmdhveGGBYM69p05kDhIn1i7wscALEmC9fYDRpHzjER9dL7kk-kHPYwvY9ROkmy50qGTcIiJZ4Am86hPXkpVPL90HJmAf5fZ7PejypdbcK4wso8Kf3axiSWtIROhOcn9NzGaSvl7WqRMNOpHGgBrYn4I8krW57R8_ws8u7XcSw8u0DiL9HrpMm0LtwsCh81k_6bb2CWOEb1lIx3HWSBTePFWsQ",
  "id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjFjMzIxOTgzNGRhNTBlMjBmYWVhZWE3Yzg2Y2U3YjU1MzhmMTdiZTEiLCJ0eXAiOiJKV1QifQ...",
  "user_id": "hITdadrrT2bhXy2jiGRVjapEXvA3",
  "project_id": "astral-field-294621"
}
```

**字段说明**:
- `access_token`: JWT访问令牌，用于后续API调用
- `expires_in`: 令牌有效期（秒）
- `refresh_token`: 新的刷新令牌（可选，用于下次刷新）
- `id_token`: Firebase ID令牌
- `user_id`: 用户唯一标识

---

## 2. 客户端登录接口

### 请求

**URL**: `https://app.warp.dev/client/login`

**方法**: `POST`

**Headers**:
```
x-warp-client-id: warp-app
x-warp-client-version: v0.2025.08.06.08.12.stable_02
x-warp-os-category: Windows
x-warp-os-name: Windows
x-warp-os-version: 11 (26100)
authorization: Bearer {JWT_TOKEN}
x-warp-experiment-id: {UUID}
x-warp-experiment-bucket: {HASH}
accept: */*
accept-encoding: gzip,br
content-length: 0
```

**Body**: 空

### 响应

**成功 (204 No Content)**:
- 无响应体
- 返回 `Set-Cookie` 头，包含 `rl_anonymous_id` 等session cookies

**失败 (403)**:
```json
{
  "error": "Forbidden"
}
```

---

## 3. AI 对话接口

### 请求

**URL**: `https://app.warp.dev/ai/multi-agent`

**方法**: `POST`

**Headers**:
```
accept: text/event-stream
content-type: application/x-protobuf
x-warp-client-version: v0.2025.08.06.08.12.stable_02
x-warp-os-category: Windows
x-warp-os-name: Windows
x-warp-os-version: 11 (26100)
authorization: Bearer {JWT_TOKEN}
content-length: {PROTOBUF_SIZE}
```

**Body**: Protobuf 编码的 `warp.multi_agent.v1.Request` 消息

#### Protobuf 消息结构 (JSON表示)

```json
{
  "task_context": {
    "tasks": [
      {
        "id": "uuid-task-id",
        "description": "",
        "status": {
          "in_progress": {}
        },
        "messages": [
          {
            "id": "uuid-message-id",
            "agent_output": {
              "text": "用户消息内容"
            }
          }
        ]
      }
    ]
  },
  "settings": {
    "model_config": {
      "base": "claude-4-sonnet",
      "planning": "o3",
      "coding": "auto"
    }
  },
  "client_capabilities": {
    "supports_todos_ui": false,
    "supports_linked_code_blocks": false,
    "supported_tools": [9]
  },
  "metadata": {
    "conversation_id": "conv-uuid",
    "logging": {
      "is_autodetected_user_query": true,
      "entrypoint": "USER_INITIATED"
    }
  }
}
```

### 响应

**成功 (200)**: Server-Sent Events (SSE) 流

#### SSE 事件格式

每个事件是 Protobuf 编码的 `warp.multi_agent.v1.ResponseEvent` 消息，包含以下类型：

##### 1. StreamInit 事件
```json
{
  "init": {
    "conversation_id": "conv-uuid",
    "request_id": "req-uuid"
  }
}
```

##### 2. ClientActions 事件

**创建任务**:
```json
{
  "client_actions": {
    "actions": [
      {
        "create_task": {
          "task": {
            "id": "task-uuid",
            "description": "任务描述"
          }
        }
      }
    ]
  }
}
```

**添加消息**:
```json
{
  "client_actions": {
    "actions": [
      {
        "add_messages_to_task": {
          "task_id": "task-uuid",
          "messages": [
            {
              "id": "msg-uuid",
              "agent_output": {
                "text": "AI回复内容"
              }
            }
          ]
        }
      }
    ]
  }
}
```

**追加内容** (流式输出):
```json
{
  "client_actions": {
    "actions": [
      {
        "append_to_message_content": {
          "task_id": "task-uuid",
          "message_id": "msg-uuid",
          "text_delta": "流式输出的文本片段"
        }
      }
    ]
  }
}
```

##### 3. StreamFinished 事件
```json
{
  "finished": {
    "token_usage": [
      {
        "model_id": "claude-4-sonnet",
        "total_input": 150,
        "output": 200,
        "input_cache_read": 0,
        "input_cache_write": 0,
        "cost_in_cents": 0.05
      }
    ],
    "request_cost": {
      "exact": 0.05
    },
    "context_window_info": {
      "context_window_usage": 0.15,
      "summarized": false
    },
    "reason": {
      "done": {}
    }
  }
}
```

**完成原因类型**:
- `done`: 正常完成
- `max_token_limit`: 达到最大token限制
- `quota_limit`: 配额用尽
- `context_window_exceeded`: 上下文窗口超限
- `llm_unavailable`: LLM服务不可用
- `internal_error`: 内部错误

---

## 4. GraphQL 接口

### 4.1 获取模型列表

**URL**: `https://app.warp.dev/graphql/v2?op=GetFeatureModelChoices`

**方法**: `POST`

**Headers**:
```
x-warp-client-id: warp-app
x-warp-client-version: v0.2025.08.06.08.12.stable_02
x-warp-os-category: Windows
x-warp-os-name: Windows
x-warp-os-version: 11 (26100)
content-type: application/json
authorization: Bearer {JWT_TOKEN}
accept: */*
accept-encoding: gzip,br
```

**Body**:
```json
{
  "query": "query GetFeatureModelChoices($requestContext: RequestContext!) { user(requestContext: $requestContext) { __typename ... on UserOutput { user { workspaces { featureModelChoice { agentMode { defaultId choices { displayName baseModelName id reasoningLevel usageMetadata { creditMultiplier requestMultiplier } description disableReason visionSupported spec { cost quality speed } provider } } } } } } } }",
  "variables": {
    "requestContext": {
      "clientContext": {
        "version": "v0.2025.08.06.08.12.stable_02"
      },
      "osContext": {
        "category": "Windows",
        "linuxKernelVersion": null,
        "name": "Windows",
        "version": "11 (26100)"
      }
    }
  },
  "operationName": "GetFeatureModelChoices"
}
```

**响应**:
```json
{
  "data": {
    "user": {
      "__typename": "UserOutput",
      "user": {
        "workspaces": [
          {
            "featureModelChoice": {
              "agentMode": {
                "defaultId": "auto-genius",
                "choices": [
                  {
                    "displayName": "claude 4 sonnet",
                    "baseModelName": "claude 4 sonnet",
                    "id": "claude-4-sonnet",
                    "reasoningLevel": "Off",
                    "usageMetadata": {
                      "creditMultiplier": null,
                      "requestMultiplier": 1
                    },
                    "description": null,
                    "disableReason": null,
                    "visionSupported": true,
                    "spec": {
                      "cost": 0.6,
                      "quality": 0.6,
                      "speed": 0.7
                    },
                    "provider": "ANTHROPIC"
                  }
                ]
              }
            }
          }
        ]
      }
    }
  }
}
```

### 4.2 获取用量信息

**URL**: `https://app.warp.dev/graphql/v2?op=GetRequestLimitInfo`

**方法**: `POST`

**Headers**: 同上

**Body**:
```json
{
  "query": "query GetRequestLimitInfo($requestContext: RequestContext!) { user(requestContext: $requestContext) { __typename ... on UserOutput { user { requestLimitInfo { isUnlimited nextRefreshTime requestLimit requestsUsedSinceLastRefresh requestLimitRefreshDuration } } } } }",
  "variables": {
    "requestContext": {
      "clientContext": {
        "version": "v0.2025.08.06.08.12.stable_02"
      },
      "osContext": {
        "category": "Windows",
        "linuxKernelVersion": null,
        "name": "Windows",
        "version": "11 (26100)"
      }
    }
  },
  "operationName": "GetRequestLimitInfo"
}
```

**响应**:
```json
{
  "data": {
    "user": {
      "__typename": "UserOutput",
      "user": {
        "requestLimitInfo": {
          "isUnlimited": false,
          "nextRefreshTime": "2026-02-21T14:06:36.156167Z",
          "requestLimit": 300,
          "requestsUsedSinceLastRefresh": 10,
          "requestLimitRefreshDuration": "MONTHLY"
        }
      }
    }
  }
}
```

---

## 5. 匿名用户创建接口

### 请求

**URL**: `https://app.warp.dev/graphql/v2?op=CreateAnonymousUser`

**方法**: `POST`

**Headers**:
```
content-type: application/json
accept: */*
accept-encoding: gzip, br
```

**Body**:
```json
{
  "query": "mutation CreateAnonymousUser($requestContext: RequestContext!) { createAnonymousUser(requestContext: $requestContext) { __typename ... on CreateAnonymousUserOutput { customToken } } }",
  "variables": {
    "requestContext": {
      "clientContext": {
        "version": "v0.2025.08.06.08.12.stable_02"
      },
      "osContext": {
        "category": "Windows",
        "linuxKernelVersion": null,
        "name": "Windows",
        "version": "11 (26100)"
      }
    }
  },
  "operationName": "CreateAnonymousUser"
}
```

**响应**:
```json
{
  "data": {
    "createAnonymousUser": {
      "__typename": "CreateAnonymousUserOutput",
      "customToken": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
    }
  }
}
```

### 后续步骤

使用 `customToken` 调用 Firebase Identity Toolkit:

**URL**: `https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key=AIzaSyBdy3O3S9hrdayLJxJ7mriBR4qgUaUygAs`

**方法**: `POST`

**Body**:
```json
{
  "token": "{CUSTOM_TOKEN}",
  "returnSecureToken": true
}
```

**响应**:
```json
{
  "idToken": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjFjMzIxOTgzNGRhNTBlMjBmYWVhZWE3Yzg2Y2U3YjU1MzhmMTdiZTEiLCJ0eXAiOiJKV1QifQ...",
  "refreshToken": "AMf-vBxSRmdhveGGBYM69p05kDhIn1i7wscALEmC9fYDRpHzjER9dL7kk...",
  "expiresIn": "3600"
}
```

---

## 6. 错误响应

### 401 Unauthorized
```json
{
  "error": "Unauthorized",
  "message": "Invalid or expired JWT token"
}
```

### 403 Forbidden
```json
{
  "error": "Forbidden",
  "message": "Client login required"
}
```

### 429 Too Many Requests
```
No remaining quota
```
或
```
No AI requests remaining
```

### 500 Internal Server Error
```json
{
  "error": "Internal Server Error",
  "message": "Failed to process request"
}
```

---

## 7. 支持的模型列表

### Anthropic Claude
- `claude-4-sonnet`
- `claude-4-opus`
- `claude-4.1-opus`
- `claude-4.5-haiku`
- `claude-4.5-opus`
- `claude-4.5-sonnet`

### OpenAI GPT
- `gpt-5`
- `gpt-5-low-reasoning`
- `gpt-5-1-low-reasoning`
- `gpt-5-1-medium-reasoning`
- `gpt-5-1-high-reasoning`
- `gpt-5-1-codex-low`
- `gpt-5-2-low`
- `gpt-5-2-medium`
- `gpt-5-2-high`

### Google Gemini
- `gemini-2.5-pro`
- `gemini-3-pro`

### 其他
- `auto` - 自动选择
- `auto-efficient` - 成本优化
- `auto-genius` - 质量优先
- `glm-47-fireworks` - GLM 4.7

---

## 8. 注意事项

1. **HTTP/2 支持**: Warp API 需要 HTTP/2 支持
2. **TLS 验证**: 生产环境必须启用 TLS 验证
3. **JWT 刷新**: JWT token 有效期约1小时，需要定期刷新
4. **Session Cookies**: 某些操作需要先调用 `/client/login` 获取 session
5. **Protobuf 编码**: AI对话接口使用 Protobuf 二进制格式
6. **SSE 流式**: 响应是 Server-Sent Events 格式，需要逐行解析
7. **配额限制**: 免费账号有请求次数限制（通常300次/月）
8. **User-Agent**: 不要发送 `user-agent` 头，否则可能触发403错误
