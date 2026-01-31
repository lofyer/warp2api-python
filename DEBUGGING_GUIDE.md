# Warp API 调试指南

## 当前状态

- **请求大小**：327 bytes（真实请求：300 bytes，差 27 bytes）
- **HTTP 状态**：200 OK
- **问题**：响应为空（content-length: 0），没有 SSE 事件流

## 已完成的工作

1. ✅ 修复了模型映射（添加 claude-4-5-sonnet 和 claude-4-5-opus）
2. ✅ 实现了完整的 protobuf 请求构建
3. ✅ 添加了 InputContext（目录、OS、Shell、timestamp）
4. ✅ 正确设置了 Task.description（嵌入 UserInputs）
5. ✅ 设置了 Settings（model_config、supported_tools）
6. ✅ 设置了 Metadata（conversation_id、logging）

## 问题诊断

### 可能的原因

1. **账号权限问题**
   - 免费账号可能有 AI 功能限制
   - 需要特定的订阅或权限
   - 账号可能被标记或限制

2. **请求细节差异**
   - Metadata.logging 的 Struct 格式可能不匹配
   - 某些必需字段可能缺失或值不对
   - 27 bytes 的差异可能包含关键信息

3. **服务器端逻辑**
   - 可能需要特定的 client-id 或 session
   - 可能检查了某些隐藏的字段

## 建议的解决方案

### 方案 1：使用 mitmproxy 抓取完整请求（推荐）

```bash
# 1. 安装 mitmproxy
brew install mitmproxy

# 2. 启动 mitmproxy
mitmproxy -p 8080 --mode regular

# 3. 配置系统代理
export HTTP_PROXY=http://127.0.0.1:8080
export HTTPS_PROXY=http://127.0.0.1:8080

# 4. 在 Warp 终端中发送一条 AI 消息

# 5. 在 mitmproxy 中：
#    - 找到 POST https://app.warp.dev/ai/multi-agent
#    - 按 'e' 编辑
#    - 选择 'request body'
#    - 复制完整的 hex

# 6. 保存为文件
echo "0a56..." > real_request.hex

# 7. 对比我们的请求
python3 << 'EOF'
import sys
sys.path.insert(0, '.')
from warp2protobuf.core.protobuf import build_request_bytes

our_bytes = build_request_bytes('你好呀', 'claude-4-5-opus')
with open('real_request.hex', 'r') as f:
    real_hex = f.read().strip()
    real_bytes = bytes.fromhex(real_hex)

print(f"Our: {len(our_bytes)} bytes")
print(f"Real: {len(real_bytes)} bytes")
print(f"\nOur hex:\n{our_bytes.hex()}")
print(f"\nReal hex:\n{real_bytes.hex()}")

# 找出第一个不同的字节
for i, (a, b) in enumerate(zip(our_bytes, real_bytes)):
    if a != b:
        print(f"\nFirst difference at byte {i}:")
        print(f"  Our: 0x{a:02x}")
        print(f"  Real: 0x{b:02x}")
        break
EOF
```

### 方案 2：检查账号状态

1. 登录 https://app.warp.dev
2. 检查账号类型和权限
3. 在 Warp 终端中测试 AI 功能：
   ```bash
   # 在 Warp 终端中按 Ctrl+` 或点击 AI 按钮
   # 发送一条消息，看是否能正常工作
   ```
4. 如果不能使用，可能需要：
   - 升级账号
   - 申请 AI 功能访问权限
   - 使用不同的账号

### 方案 3：参考原始项目

```bash
# 1. 克隆原始项目
git clone https://github.com/lofyer/warp2openai.git
cd warp2openai

# 2. 查看他们的 protobuf 构建逻辑
# 特别关注：
#   - proto 文件的版本
#   - 请求构建的细节
#   - 是否有特殊的字段处理

# 3. 对比差异
diff -u warp2openai/proto/ /Users/lofyer/tmp/warp2api-python/proto/
```

### 方案 4：简化测试（二分法）

创建一个测试脚本，逐步添加字段：

```python
# test_minimal.py
import sys
sys.path.insert(0, '.')
from warp2protobuf.core.protobuf import get_request_schema, msg_cls
import uuid

full, path = get_request_schema()
Cls = msg_cls(full)

# 测试 1：最小请求（只有 Task）
msg1 = Cls()
task1 = msg1.task_context.tasks.add()
task1.id = str(uuid.uuid4())
print(f"Test 1 (minimal): {len(msg1.SerializeToString())} bytes")

# 测试 2：添加 UserInputs
msg2 = Cls()
task2 = msg2.task_context.tasks.add()
task2.id = str(uuid.uuid4())
user_inputs = msg2.input.user_inputs
user_input = user_inputs.inputs.add()
user_input.user_query.query = "你好呀"
task2.description = user_inputs.SerializeToString()
print(f"Test 2 (+ UserInputs): {len(msg2.SerializeToString())} bytes")

# 测试 3：添加 InputContext
msg3 = Cls()
task3 = msg3.task_context.tasks.add()
task3.id = str(uuid.uuid4())
# ... 添加 context
print(f"Test 3 (+ InputContext): {len(msg3.SerializeToString())} bytes")

# 依次测试，找出导致问题的字段
```

## 快速验证步骤

```bash
# 1. 测试当前实现
cd /Users/lofyer/tmp/warp2api-python
python3 server.py

# 2. 在另一个终端测试
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-dummy" \
  -d '{
    "model": "claude-4-5-opus",
    "messages": [{"role": "user", "content": "你好呀"}],
    "stream": true
  }'

# 3. 查看日志
tail -f logs/warp_api.log
```

## 需要收集的信息

如果问题持续，请收集以下信息：

1. **完整的真实请求 hex**（使用 mitmproxy）
2. **账号类型和权限**
3. **Warp 终端版本**
4. **是否能在 Warp 终端中正常使用 AI 功能**
5. **完整的错误日志**

## 联系支持

如果以上方法都不行，可以：

1. 在 GitHub 上提 issue：https://github.com/lofyer/warp2openai/issues
2. 查看 Warp 官方文档：https://docs.warp.dev
3. 联系 Warp 支持团队

## 总结

当前实现已经非常接近正确的格式（327 vs 300 bytes），但还差最后一步。最有效的方法是使用 mitmproxy 抓取完整的真实请求，然后逐字节对比找出差异。
