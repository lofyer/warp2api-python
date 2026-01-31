# Warp API 请求格式说明

本文档描述了 Warp Multi-Agent API 的 Protobuf 请求格式，基于真实客户端抓包分析。

## 概述

Warp 使用 Protobuf 格式进行 API 通信。请求消息类型为 `warp.multi_agent.v1.Request`。

## 请求结构

```protobuf
message Request {
    TaskContext task_context = 1;    // 历史对话上下文
    Input input = 2;                  // 当前用户输入
    Settings settings = 3;            // 设置（模型、工具等）
    Metadata metadata = 4;            // 元数据（conversation_id等）
}
```

## 单轮对话请求（第一条消息）

当用户发送第一条消息时，`task_context` 为空：

```
Request:
├── task_context: {} (空)
├── input:
│   ├── context:
│   │   ├── directory: {pwd: "/Users/xxx", home: "/Users/xxx"}
│   │   ├── operating_system: {platform: "MacOS"}
│   │   ├── shell: {name: "zsh", version: "5.9"}
│   │   └── current_time: <timestamp>
│   └── user_inputs:
│       └── inputs[0]:
│           └── user_query:
│               ├── query: "你好"
│               ├── attachments_bytes: "" (空)
│               └── is_new_conversation: true
├── settings:
│   ├── model_config: {base: "auto-genius", coding: "cli-agent-auto"}
│   ├── rules_enabled: true
│   ├── supports_parallel_tool_calls: true
│   ├── planning_enabled: true
│   ├── supported_tools: [6,7,12,8,9,15,14,0,11,16,10,20,17,19,18,2,3,1,13]
│   └── ... (其他设置)
└── metadata:
    ├── conversation_id: "<uuid>"
    └── logging: {entrypoint: "USER_INITIATED", ...}
```

### Hex 示例（第一条消息）
```
0a 00                          # task_context: 空 (field 1, length 0)
12 5a                          # input: (field 2, length 90)
  0a 43                        #   context: (field 1, length 67)
    0a 1e                      #     directory: (field 1)
      0a 0d /Users/lofyer      #       pwd
      12 0d /Users/lofyer      #       home
    12 07                      #     operating_system: (field 2)
      0a 05 MacOS              #       platform
    1a 0a                      #     shell: (field 3)
      0a 03 zsh                #       name
      12 03 5.9                #       version
    22 0c ...                  #     current_time: (field 4)
  32 13                        #   user_inputs: (field 6, length 19)
    0a 11                      #     inputs[0]: (field 1)
      0a 0f                    #       user_query: (field 1)
        0a 09 你好呀           #         query: "你好呀" (9 bytes UTF-8)
        1a 00                  #         attachments_bytes: "" (空)
        20 01                  #         is_new_conversation: true
1a 66 ...                      # settings: (field 3, length 102)
22 64 ...                      # metadata: (field 4, length 100)
```

## 多轮对话请求（后续消息）

当用户发送后续消息时，`task_context` 包含完整的对话历史：

```
Request:
├── task_context:
│   ├── tasks[0]:
│   │   ├── id: "<task-uuid>"
│   │   ├── description: "Greet And Initial Developer Setup"
│   │   ├── status: {in_progress: {}}
│   │   └── messages:
│   │       ├── [0] user_query:
│   │       │   ├── id: "<msg-uuid>"
│   │       │   ├── task_id: "<task-uuid>"
│   │       │   └── user_query:
│   │       │       ├── query: "你好"
│   │       │       └── context: {...}
│   │       └── [1] agent_output:
│   │           ├── id: "<msg-uuid>"
│   │           ├── task_id: "<task-uuid>"
│   │           └── agent_output:
│   │               └── text: "你好！👋\n\nI'm here to help..."
│   └── active_task_id: "<task-uuid>"
├── input:
│   ├── context: {...}
│   └── user_inputs:
│       └── inputs[0]:
│           └── user_query:
│               ├── query: "你好呀呀呀"
│               └── is_new_conversation: false  # 注意这里是 false
├── settings: {...}
└── metadata: {...}
```

## 消息类型

### Message (task.proto)

历史消息存储在 `Task.messages` 中，每条消息可以是以下类型之一：

```protobuf
message Message {
    string id = 1;
    string task_id = 11;
    
    oneof message {
        UserQuery user_query = 2;           // 用户输入
        AgentOutput agent_output = 3;       // AI 回复
        ToolCall tool_call = 4;             // 工具调用
        ToolCallResult tool_call_result = 5; // 工具结果
        ServerEvent server_event = 6;       // 服务器事件
    }
}
```

### UserQuery

```protobuf
message UserQuery {
    string query = 1;                    // 用户输入文本
    InputContext context = 2;            // 上下文信息
    map<string, Attachment> attachments = 3;
}
```

### AgentOutput

```protobuf
message AgentOutput {
    string text = 1;      // AI 回复文本
    string reasoning = 2; // 推理过程（可选）
}
```

## OpenAI 到 Warp 的转换

当从 OpenAI 格式转换到 Warp 格式时：

| OpenAI 消息 | Warp 位置 |
|------------|----------|
| `system` | 不支持，跳过 |
| `user` (历史) | `task_context.tasks[0].messages[].user_query` |
| `assistant` (历史) | `task_context.tasks[0].messages[].agent_output` |
| `user` (当前) | `input.user_inputs.inputs[0].user_query` |

### 转换示例

**OpenAI 格式:**
```json
{
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！我是AI助手"},
    {"role": "user", "content": "今天天气怎么样?"}
  ]
}
```

**Warp 格式:**
```
task_context:
  tasks[0]:
    messages:
      [0] user_query.query: "你好"
      [1] agent_output.text: "你好！我是AI助手"
  active_task_id: <task_id>

input:
  user_inputs:
    inputs[0]:
      user_query:
        query: "今天天气怎么样?"
        is_new_conversation: false
```

## Settings 字段

```protobuf
message Settings {
    ModelConfig model_config = 1;
    bool rules_enabled = 2;                      // true
    bool web_context_retrieval_enabled = 3;      // true
    bool supports_parallel_tool_calls = 4;       // true
    bool use_anthropic_text_editor_tools = 5;    // true
    bool planning_enabled = 6;                   // true
    bool warp_drive_context_enabled = 7;         // true
    bool supports_create_files = 8;              // true
    repeated ToolType supported_tools = 9;       // 工具列表
    bool supports_long_running_commands = 10;    // true
    bool should_preserve_file_content_in_history = 11; // true
    bool supports_todos_ui = 12;                 // true
    bool supports_linked_code_blocks = 13;       // true
    // ... 更多字段
}
```

### 工具类型 (ToolType)

```protobuf
enum ToolType {
    RUN_SHELL_COMMAND = 0;
    SEARCH_CODEBASE = 1;
    READ_FILES = 2;
    APPLY_FILE_DIFFS = 3;
    SUGGEST_PLAN = 4;
    SUGGEST_CREATE_PLAN = 5;
    GREP = 6;
    FILE_GLOB = 7;
    READ_MCP_RESOURCE = 8;
    CALL_MCP_TOOL = 9;
    WRITE_TO_LONG_RUNNING_SHELL_COMMAND = 10;
    SUGGEST_NEW_CONVERSATION = 11;
    FILE_GLOB_V2 = 12;
}
```

## 代码实现

历史消息的构建在 `warp2protobuf/core/protobuf.py` 中的 `build_request_bytes_with_history` 函数实现：

```python
def build_request_bytes(
    user_text: str, 
    model: str = "auto", 
    disable_warp_tools: bool = False,
    history_messages: Optional[List[Dict[str, Any]]] = None
) -> bytes:
    """
    构建 Warp API 请求
    
    Args:
        user_text: 当前用户输入
        model: 模型名称
        disable_warp_tools: 是否禁用Warp工具
        history_messages: 历史消息列表 [{"role": "user"|"assistant", "content": "..."}]
    """
```

## 调试模式 (DEBUG)

在 `config/settings.json` 中设置 `logging.level` 为 `DEBUG` 可以查看详细的请求和响应信息：

```json
{
  "logging": {
    "level": "DEBUG"
  }
}
```

或通过命令行：
```bash
python server.py --log-level DEBUG
```

### DEBUG 输出内容

DEBUG 模式会打印以下信息：

**1. 用户 API 请求 (OpenAI 格式)**
```
============================================================
[OpenAI Request] User API Request:
  Model: claude-4-sonnet
  Stream: True
  Messages (3):
    [0] user: 你好
    [1] assistant: 你好！我是AI助手
    [2] user: 今天天气怎么样?
============================================================
```

**2. 提交给 Warp 的请求**
```
============================================================
[Warp Request] Submitting to Warp API:
  Current query: 今天天气怎么样?
  Model: claude-4-sonnet
  History messages: 2
    [0] user: 你好
    [1] assistant: 你好！我是AI助手
  Protobuf size: 640 bytes
  Protobuf hex (first 200): 0aa7020afe010a24...
  task_context.active_task_id: f3ddd910-e9f1-43f6-95c9-ac4b3dc83349
  task_context.tasks[0].messages count: 2
============================================================
```

**3. Warp 返回的响应**
```
============================================================
[Warp Response] Response from Warp API:
  Total events: 15
  Response length: 256 chars
  Content: 今天天气晴朗...
============================================================
```

## 注意事项

1. **is_new_conversation**: 第一条消息为 `true`，后续消息为 `false`
2. **system 消息**: Warp 不支持 system role，会被跳过
3. **task_id**: 历史消息中的 `task_id` 应与 `active_task_id` 一致
4. **消息顺序**: 历史消息应按时间顺序排列
5. **DEBUG 模式**: 生产环境建议使用 INFO 级别，DEBUG 会输出大量日志
