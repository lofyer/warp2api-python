# Warp2API 消息流转与工具调用流程

本文档详细描述了 Warp2API 中消息的流转过程，以及工具调用的完整流程。

---

## 目录

1. [消息流转流程](#消息流转流程)
2. [工具调用流程](#工具调用流程)
3. [多轮工具调用](#多轮工具调用)
4. [关键代码位置](#关键代码位置)
5. [已知问题与解决方案](#已知问题与解决方案)

---

## 消息流转流程

### 1. 整体流程图

```
┌─────────────┐
│   客户端     │
│  (OpenAI)   │
└──────┬──────┘
       │ POST /v1/chat/completions
       │ {messages: [...], tools: [...]}
       ▼
┌─────────────────────────────────────────────────────────┐
│                    server.py                             │
│  @app.post("/v1/chat/completions")                      │
│  async def chat_completions(request)                    │
└──────┬──────────────────────────────────────────────────┘
       │
       │ 1. 提取消息列表
       ▼
┌─────────────────────────────────────────────────────────┐
│              core/warp_client.py                         │
│  WarpClient.chat_completion(messages, model, tools)     │
│                                                          │
│  ┌────────────────────────────────────────────┐        │
│  │ 消息分类逻辑 (lines 320-380)               │        │
│  │                                             │        │
│  │ 1. 找到最后一个 user 消息                  │        │
│  │ 2. 提取最后一个 user 之后的所有 tool 结果  │        │
│  │ 3. 将之前的消息放入 history_messages       │        │
│  └────────────────────────────────────────────┘        │
└──────┬──────────────────────────────────────────────────┘
       │
       │ 2. 构建 Protobuf 请求
       ▼
┌─────────────────────────────────────────────────────────┐
│         warp2protobuf/core/protobuf.py                   │
│  build_request_bytes(user_text, history, tool_results)  │
│                                                          │
│  ┌────────────────────────────────────────────┐        │
│  │ 如果有 tool_results:                       │        │
│  │   - 将 history + tool_results 组合成文本   │        │
│  │   - 格式: "User: ...\nAssistant: ...\n     │        │
│  │            Tool result (id): ..."          │        │
│  │   - 设置 is_new_conversation = False       │        │
│  │                                             │        │
│  │ 如果没有 tool_results:                     │        │
│  │   - 只包含 history + 当前 user 消息        │        │
│  │   - 设置 is_new_conversation = True/False  │        │
│  └────────────────────────────────────────────┘        │
└──────┬──────────────────────────────────────────────────┘
       │
       │ 3. 发送到 Warp API
       ▼
┌─────────────────────────────────────────────────────────┐
│                   Warp API                               │
│  https://app.warp.dev/api/v1/ai/chat                    │
└──────┬──────────────────────────────────────────────────┘
       │
       │ 4. SSE 流式响应
       ▼
┌─────────────────────────────────────────────────────────┐
│         core/warp_client.py                              │
│  _parse_sse_stream() - 解析 SSE 事件                    │
│                                                          │
│  事件类型:                                               │
│  - data: 文本内容增量                                    │
│  - client_actions: 工具调用、任务状态等                 │
│  - finished: 流结束                                      │
└──────┬──────────────────────────────────────────────────┘
       │
       │ 5. 转换为 OpenAI 格式
       ▼
┌─────────────────────────────────────────────────────────┐
│         core/openai_adapter.py                           │
│  warp_to_openai_stream() - 转换流式响应                 │
│                                                          │
│  ┌────────────────────────────────────────────┐        │
│  │ MCP Gateway 转换:                          │        │
│  │   call_mcp_tool -> 实际工具名称            │        │
│  │                                             │        │
│  │ 示例:                                       │        │
│  │   call_mcp_tool(name="Execute", args={})   │        │
│  │   ↓                                         │        │
│  │   Execute(command="...", riskLevel="...")  │        │
│  └────────────────────────────────────────────┘        │
└──────┬──────────────────────────────────────────────────┘
       │
       │ 6. 返回给客户端
       ▼
┌─────────────┐
│   客户端     │
│  (OpenAI)   │
└─────────────┘
```

---

## 工具调用流程

### 1. 工具定义传递

```
客户端请求:
{
  "messages": [...],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "Execute",
        "description": "执行命令",
        "parameters": {...}
      }
    }
  ]
}

↓ server.py 接收

↓ warp_client.py 传递给 protobuf.py

↓ protobuf.py: add_mcp_tools_to_request()
  - 将 OpenAI 工具格式转换为 Warp MCP 格式
  - 添加到 request.input.mcp_servers[0].tools

↓ 发送到 Warp API
```

### 2. 工具调用响应

```
Warp API 返回:
event: data
data: {...}  # client_actions.add_messages_to_task

解析后的结构:
{
  "client_actions": {
    "add_messages_to_task": {
      "messages": [
        {
          "assistant_message": {
            "tool_calls": [
              {
                "id": "toolu_xxx",
                "type": "function",
                "function": {
                  "name": "call_mcp_tool",
                  "arguments": "{\"name\":\"Execute\",\"args\":{...}}"
                }
              }
            ]
          }
        }
      ]
    }
  }
}

↓ openai_adapter.py: transform_mcp_tool_call()
  - 检测到 call_mcp_tool
  - 解析 arguments 中的 name 和 args
  - 转换为实际工具调用

转换后:
{
  "id": "toolu_xxx",
  "type": "function",
  "function": {
    "name": "Execute",
    "arguments": "{\"command\":\"...\",\"riskLevel\":\"...\"}"
  }
}

↓ 返回给客户端
```

### 3. 工具结果提交

```
客户端提交工具结果:
{
  "messages": [
    {"role": "user", "content": "帮我执行命令"},
    {"role": "assistant", "content": "", "tool_calls": [...]},
    {"role": "tool", "tool_call_id": "toolu_xxx", "content": "命令输出"}
  ]
}

↓ warp_client.py: 消息分类
  - last_user_idx = 0 (第一条消息)
  - tool_results = [{"tool_call_id": "toolu_xxx", "content": "命令输出"}]
  - history_messages = [{"role": "assistant", "tool_calls": [...]}]

↓ protobuf.py: build_request_bytes_with_history()
  - 将 tool_results 转换为文本格式
  - 组合成完整的 query:
    """
    User: 帮我执行命令
    Assistant: 
    Tool calls: Called Execute with args: {...}
    Tool result (toolu_xxx): 命令输出
    """

↓ 发送到 Warp API
  - Warp 将工具结果作为上下文理解
  - 生成后续响应
```

---

## 多轮工具调用

### 场景：连续调用多个工具

```
第1轮:
User: "帮我查看日志并分析"
  ↓
Assistant: [调用 Execute 工具读取日志]
  ↓
Tool: [返回日志内容]

第2轮:
Assistant: [分析日志，再次调用 Execute 工具]
  ↓
Tool: [返回分析结果]

第3轮:
Assistant: [总结分析结果]
```

### 消息列表结构

```python
messages = [
    {"role": "user", "content": "帮我查看日志并分析"},           # 索引 0
    {"role": "assistant", "content": "", "tool_calls": [...]},   # 索引 1
    {"role": "tool", "tool_call_id": "call_1", "content": "..."}, # 索引 2
    {"role": "assistant", "content": "", "tool_calls": [...]},   # 索引 3
    {"role": "tool", "tool_call_id": "call_2", "content": "..."}, # 索引 4
]
```

### 工具结果提取逻辑

**关键代码** (`core/warp_client.py` lines 328-352):

```python
# 找到最后一个用户消息的位置
last_user_idx = -1
for i, msg in enumerate(messages):
    if msg.get("role") == "user":
        last_user_idx = i

# 提取最后一个用户消息之后的所有工具结果
for i, msg in enumerate(messages):
    if msg.get("role") == "tool":
        if i > last_user_idx:
            tool_results.append({
                "tool_call_id": msg.get("tool_call_id"),
                "content": msg.get("content", "")
            })
```

**为什么从 last_user_idx 开始？**

- 用户提问后，可能有多轮 assistant → tool 的交互
- 我们需要提取这一轮对话中的所有工具结果
- 配合 `max_tool_results: 10` 限制，只保留最近的 10 个结果

**示例**:

```
messages = [
    user (idx=0): "帮我分析"
    assistant (idx=1): [call tool_1]
    tool (idx=2): result_1
    assistant (idx=3): [call tool_2]
    tool (idx=4): result_2
    assistant (idx=5): [call tool_3]
    tool (idx=6): result_3
]

last_user_idx = 0
提取的 tool_results = [result_1, result_2, result_3]
```

---

## 关键代码位置

### 1. 消息分类与工具结果提取

**文件**: `core/warp_client.py`  
**函数**: `chat_completion()`  
**行数**: 320-400

```python
# 找到最后一个用户消息的位置
last_user_idx = -1
for i, msg in enumerate(messages):
    if msg.get("role") == "user":
        last_user_idx = i

# 提取工具结果
for i, msg in enumerate(messages):
    role = msg.get("role", "")
    
    if role == "tool":
        if i > last_user_idx:
            tool_results.append({
                "tool_call_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", "")
            })
```

### 2. Protobuf 请求构建

**文件**: `warp2protobuf/core/protobuf.py`  
**函数**: `build_request_bytes_with_history()`  
**行数**: 556-620

```python
if tool_results and len(tool_results) > 0:
    # 构建包含工具结果的上下文
    context_parts = []
    
    # 添加历史消息
    for msg in history_messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            context_parts.append(f"User: {content}")
        elif role == "assistant":
            if msg.get("tool_calls"):
                tool_calls_desc = []
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    tool_calls_desc.append(
                        f"Called {func.get('name')} with args: {func.get('arguments')}"
                    )
                context_parts.append(
                    f"Assistant: {content}\nTool calls: {'; '.join(tool_calls_desc)}"
                )
            else:
                context_parts.append(f"Assistant: {content}")
    
    # 添加工具结果
    for tool_result in tool_results:
        tool_call_id = tool_result.get("tool_call_id", "")
        content = tool_result.get("content", "")
        context_parts.append(f"Tool result ({tool_call_id}): {content}")
    
    # 组合成完整 query
    full_query = "\n\n".join(context_parts)
    input_msg.user_query.query = full_query
    input_msg.user_query.is_new_conversation = False
```

### 3. MCP 工具转换

**文件**: `core/openai_adapter.py`  
**函数**: `transform_mcp_tool_call()`  
**行数**: 27-86

```python
@staticmethod
def transform_mcp_tool_call(tool_call: dict) -> dict:
    """
    MCP Gateway: 将 call_mcp_tool 转换为实际的工具调用
    """
    try:
        func = tool_call.get("function", {})
        func_name = func.get("name", "")
        
        # 只处理 call_mcp_tool
        if func_name != "call_mcp_tool":
            return tool_call
        
        # 解析 arguments
        args_str = func.get("arguments", "{}")
        args = json.loads(args_str)
        
        # 提取实际工具名称和参数
        actual_tool_name = args.get("name")
        actual_tool_args = args.get("args", {})
        
        if not actual_tool_name:
            return tool_call
        
        # 构建新的 tool_call
        new_tool_call = {
            "id": tool_call.get("id"),
            "type": "function",
            "function": {
                "name": actual_tool_name,
                "arguments": json.dumps(actual_tool_args, ensure_ascii=False)
            }
        }
        
        logger.info(f"[MCP Gateway] Transformed call_mcp_tool -> {actual_tool_name}")
        return new_tool_call
        
    except Exception as e:
        logger.error(f"[MCP Gateway] Failed to transform: {e}")
        return tool_call
```

### 4. 工具定义转换

**文件**: `warp2protobuf/core/tool_converter.py`  
**函数**: `add_mcp_tools_to_request()`  
**行数**: 完整文件

```python
def add_mcp_tools_to_request(request, tools: List[Dict[str, Any]]):
    """
    将 OpenAI 格式的工具列表添加到 Warp protobuf 请求中
    """
    if not tools:
        return
    
    # 创建 MCP server
    mcp_server = request.input.mcp_servers.add()
    mcp_server.name = "custom_tools"
    
    # 添加每个工具
    for tool in tools:
        if tool.get("type") != "function":
            continue
        
        func = tool.get("function", {})
        tool_def = mcp_server.tools.add()
        tool_def.name = func.get("name", "")
        tool_def.description = func.get("description", "")
        
        # 转换参数 schema
        parameters = func.get("parameters", {})
        if parameters:
            tool_def.input_schema = json.dumps(parameters)
```

---

## 已知问题与解决方案

### 问题 1: 多个工具结果丢失

**症状**: 
- 连续调用多个工具时，只有最后一个工具的结果被提交
- 导致 AI 无法看到之前的工具执行结果

**原因**:
- 之前的逻辑从 `last_assistant_idx` 开始提取工具结果
- 如果有多个 assistant → tool 循环，只会提取最后一轮的结果

**解决方案** (已修复):
```python
# 修改前: 从最后一个 assistant 开始
if i > last_assistant_idx:
    tool_results.append(...)

# 修改后: 从最后一个 user 开始
if i > last_user_idx:
    tool_results.append(...)
```

**验证**:
- 日志中 `Tool results: 1` → `Tool results: 4` 或更多
- 多轮工具调用能正常工作

### 问题 2: 工具结果过多导致请求过大

**症状**:
- 长时间对话后，工具结果累积过多
- 请求体积过大，可能导致 API 错误

**解决方案**:
```python
# config/settings.json
{
  "max_tool_results": 10,
  "max_history_messages": 50
}

# core/warp_client.py
if tool_results and len(tool_results) > max_tool_results:
    logger.info(f"Limiting tool results from {len(tool_results)} to {max_tool_results}")
    tool_results = tool_results[-max_tool_results:]
```

### 问题 3: call_mcp_tool 未转换

**症状**:
- 客户端收到 `call_mcp_tool` 而不是实际工具名称
- 工具调用无法执行

**解决方案**:
- 在 `openai_adapter.py` 中添加 `transform_mcp_tool_call()`
- 在流式和非流式响应中都进行转换

**关键代码**:
```python
# 流式响应中转换
for tool_call in tool_calls:
    transformed = OpenAIAdapter.transform_mcp_tool_call(tool_call)
    # 使用 transformed

# 非流式响应中转换
for tool_call in message.get("tool_calls", []):
    transformed = OpenAIAdapter.transform_mcp_tool_call(tool_call)
    # 使用 transformed
```

### 问题 4: 工具结果格式不正确

**症状**:
- Warp API 无法理解工具结果
- AI 响应不包含工具执行结果的分析

**解决方案**:
- 将工具结果转换为文本格式，而不是使用 Protobuf 的 ToolCallResult
- 格式化为易读的上下文

**示例**:
```python
# 不使用 Protobuf ToolCallResult
# 而是转换为文本:
context_parts = [
    "User: 帮我执行命令",
    "Assistant: \nTool calls: Called Execute with args: {...}",
    "Tool result (toolu_xxx): 命令执行成功\n输出: ..."
]
full_query = "\n\n".join(context_parts)
```

### 问题 5: 工具执行后 AI 重复调用工具而不生成回答

**症状**:
- 工具执行完成后，AI 不分析结果，而是继续调用工具
- 形成无限循环的工具调用
- 日志中看到多次连续的工具调用请求

**原因**:
- 当工具结果返回后，第二次请求仍然包含历史中的用户消息
- Warp AI 收不到明确的"继续生成回答"指令
- AI 可能认为需要继续执行工具来完成任务

**解决方案** (2026-01-27 修复):

**步骤 1**: 在 `warp_client.py` 中检测最后一条消息是否是工具结果：

```python
# core/warp_client.py (lines 355-366)
# 判断是否是最后一条用户消息
is_last_user = (role == "user" and 
               all(m.get("role") != "user" for m in messages[i+1:]))

if is_last_user:
    # 检查最后一条消息是否是工具结果
    # 如果是，说明用户消息只是历史上下文，不是新消息
    if messages and messages[-1].get("role") == "tool":
        # 不设置 user_message，让它保持空字符串
        pass
    else:
        user_message = content or ""  # 确保是字符串
```

**步骤 2**: 在 `protobuf.py` 中添加隐式继续指令：

```python
# warp2protobuf/core/protobuf.py (lines 603-612)
# 添加当前用户查询
if user_text and user_text.strip():
    context_parts.append(f"User: {user_text}")
else:
    # 如果只有工具结果没有新的用户消息，添加隐式继续指令
    context_parts.append("User: Please analyze the tool results above and provide your response.")
    logger.debug("Added implicit continuation instruction for tool results")
```

**效果**:
- 第一次请求（用户发起）：正常发送用户消息
- 第二次请求（工具结果返回）：
  - 检测到 `messages[-1].get("role") == "tool"`
  - `user_message` 保持为空字符串
  - 触发隐式继续指令
  - Warp AI 收到明确指令后生成最终回答

**验证日志**:
```bash
# 查看是否触发了隐式继续指令
tail -f logs/warp_api.log | grep "Added implicit continuation instruction"
```

**完整请求格式** (第二次请求):
```
User: 查看配置文件

Assistant: 
Tool calls: Called Execute with args: {"command": "cat config.json"}

Tool result (toolu_xxx): {配置文件内容...}

User: Please analyze the tool results above and provide your response.
```

---

## 配置参数

### settings.json

```json
{
  "max_tool_results": 10,
  "max_history_messages": 50,
  "disable_warp_tools": false
}
```

**参数说明**:

- `max_tool_results`: 发送给 Warp 的工具结果最大数量，默认 10
- `max_history_messages`: 发送给 Warp 的历史消息最大数量，默认 50
- `disable_warp_tools`: 是否禁用 Warp 内置工具，默认 false

---

## 调试技巧

### 1. 查看消息分类日志

```bash
tail -f logs/warp_api.log | grep -E "Extracted tool result|Tool results:"
```

### 2. 查看 Protobuf 请求内容

```bash
tail -f logs/warp_api.log | grep -E "\[Warp Request\]" -A 20
```

### 3. 查看工具转换日志

```bash
tail -f logs/warp_api.log | grep -E "\[MCP Gateway\]"
```

### 4. 完整请求响应日志

```bash
tail -f logs/warp_api.log | grep -E "============"
```

---

## 总结

### 消息流转关键点

1. **消息分类**: 区分 user、assistant、tool 消息
2. **工具结果提取**: 从最后一个 user 消息开始提取所有 tool 结果
3. **上下文构建**: 将 history + tool_results 组合成文本格式
4. **Protobuf 编码**: 构建完整的 Warp API 请求

### 工具调用关键点

1. **工具定义**: OpenAI 格式 → Warp MCP 格式
2. **工具调用**: call_mcp_tool → 实际工具名称
3. **工具结果**: 文本格式上下文，而非 Protobuf 结构
4. **多轮调用**: 保留所有工具结果，配合限制参数

### 最佳实践

1. 合理设置 `max_tool_results` 和 `max_history_messages`
2. 监控日志中的工具结果数量
3. 使用 DEBUG 日志级别排查问题
4. 定期清理长时间对话的历史消息
