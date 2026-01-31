# Anthropic API 兼容接口实现方案

## 概述

在现有 OpenAI 兼容接口基础上，增加 Anthropic Messages API 兼容接口，使项目同时支持两种主流 API 格式。

## API 差异对比

### 端点

| API | 端点 |
|-----|------|
| OpenAI | `POST /v1/chat/completions` |
| Anthropic | `POST /v1/messages` |

### 请求格式

**OpenAI:**
```json
{
  "model": "claude-4-sonnet",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "max_tokens": 1024
}
```

**Anthropic:**
```json
{
  "model": "claude-4-sonnet",
  "system": "You are a helpful assistant.",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "max_tokens": 1024
}
```

### 响应格式

**OpenAI (非流式):**
```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "claude-4-sonnet",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Hello! How can I help you?"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 8,
    "total_tokens": 18
  }
}
```

**Anthropic (非流式):**
```json
{
  "id": "msg_xxx",
  "type": "message",
  "role": "assistant",
  "model": "claude-4-sonnet",
  "content": [{
    "type": "text",
    "text": "Hello! How can I help you?"
  }],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 10,
    "output_tokens": 8
  }
}
```

### 流式响应格式

**OpenAI:**
```
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"Hello"}}]}

data: [DONE]
```

**Anthropic:**
```
event: message_start
data: {"type":"message_start","message":{"id":"msg_xxx","type":"message","role":"assistant","model":"claude-4-sonnet","content":[],"stop_reason":null,"usage":{"input_tokens":10,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":8}}

event: message_stop
data: {"type":"message_stop"}
```

### Tool Calls 格式

**OpenAI:**
```json
{
  "message": {
    "role": "assistant",
    "content": null,
    "tool_calls": [{
      "id": "call_xxx",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"location\":\"Beijing\"}"
      }
    }]
  }
}
```

**Anthropic:**
```json
{
  "content": [
    {
      "type": "tool_use",
      "id": "toolu_xxx",
      "name": "get_weather",
      "input": {"location": "Beijing"}
    }
  ],
  "stop_reason": "tool_use"
}
```

### Tool Results 格式

**OpenAI:**
```json
{
  "role": "tool",
  "tool_call_id": "call_xxx",
  "content": "{\"temperature\": 25}"
}
```

**Anthropic:**
```json
{
  "role": "user",
  "content": [{
    "type": "tool_result",
    "tool_use_id": "toolu_xxx",
    "content": "{\"temperature\": 25}"
  }]
}
```

## 实现计划

### 1. 新增文件

#### `core/anthropic_adapter.py`

```python
class AnthropicAdapter:
    """Anthropic 格式适配器"""
    
    @staticmethod
    def anthropic_to_internal_messages(system: str, messages: list) -> list:
        """将 Anthropic 消息格式转换为内部格式"""
        pass
    
    @staticmethod
    async def warp_to_anthropic_stream(warp_stream, model: str):
        """将 Warp 流转换为 Anthropic SSE 格式"""
        pass
    
    @staticmethod
    async def warp_to_anthropic_response(warp_stream, model: str) -> dict:
        """将 Warp 流转换为 Anthropic 非流式响应"""
        pass
```

### 2. 修改 `server.py`

新增 Pydantic 模型：

```python
class AnthropicContentBlock(BaseModel):
    type: str  # "text" | "image" | "tool_use" | "tool_result"
    text: Optional[str] = None
    # ... 其他字段

class AnthropicMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: Union[str, List[AnthropicContentBlock]]

class AnthropicMessagesRequest(BaseModel):
    model: str
    messages: List[AnthropicMessage]
    system: Optional[str] = None
    max_tokens: int
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    tools: Optional[List[dict]] = None
    # ...
```

新增路由：

```python
@app.post("/v1/messages")
async def anthropic_messages(request: AnthropicMessagesRequest):
    """Anthropic Messages API 兼容端点"""
    return await handle_anthropic_completion(request)
```

### 3. 转换流程

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────┐
│ Anthropic       │     │ Internal         │     │ Warp        │
│ Request         │ ──> │ Format           │ ──> │ API         │
└─────────────────┘     └──────────────────┘     └─────────────┘
                                                       │
┌─────────────────┐     ┌──────────────────┐           │
│ Anthropic       │     │ Internal         │           │
│ Response        │ <── │ Response         │ <─────────┘
└─────────────────┘     └──────────────────┘
```

## 实现步骤

1. **创建 `core/anthropic_adapter.py`**
   - 实现消息格式转换
   - 实现流式响应转换
   - 实现非流式响应转换
   - 实现 Tool calls 转换

2. **修改 `server.py`**
   - 添加 Anthropic 请求/响应模型
   - 添加 `/v1/messages` 路由
   - 添加处理函数 `handle_anthropic_completion()`

3. **测试**
   - 非流式文本响应
   - 流式文本响应
   - Tool calls 功能
   - 错误处理

## 注意事项

1. **认证方式**: Anthropic 使用 `x-api-key` header，OpenAI 使用 `Authorization: Bearer xxx`
2. **错误格式**: Anthropic 错误响应格式与 OpenAI 不同
3. **模型映射**: 可能需要处理模型名称映射
4. **多模态**: Anthropic 的图片格式与 OpenAI 略有不同

## 参考文档

- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)
- [Anthropic Streaming](https://docs.anthropic.com/en/api/messages-streaming)
- [OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat)
