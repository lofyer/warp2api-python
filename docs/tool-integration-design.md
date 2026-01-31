# OpenAI 客户端工具与 Warp 集成方案

## 问题分析

### OpenAI 客户端的工具模式
```
客户端 → 服务器（带 tools）
       ← AI 返回 tool_calls
客户端执行工具
客户端 → 服务器（带 tool 结果）
       ← AI 基于结果生成最终回答
```

### Warp 的工具模式
```
客户端 → Warp（带 MCPContext.tools）
       ← Warp AI 决定是否调用工具
       ← 如果调用：返回 CallMCPTool
客户端需要有 MCP 服务器来执行工具
客户端 → Warp（带 CallMCPToolResult）
       ← Warp 继续生成
```

### 核心矛盾
- OpenAI 模式：工具由**客户端**执行
- Warp 模式：工具由**MCP 服务器**执行
- 我们的服务器是中间层，需要桥接两者

## 解决方案对比

### 方案 1：完全模拟 OpenAI（推荐）

**原理**：不使用 Warp 的工具系统，完全在服务器端模拟 OpenAI 的工具调用行为。

**实现**：
1. 禁用 Warp 原生工具（`disable_warp_tools=true`）
2. 不将 tools 传给 Warp（因为 Warp 不会真正调用）
3. 在系统提示词中描述可用工具
4. 引导 AI 以特定格式输出工具调用意图
5. 服务器解析响应，构造 `tool_calls`
6. 客户端执行工具，返回结果
7. 服务器将结果添加到历史，继续请求

**优点**：
- ✓ 完全兼容 OpenAI API
- ✓ 客户端无需修改
- ✓ 不依赖 Warp 的 MCP 支持
- ✓ 工具由客户端执行（符合 OpenAI 模式）

**缺点**：
- ✗ 需要解析 AI 的自然语言输出
- ✗ 可能不够可靠（AI 可能不遵循格式）
- ✗ 需要额外的 token（工具描述）

### 方案 2：混合模式

**原理**：同时使用 Warp 原生工具和自定义工具。

**实现**：
1. 将 OpenAI tools 转换为 MCP 工具格式
2. 添加到 `MCPContext.tools`
3. 保留 Warp 原生工具
4. 当 Warp 返回 `CallMCPTool` 时：
   - 拦截并转换为 OpenAI `tool_calls`
   - 返回给客户端
5. 客户端执行工具，返回结果
6. 服务器转换为 `CallMCPToolResult`
7. 发送给 Warp，继续生成

**优点**：
- ✓ 利用 Warp AI 的工具选择能力
- ✓ 支持 Warp 原生工具 + 自定义工具
- ✓ 更智能的工具调用决策

**缺点**：
- ✗ 需要实现 MCP 工具执行逻辑
- ✗ **Warp 可能不会调用 MCP 工具**（当前测试结果）
- ✗ 复杂度高

### 方案 3：提示词引导（最简单）

**原理**：通过精心设计的提示词，引导 AI 以 JSON 格式输出工具调用。

**实现**：
1. 禁用 Warp 原生工具
2. 构造系统提示词：
```
You have access to the following tools:
- get_weather(location: string): Get weather information
- calculator(expression: string): Calculate math expression

To use a tool, respond ONLY with JSON in this format:
{"tool_call": {"name": "tool_name", "arguments": {...}}}

If you don't need a tool, respond normally.
```
3. 解析 AI 响应：
   - 尝试解析 JSON
   - 如果成功，构造 `tool_calls`
   - 如果失败，返回普通文本响应
4. 处理工具结果（同方案 1）

**优点**：
- ✓ 实现最简单
- ✓ 不依赖 Warp 的 MCP 支持
- ✓ 完全控制工具调用逻辑
- ✓ 可以逐步优化提示词

**缺点**：
- ✗ 依赖 AI 遵循格式
- ✗ 可能不够可靠
- ✗ 需要额外的 token

## 推荐方案

**采用方案 3（提示词引导）作为初始实现**，原因：

1. **当前 Warp 不支持自定义 MCP 工具**（测试结果）
2. **实现简单**，可以快速验证
3. **完全兼容 OpenAI API**
4. **可以后续升级**到方案 2（如果 Warp 支持）

## 实现计划

### 阶段 1：基础工具调用支持

1. **创建工具提示词生成器**
   - 将 OpenAI tools 转换为自然语言描述
   - 生成 JSON 格式规范
   - 添加到系统消息

2. **实现响应解析器**
   - 检测响应中的 JSON 工具调用
   - 提取工具名称和参数
   - 构造 OpenAI 格式的 `tool_calls`

3. **处理工具结果**
   - 接收 role="tool" 的消息
   - 将结果添加到对话历史
   - 继续请求 Warp

### 阶段 2：优化和增强

1. **改进提示词**
   - 测试不同的提示词格式
   - 提高工具调用的准确性

2. **错误处理**
   - 处理 JSON 解析失败
   - 处理工具执行错误
   - 提供友好的错误消息

3. **性能优化**
   - 缓存工具描述
   - 减少不必要的 token

### 阶段 3：高级功能

1. **并行工具调用**
   - 支持一次调用多个工具
   - 符合 OpenAI API 规范

2. **工具选择优化**
   - 使用更智能的提示词
   - 可能引入额外的 LLM 判断

3. **监控和日志**
   - 记录工具调用成功率
   - 分析常见失败模式

## 代码结构

```
warp2api-python/
├── core/
│   ├── tool_prompt.py          # 工具提示词生成
│   ├── tool_parser.py          # 工具调用解析
│   └── tool_handler.py         # 工具调用处理
├── server.py                   # 修改以支持工具
└── tests/
    └── test_tool_calling.py    # 工具调用测试
```

## 测试计划

1. **单元测试**
   - 提示词生成
   - JSON 解析
   - 工具调用构造

2. **集成测试**
   - 完整的工具调用流程
   - 多轮对话
   - 错误处理

3. **端到端测试**
   - 使用真实的 OpenAI 客户端
   - 测试各种工具场景
