#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Protobuf runtime for Warp API

Handles protobuf compilation, message creation, and request building.
"""
import os
import re
import json
import time
import uuid
import pathlib
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from google.protobuf import descriptor_pool, descriptor_pb2
from google.protobuf.descriptor import FieldDescriptor as FD
from google.protobuf.message_factory import GetMessageClass
from google.protobuf import struct_pb2

from ..config.settings import PROTO_DIR, CLIENT_VERSION, OS_CATEGORY, OS_NAME, OS_VERSION, TEXT_FIELD_NAMES, PATH_HINT_BONUS
from .logging import logger, log

# Global protobuf state
_pool: Optional[descriptor_pool.DescriptorPool] = None
ALL_MSGS: List[str] = []


def _find_proto_files(root: pathlib.Path) -> List[str]:
    """Find necessary .proto files in the given directory, excluding problematic test files"""
    if not root.exists():
        return []
    
    essential_files = [
        "request.proto",
        "response.proto", 
        "task.proto",
        "attachment.proto",
        "file_content.proto",
        "input_context.proto",
        "citations.proto"
    ]
    
    found_files = []
    for file_name in essential_files:
        file_path = root / file_name
        if file_path.exists():
            found_files.append(str(file_path))
            logger.debug(f"Found essential proto file: {file_name}")
    
    if not found_files:
        logger.warning("Essential proto files not found, scanning all files...")
        exclude_patterns = [
            "unittest", "test", "sample_messages", "java_features", 
            "legacy_features", "descriptor_test"
        ]
        
        for proto_file in root.rglob("*.proto"):
            file_name = proto_file.name.lower()
            if not any(pattern in file_name for pattern in exclude_patterns):
                found_files.append(str(proto_file))
    
    logger.info(f"Selected {len(found_files)} proto files for compilation")
    return found_files


def _build_descset(proto_files: List[str], includes: List[str]) -> bytes:
    from grpc_tools import protoc
    try:
        from importlib.resources import files as pkg_files
        tool_inc = str(pkg_files("grpc_tools").joinpath("_proto"))
    except Exception:
        tool_inc = None

    outdir = pathlib.Path(tempfile.mkdtemp(prefix="desc_"))
    out = outdir / "bundle.pb"
    args = ["protoc", f"--descriptor_set_out={out}", "--include_imports"]
    for inc in includes:
        args.append(f"-I{inc}")
    if tool_inc:
        args.append(f"-I{tool_inc}")
    args.extend(proto_files)
    rc = protoc.main(args)
    if rc != 0 or not out.exists():
        raise RuntimeError("protoc failed to produce descriptor set")
    return out.read_bytes()


def _load_pool_from_descset(descset: bytes):
    global _pool, ALL_MSGS
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(descset)
    pool = descriptor_pool.DescriptorPool()
    for fd in fds.file:
        pool.Add(fd)
    names: List[str] = []
    for fd in fds.file:
        pkg = fd.package
        def walk(m, prefix):
            full = f"{prefix}.{m.name}" if prefix else m.name
            names.append(full)
            for nested in m.nested_type:
                walk(nested, full)
        for m in fd.message_type:
            walk(m, pkg)
    _pool, ALL_MSGS = pool, names
    log(f"proto loaded: {len(ALL_MSGS)} message type(s)")


def ensure_proto_runtime():
    if _pool is not None: 
        return
    files = _find_proto_files(PROTO_DIR)
    if not files:
        raise RuntimeError(f"No .proto found under {PROTO_DIR}")
    desc = _build_descset(files, [str(PROTO_DIR)])
    _load_pool_from_descset(desc)


def msg_cls(full: str):
    desc = _pool.FindMessageTypeByName(full)  # type: ignore
    return GetMessageClass(desc)


def _list_text_paths(desc, max_depth=6):
    out: List[Tuple[List[FD], int]] = []
    def walk(cur_desc, cur_path: List[FD], depth: int):
        if depth > max_depth:
            return
        for f in cur_desc.fields:
            base = 0
            if f.name.lower() in TEXT_FIELD_NAMES: base += 10
            for hint in PATH_HINT_BONUS:
                if hint in f.name.lower(): base += 2
            if f.type == FD.TYPE_STRING:
                out.append((cur_path + [f], base + depth))
            elif f.type == FD.TYPE_MESSAGE:
                walk(f.message_type, cur_path + [f], depth + 1)
    walk(desc, [], 0)
    return out


def _pick_best_request_schema() -> Tuple[str, List[FD]]:
    ensure_proto_runtime()
    try:
        request_type = "warp.multi_agent.v1.Request"
        d = _pool.FindMessageTypeByName(request_type)  # type: ignore
        path_names = ["input", "user_inputs", "inputs", "user_query", "query"]
        path_fields = []
        current_desc = d
        
        for field_name in path_names:
            field = current_desc.fields_by_name.get(field_name)
            if not field:
                raise RuntimeError(f"Field '{field_name}' not found")
            path_fields.append(field)
            if field.type == FD.TYPE_MESSAGE:
                current_desc = field.message_type
        
        log("using modern request format:", request_type, " :: ", ".".join(path_names))
        return request_type, path_fields
        
    except Exception as e:
        log(f"Failed to use modern format, falling back to auto-detection: {e}")
        best: Optional[Tuple[str, List[FD], int]] = None
        for full in ALL_MSGS:
            try:
                d = _pool.FindMessageTypeByName(full)  # type: ignore
            except Exception:
                continue
            name_bias = 0
            lname = full.lower()
            for kw, w in (("request", 8), ("multi_agent", 6), ("multiagent", 6),
                          ("chat", 5), ("client", 2), ("message", 1), ("input", 1)):
                if kw in lname: name_bias += w
            for path, score in _list_text_paths(d):
                total = score + name_bias + max(0, 6 - len(path))
                if best is None or total > best[2]:
                    best = (full, path, total)
        if not best:
            raise RuntimeError("Could not auto-detect request root & text field from proto/")
        full, path, _ = best
        log("auto-detected request:", full, " :: ", ".".join(f.name for f in path))
        return full, path


_REQ_CACHE: Optional[Tuple[str, List[FD]]] = None

def get_request_schema() -> Tuple[str, List[FD]]:
    global _REQ_CACHE
    if _REQ_CACHE is None:
        _REQ_CACHE = _pick_best_request_schema()
    return _REQ_CACHE


def _set_text_at_path(msg, path_fields: List[FD], text: str):
    cur = msg
    for i, f in enumerate(path_fields):
        last = (i == len(path_fields) - 1)
        try:
            is_repeated = f.is_repeated
        except AttributeError:
            is_repeated = (f.label == FD.LABEL_REPEATED)
        
        if is_repeated:
            rep = getattr(cur, f.name)
            if f.type == FD.TYPE_MESSAGE:
                cur = rep.add()
            elif f.type == FD.TYPE_STRING:
                if not last: raise TypeError(f"path continues after repeated string field '{f.name}'")
                rep.append(text); return
            else:
                raise TypeError(f"unsupported repeated scalar at '{f.name}'")
        else:
            if f.type == FD.TYPE_MESSAGE:
                cur = getattr(cur, f.name)
                if last:
                    raise TypeError(f"last field '{f.name}' is a message, not string")
            elif f.type == FD.TYPE_STRING:
                if not last: raise TypeError(f"path continues after string field '{f.name}'")
                setattr(cur, f.name, text); return
            else:
                raise TypeError(f"unsupported scalar at '{f.name}'")
    raise RuntimeError("failed to set text")


# 真实请求模板 (query="你好呀", 9 bytes UTF-8)
# 这个模板已被验证可以正常工作
_REAL_REQUEST_TEMPLATE = bytes.fromhex(
    "0a00125a0a430a1e0a0d2f55736572732f6c6f66796572120d2f55736572732f6c6f6679657212070a054d61634f531a0a0a037a73681203352e39220c08eeb8d3cb0610908ef0bd0232130a110a0f0a09e4bda0e5a5bde591801a0020011a660a210a0f636c617564652d342d352d6f707573220e636c692d6167656e742d6175746f1001180120013001380140014a1306070c08090f0e000b100a141113120203010d500158016001680170017801800101880101a80101b201070a1406070c0201b801012264121e0a0a656e747279706f696e7412101a0e555345525f494e4954494154454412200a1a69735f6175746f5f726573756d655f61667465725f6572726f721202200012200a1a69735f6175746f64657465637465645f757365725f717565727912022001"
)

# 模板中 query 的位置信息
# query "你好呀" = e4bda0e5a5bde59180 (9 bytes) 位于 offset 80-88
_TEMPLATE_QUERY_OFFSET = 80
_TEMPLATE_QUERY_LEN = 9
_TEMPLATE_ORIGINAL_QUERY = "你好呀"


def _encode_varint(value: int) -> bytes:
    """编码 protobuf varint"""
    result = []
    while value > 127:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _varint_len(value: int) -> int:
    """计算 varint 编码后的字节数"""
    if value < 128:
        return 1
    elif value < 16384:
        return 2
    elif value < 2097152:
        return 3
    else:
        return 4


def build_request_bytes_from_template(user_text: str, model: str = "auto", disable_warp_tools: bool = False, tools: Optional[List[Dict[str, Any]]] = None) -> bytes:
    """
    使用真实请求模板构建请求，动态替换 query 部分
    
    这是最可靠的方法，因为模板已被验证可以正常工作
    
    Args:
        user_text: 用户输入的文本
        model: 模型名称
        disable_warp_tools: 是否禁用Warp内置工具
        tools: OpenAI格式的工具列表
    """
    template = bytearray(_REAL_REQUEST_TEMPLATE)
    
    # 编码新 query
    new_query_bytes = user_text.encode('utf-8')
    new_query_len = len(new_query_bytes)
    old_query_len = _TEMPLATE_QUERY_LEN  # 9 bytes for "你好呀"
    
    # 计算长度差异
    len_diff = new_query_len - old_query_len
    
    logger.debug(f"Template query replacement: old={old_query_len} bytes, new={new_query_len} bytes, diff={len_diff}")
    
    # 构建新的 user_query 内容
    # 结构: 0a [query_len] [query] 1a 00 20 01
    # 其中 0a = field 1 (query), 1a = field 3 (attachments_bytes), 20 = field 4 (is_new_conversation)
    user_query_content = b'\x0a' + _encode_varint(new_query_len) + new_query_bytes + b'\x1a\x00\x20\x01'
    user_query_total_len = len(user_query_content)
    
    # 在模板中找到并替换 user_query 部分
    # 模板结构：
    # - 位置 0-1: 0a 00 (TaskContext)
    # - 位置 2-3: 12 5a (Input, length=90)
    # - Input 内容开始于位置 4
    #   - context 占 67 bytes (0a 43 ...)
    #   - user_inputs 在 context 之后 (32 13 ...)
    
    # 完整重建 user_inputs 部分
    # user_inputs 结构: 32 [len] 0a [inputs_len] 0a [user_input_len] [user_query_content]
    user_input_content = b'\x0a' + _encode_varint(user_query_total_len) + user_query_content
    inputs_content = b'\x0a' + _encode_varint(len(user_input_content)) + user_input_content
    user_inputs_content = b'\x32' + _encode_varint(len(inputs_content)) + inputs_content
    
    # 找到 user_inputs 在 Input 中的起始位置
    # Input 从位置 4 开始，context 长度为 67 bytes (0a 43 后面 67 bytes)
    # context tag+len = 2 bytes, context content = 67 bytes
    # 所以 user_inputs 从位置 4 + 2 + 67 = 73 开始
    user_inputs_start = 4 + 2 + 67  # 73
    
    # 验证模板中 user_inputs 的起始位置
    if template[user_inputs_start] != 0x32:
        logger.warning(f"Expected 0x32 at position {user_inputs_start}, got {hex(template[user_inputs_start])}")
        # 回退到搜索方式
        user_inputs_start = bytes(template).find(b'\x32\x13')
        if user_inputs_start == -1:
            raise RuntimeError("Cannot find user_inputs in template")
    
    # 原始 user_inputs 长度 (32 13 后面的内容)
    old_user_inputs_len = template[user_inputs_start + 1]  # 0x13 = 19
    old_user_inputs_total = 2 + old_user_inputs_len  # tag + len byte + content
    
    # 构建新的 Input 内容
    # context 部分不变
    context_start = 4  # Input content 开始位置
    context_end = user_inputs_start
    context_part = bytes(template[context_start:context_end])
    
    new_input_content = context_part + user_inputs_content
    new_input_len = len(new_input_content)
    
    # 构建新的 Input 消息 (field 2, tag = 12)
    new_input_msg = b'\x12' + _encode_varint(new_input_len) + new_input_content
    
    # 找到 Settings 和 Metadata 部分（从 Input 之后）
    # 原始 Input: 12 5a [90 bytes content] = 92 bytes total
    # Settings 从位置 2 + 92 = 94 开始? 不对，让我重新计算
    # 位置 0-1: 0a 00
    # 位置 2: 12 (Input tag)
    # 位置 3: 5a (Input length = 90)
    # 位置 4-93: Input content (90 bytes)
    # 位置 94: Settings 开始 (1a 66)
    
    settings_start = 2 + 2 + 90  # TaskContext(2) + Input tag+len(2) + Input content(90) = 94
    rest_of_template = bytes(template[settings_start:])
    
    # 组装最终请求
    result = b'\x0a\x00' + new_input_msg + rest_of_template
    
    # 如果需要禁用Warp工具，移除 supported_tools 字段
    if disable_warp_tools:
        result = _remove_supported_tools(result)
        logger.debug("Removed Warp supported_tools from request")
    
    # 如果提供了自定义工具，需要添加到请求中
    if tools and len(tools) > 0:
        logger.debug(f"Adding {len(tools)} custom tools to template-based request")
        # 模板方法构建的是 bytes，需要先反序列化为 protobuf 对象
        # 然后添加工具，再重新序列化
        try:
            # 导入工具转换函数（在函数内部导入以避免循环导入）
            from .tool_converter import add_mcp_tools_to_request
            
            ensure_proto_runtime()
            request_cls = msg_cls("warp.multi_agent.v1.Request")
            request = request_cls()
            request.ParseFromString(result)
            
            # 添加工具
            add_mcp_tools_to_request(request, tools)
            
            # 重新序列化
            result = request.SerializeToString()
            logger.debug(f"Added custom tools, new request size: {len(result)} bytes")
        except Exception as e:
            logger.error(f"Failed to add custom tools to template request: {e}", exc_info=True)
    
    logger.debug(f"Built request from template: {len(result)} bytes (template was {len(template)} bytes)")
    return bytes(result)


def _remove_supported_tools(data: bytes) -> bytes:
    """
    从 protobuf 数据中移除工具相关字段
    
    需要移除的字段:
    - supported_tools: field 9 (wire type 2) = 0x4a [len] [packed values]
    - client_supported_tools: field 22 (wire type 2) = 0xb2 0x01 [len] [packed values]
    """
    result = bytearray(data)
    total_removed = 0
    
    # 模板中 supported_tools 的完整模式: 4a 13 06 07 0c 08 09 0f 0e 00 0b 10 0a 14 11 13 12 02 03 01 0d
    supported_tools_pattern = bytes.fromhex('4a1306070c08090f0e000b100a141113120203010d')
    
    # 模板中 client_supported_tools 的完整模式: b2 01 07 0a 14 06 07 0c 02 01
    client_supported_tools_pattern = bytes.fromhex('b201070a1406070c0201')
    
    # 先找到 Settings 的位置 (1a xx ... 22 xx)
    # Settings 以 1a 开头，Metadata 以 22 开头
    settings_tag_pos = -1
    metadata_tag_pos = -1
    
    # 找 Settings (field 3 = 0x1a) - 通常在数据的后半部分
    for i in range(len(data) - 1):
        if data[i] == 0x1a and i > 50:  # Settings 在 Input 之后
            # 验证这是 Settings（后面应该有 model_config: 0a 21）
            settings_len = data[i + 1]
            if i + 2 + 2 < len(data) and data[i + 2] == 0x0a and data[i + 3] == 0x21:
                settings_tag_pos = i
                break
    
    if settings_tag_pos == -1:
        logger.warning("Could not find Settings section")
        return bytes(result)
    
    original_settings_len = data[settings_tag_pos + 1]
    logger.debug(f"Found Settings at position {settings_tag_pos}, length {original_settings_len}")
    
    # 移除 supported_tools
    pos = result.find(supported_tools_pattern)
    if pos != -1:
        result = result[:pos] + result[pos + len(supported_tools_pattern):]
        total_removed += len(supported_tools_pattern)
        logger.debug(f"Removed supported_tools at position {pos}, {len(supported_tools_pattern)} bytes")
    
    # 移除 client_supported_tools
    pos = result.find(client_supported_tools_pattern)
    if pos != -1:
        result = result[:pos] + result[pos + len(client_supported_tools_pattern):]
        total_removed += len(client_supported_tools_pattern)
        logger.debug(f"Removed client_supported_tools at position {pos}, {len(client_supported_tools_pattern)} bytes")
    
    # 更新 Settings 长度
    if total_removed > 0:
        new_settings_len = original_settings_len - total_removed
        result[settings_tag_pos + 1] = new_settings_len
        logger.debug(f"Updated Settings length: {original_settings_len} -> {new_settings_len}")
    
    return bytes(result)


def build_request_bytes(
    user_text: str, 
    model: str = "auto", 
    disable_warp_tools: bool = False,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    task_id: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_results: Optional[List[Dict[str, Any]]] = None
) -> bytes:
    """
    构建 Warp API 请求的 protobuf 字节
    
    Args:
        user_text: 用户输入的文本
        model: 模型名称
        disable_warp_tools: 是否禁用Warp内置工具
        history_messages: OpenAI格式的历史消息列表 [{"role": "user"|"assistant"|"tool", "content": "..."}]
        task_id: 已有的 task_id（如果提供，则使用这个 ID 来保持会话连续性）
        tools: OpenAI格式的工具列表 [{"type": "function", "function": {...}}]
        tool_results: 工具执行结果列表 [{"tool_call_id": "...", "content": "..."}]
    
    如果有历史消息或 task_id，使用protobuf库构建完整请求；否则使用模板方法
    """
    # 重要：只有当真正有历史消息或有效的 task_id 时才使用 build_request_bytes_with_history
    # 否则使用 new 模板（is_new_conversation=true）
    if (history_messages and len(history_messages) > 0) or (task_id and task_id.strip()) or tool_results:
        # 有历史消息或有效 task_id 或工具结果时，使用protobuf库构建完整请求
        return build_request_bytes_with_history(user_text, model, disable_warp_tools, history_messages, task_id, tools, tool_results)
    else:
        # 无历史消息且无 task_id 时，使用模板方法（已验证可靠）
        return build_request_bytes_from_template(user_text, model, disable_warp_tools, tools)


def build_request_bytes_with_history(
    user_text: str,
    model: str = "auto",
    disable_warp_tools: bool = False,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    task_id: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_results: Optional[List[Dict[str, Any]]] = None
) -> bytes:
    """
    构建包含历史消息的 Warp API 请求
    
    方法：基于模板构建主体部分（Input、Settings、Metadata），
    然后在前面添加 task_context 来包含历史消息。
    这样可以确保核心格式与验证过的模板一致。
    
    Args:
        user_text: 当前用户输入
        model: 模型名称
        disable_warp_tools: 是否禁用Warp工具
        history_messages: 历史消息列表（不包含当前用户输入）
        task_id: 已有的 task_id（如果提供，则使用这个 ID）
        tools: OpenAI格式的工具列表
        tool_results: 工具执行结果列表 [{"tool_call_id": "...", "content": "..."}]
    """
    ensure_proto_runtime()
    
    # 重要：只有当真正有历史消息或明确提供了 task_id 或有工具结果时才使用此函数
    # 如果都没有，应该使用 build_request_bytes 来创建新会话
    if not history_messages and not task_id and not tool_results:
        logger.warning("build_request_bytes_with_history called without history, task_id or tool_results, using new session template")
        return build_request_bytes_from_template(user_text, model, disable_warp_tools, tools)
    
    # 1. 第二次请求需要完全重新构建，不能使用模板
    # 因为第二次请求的结构与第一次完全不同：
    # - 有 task_context（包含历史）
    # - Input 中只有 context 和 user_query（没有 user_inputs）
    # - 没有 Settings
    # - metadata 只有 conversation_id
    
    # 2. 使用提供的 task_id 或生成新的
    actual_task_id = task_id if task_id else str(uuid.uuid4())
    
    logger.debug(f"build_request_bytes_with_history: task_id={task_id}, actual_task_id={actual_task_id}")
    
    # 3. 构建完整的第二次请求
    from ..config.models import get_model_config
    import os
    
    request_cls = msg_cls("warp.multi_agent.v1.Request")
    request = request_cls()
    
    # 3.1 构建 task_context（始终为空）
    # 关键发现：Warp 服务器自己管理 task 的创建和消息
    # 我们不应该在 task_context 中预先创建 task
    # 而是通过 metadata.conversation_id 来关联会话
    # 服务器会根据 conversation_id 自动加载历史消息
    task_context_cls = msg_cls("warp.multi_agent.v1.Request.TaskContext")
    task_context = task_context_cls()
    
    # 不添加任何 task，保持为空
    # 这样序列化后就是 0a 00（field 1, length 0）
    
    request.task_context.CopyFrom(task_context)
    
    # 3.2 构建 Input（包含 context 和 user_query 或 user_inputs）
    input_msg = request.input
    
    # 设置 InputContext
    context = input_msg.context
    home_dir = os.path.expanduser("~")
    
    context.directory.pwd = os.getcwd()
    context.directory.home = home_dir
    context.operating_system.platform = "MacOS"
    context.shell.name = "zsh"
    context.shell.version = "5.9"
    
    # 设置 current_time (注意字段名是 current_time 不是 timestamp)
    import time
    now = time.time()
    context.current_time.seconds = int(now)
    context.current_time.nanos = int((now - int(now)) * 1e9)
    
    # 如果有工具结果，将其作为历史消息的一部分，而不是使用 ToolCallResult
    # 因为 Warp 不认识我们自己构造的 call_mcp_tool 结果
    if tool_results and len(tool_results) > 0:
        logger.info(f"Building request with {len(tool_results)} tool results (as history context)")
        
        # 将工具结果转换为文本，添加到历史消息中
        if not history_messages:
            history_messages = []
        
        # 构建包含工具结果的上下文
        context_parts = []
        
        # 添加历史消息（包括穿插的 tool 消息）
        for msg in history_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                context_parts.append(f"User: {content}")
            elif role == "assistant":
                # 如果 assistant 有 tool_calls，也包含进来
                if msg.get("tool_calls"):
                    tool_calls_desc = []
                    for tc in msg["tool_calls"]:
                        func = tc.get("function", {})
                        tool_calls_desc.append(f"Called {func.get('name')} with args: {func.get('arguments')}")
                    context_parts.append(f"Assistant: {content}\nTool calls: {'; '.join(tool_calls_desc)}")
                else:
                    context_parts.append(f"Assistant: {content}")
            elif role == "tool":
                # 处理穿插在历史中的 tool 消息
                tool_call_id = msg.get("tool_call_id", "")
                context_parts.append(f"Tool result ({tool_call_id}): {content}")
                logger.debug(f"Added tool result from history: tool_call_id={tool_call_id}, content_len={len(content)}")
        
        # 添加额外的工具结果（如果有）
        for tool_result in tool_results:
            tool_call_id = tool_result.get("tool_call_id", "")
            content = tool_result.get("content", "")
            context_parts.append(f"Tool result ({tool_call_id}): {content}")
            logger.debug(f"Added tool result to context: tool_call_id={tool_call_id}, content_len={len(content)}")
        
        # 添加当前用户查询
        if user_text and user_text.strip():
            context_parts.append(f"User: {user_text}")
        else:
            # 如果只有工具结果没有新的用户消息，添加隐式继续指令
            context_parts.append("User: Please analyze the tool results above and provide your response.")
            logger.debug("Added implicit continuation instruction for tool results")
        
        # 将所有内容组合成一个 query
        full_query = "\n\n".join(context_parts)
        input_msg.user_query.query = full_query
        input_msg.user_query.is_new_conversation = False
        logger.debug(f"Built query with tool results as context: {len(context_parts)} parts")
    else:
        # 没有工具结果，使用普通的 user_query 格式
        # 如果有历史消息，将其作为上下文包含在 query 中
        if history_messages:
            # 构建包含历史的 query
            context_parts = []
            for msg in history_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user":
                    context_parts.append(f"User: {content}")
                elif role == "assistant":
                    context_parts.append(f"Assistant: {content}")
                elif role == "tool":
                    # 即使没有 tool_results 参数，也处理历史中的 tool 消息
                    tool_call_id = msg.get("tool_call_id", "")
                    context_parts.append(f"Tool result ({tool_call_id}): {content}")
            
            # 将历史和当前问题组合
            if user_text and user_text.strip():
                full_query = "\n\n".join(context_parts) + f"\n\nUser: {user_text}"
            else:
                full_query = "\n\n".join(context_parts)
            
            input_msg.user_query.query = full_query
            logger.debug(f"Built query with history context: {len(context_parts)} messages")
        else:
            # 新对话，只在这里添加系统提示词（如果需要）
            if disable_warp_tools:
                system_prompt = """IMPORTANT INSTRUCTIONS:
- Do NOT use Warp's built-in tools (like terminal commands, file operations, etc.)
- ONLY use the tools explicitly provided by the client through tool calls
- If you need to perform an action, use the available client tools
- Available client tools will be listed in the tool definitions"""
                input_msg.user_query.query = system_prompt + "\n\n" + user_text
            else:
                input_msg.user_query.query = user_text
        
        input_msg.user_query.is_new_conversation = True
    
    # 3.3 设置 Settings（包含模型信息）
    model_config = get_model_config(model)
    settings = request.settings
    
    # 设置 model_config.base
    settings.model_config.base = model_config.get("base_model", "auto-genius")
    # planning 字段留空（不设置）
    
    # 设置其他 Settings 字段（参考文档中的第二次请求）
    settings.rules_enabled = True
    settings.web_context_retrieval_enabled = True
    settings.supports_parallel_tool_calls = True
    settings.planning_enabled = True
    settings.warp_drive_context_enabled = True
    settings.supports_create_files = True
    settings.supports_long_running_commands = True
    settings.should_preserve_file_content_in_history = True
    settings.supports_todos_ui = True
    settings.supports_linked_code_blocks = True
    
    # 设置 supported_tools（与第一次请求保持一致）
    if not disable_warp_tools:
        try:
            tool_types = [6, 7, 12, 8, 9, 15, 14, 0, 11, 16, 10, 20, 17, 19, 18, 2, 3, 1, 13]
            settings.supported_tools[:] = tool_types
            logger.debug(f"Set supported_tools: {tool_types}")
        except Exception as e:
            logger.debug(f"Could not set supported_tools: {e}")
    else:
        logger.debug("Warp tools disabled, skipping supported_tools")
    
    settings.field_14 = True
    settings.field_15 = True
    settings.field_16 = True
    settings.field_17 = True
    settings.field_21 = True
    
    # 设置 client_supported_tools (包含 9=CALL_MCP_TOOL 以支持自定义工具)
    if hasattr(settings, 'client_supported_tools'):
        settings.client_supported_tools[:] = [10, 20, 6, 7, 12, 9, 2, 1]
    
    settings.field_23 = True
    
    # 3.4 添加自定义工具（如果提供）
    if tools and len(tools) > 0:
        logger.debug(f"Adding {len(tools)} custom tools to request")
        # 导入工具转换函数（在函数内部导入以避免循环导入）
        from .tool_converter import add_mcp_tools_to_request
        add_mcp_tools_to_request(request, tools)
    
    # 3.5 设置 metadata（不设置 conversation_id，让每次都是新对话）
    # 关键发现：设置 conversation_id 会导致服务器返回空响应
    # 因为服务器端可能不会持久化会话状态
    # 所以我们通过在 query 中包含历史来实现"伪多轮对话"
    # request.metadata.conversation_id = actual_task_id  # 注释掉
    
    # 4. 序列化
    result = request.SerializeToString()
    
    logger.debug(f"Built request with history: {len(result)} bytes, history_count={len(history_messages) if history_messages else 0}")
    # logger.debug(f"Set metadata.conversation_id: {actual_task_id}")  # 注释掉
    
    return result


def _build_request_bytes_protobuf(user_text: str, model: str = "auto", disable_warp_tools: bool = False) -> bytes:
    """
    使用 protobuf 库构建请求（备用方法）
    
    Args:
        user_text: 用户输入的文本
        model: 模型名称
        disable_warp_tools: 是否禁用Warp内置工具
    """
    from ..config.models import get_model_config
    import os
    import platform

    full, path = get_request_schema()
    Cls = msg_cls(full)
    msg = Cls()
    
    # 1. TaskContext 保持为空（根据真实请求分析）
    # 但需要确保字段存在，这样序列化时会输出 0a 00
    # 访问 task_context 字段以确保它被序列化
    _ = msg.task_context
    
    # 2. 构建 Input（包含 context 和 user_inputs）
    input_msg = msg.input
    
    # 设置 InputContext
    if hasattr(input_msg, 'context'):
        context = input_msg.context
        
        # 设置目录信息
        home_dir = os.path.expanduser("~")
        if hasattr(context, 'directory'):
            dir_msg = context.directory
            if hasattr(dir_msg, 'pwd'):
                dir_msg.pwd = home_dir
            if hasattr(dir_msg, 'home'):
                dir_msg.home = home_dir
        
        # 设置 OS 信息
        if hasattr(context, 'operating_system'):
            os_msg = context.operating_system
            if hasattr(os_msg, 'platform'):
                os_msg.platform = "MacOS"
            if hasattr(os_msg, 'distribution'):
                os_msg.distribution = ""  # 空字符串
        
        # 设置 shell 信息
        if hasattr(context, 'shell'):
            shell_msg = context.shell
            if hasattr(shell_msg, 'name'):
                shell_msg.name = "zsh"
            if hasattr(shell_msg, 'version'):
                shell_msg.version = "5.9"
        
        # 设置当前时间
        if hasattr(context, 'current_time'):
            import time
            current_time = context.current_time
            current_time.GetCurrentTime()
        
        logger.debug("Set InputContext with directory, OS, shell, and timestamp")
    
    # 设置 user_inputs（在 Input 中）
    if hasattr(input_msg, 'user_inputs'):
        user_inputs = input_msg.user_inputs
        user_input = user_inputs.inputs.add()
        user_query = user_input.user_query
        user_query.query = user_text
        
        # 设置 user_query 的额外字段（匹配真实请求）
        # 这些字段在 user_query message 内部
        if hasattr(user_query, 'attachments_bytes'):
            user_query.attachments_bytes = b''  # 空字节 (1a 00)
        if hasattr(user_query, 'is_new_conversation'):
            user_query.is_new_conversation = True  # (20 01)
        
        logger.debug(f"Set Input.user_inputs with query: {user_text}")
    
    # 3. 设置 Settings
    if hasattr(msg, 'settings'):
        settings = msg.settings
        
        # 设置模型配置
        if hasattr(settings, 'model_config'):
            model_config_dict = get_model_config(model)
            model_config = settings.model_config
            model_config.base = model_config_dict["base"]
            # planning 字段留空（真实请求中没有）
            # coding 字段设置为 cli-agent-auto
            model_config.coding = "cli-agent-auto"
            logger.debug(f"Set model config: base={model_config.base}, coding={model_config.coding}")
        
        # 设置 boolean 字段为 true（匹配真实请求）
        settings.rules_enabled = True
        settings.web_context_retrieval_enabled = True
        settings.supports_parallel_tool_calls = True
        settings.planning_enabled = True
        settings.warp_drive_context_enabled = True
        settings.supports_create_files = True
        settings.supports_long_running_commands = True
        settings.should_preserve_file_content_in_history = True
        settings.supports_todos_ui = True
        settings.supports_linked_code_blocks = True
        settings.use_anthropic_text_editor_tools = True
        
        # 设置 supported_tools - 完整的工具列表（匹配真实请求）
        # [6, 7, 12, 8, 9, 15, 14, 0, 11, 16, 10, 20, 17, 19, 18, 2, 3, 1, 13]
        if not disable_warp_tools:
            try:
                tool_types = [6, 7, 12, 8, 9, 15, 14, 0, 11, 16, 10, 20, 17, 19, 18, 2, 3, 1, 13]
                settings.supported_tools[:] = tool_types
                logger.debug(f"Set supported_tools: {tool_types}")
            except Exception as e:
                logger.debug(f"Could not set supported_tools: {e}")
        else:
            logger.debug("Warp tools disabled, skipping supported_tools")
        
        # 设置额外的 Settings 字段（匹配真实请求）
        if hasattr(settings, 'field_14'):
            settings.field_14 = True  # 70 01
        if hasattr(settings, 'field_15'):
            settings.field_15 = True  # 78 01
        if hasattr(settings, 'field_16'):
            settings.field_16 = True  # 80 01 01
        if hasattr(settings, 'field_17'):
            settings.field_17 = True  # 88 01 01
        if hasattr(settings, 'field_21'):
            settings.field_21 = True  # a8 01 01
        if hasattr(settings, 'client_supported_tools'):
            # 包含 9=CALL_MCP_TOOL 以支持自定义工具
            # b2 01 08 0a 14 06 07 0c 09 02 01
            settings.client_supported_tools[:] = [10, 20, 6, 7, 12, 9, 2, 1]
        if hasattr(settings, 'field_23'):
            settings.field_23 = True  # b8 01 01

        logger.debug("Applied settings fields")

    # 4. 设置 metadata
    if hasattr(msg, 'metadata'):
        metadata = msg.metadata
        metadata.conversation_id = f"conv-{uuid.uuid4()}"
        
        # 设置 logging 字段（使用Struct类型）
        if hasattr(metadata, 'logging'):
            logging_struct = metadata.logging
            logging_struct['is_autodetected_user_query'].bool_value = True
            logging_struct['entrypoint'].string_value = "USER_INITIATED"
            logging_struct['is_auto_resume_after_error'].bool_value = False
            logger.debug("Set metadata.logging fields")

    # 序列化
    request_bytes = msg.SerializeToString()
    
    # 检查是否以 0a 00 开头（空的 TaskContext）
    # 如果不是，手动添加
    if not request_bytes.startswith(b'\x0a\x00'):
        request_bytes = b'\x0a\x00' + request_bytes
        logger.debug("Added empty TaskContext prefix (0a 00)")
    
    # proto3 中空字节不会被序列化，但真实请求需要 "1a 00" (attachments_bytes = empty)
    # 这个字段在 user_query message 内部
    # user_query 结构: 0a XX [query string] 1a 00 20 01
    # 我们需要在 "20 01" 前面插入 "1a 00"
    
    # 查找 query 字节后的 "20 01"
    query_bytes = user_text.encode('utf-8')
    # 查找模式: [query] 20 01 (即查询内容后面紧跟 20 01)
    query_pattern = query_bytes + b'\x20\x01'
    pos = request_bytes.find(query_pattern)
    if pos != -1:
        # 在 query 和 20 01 之间插入 1a 00
        insert_pos = pos + len(query_bytes)
        request_bytes = request_bytes[:insert_pos] + b'\x1a\x00' + request_bytes[insert_pos:]
        logger.debug(f"Inserted attachments_bytes field (1a 00) at position {insert_pos}")
        
        # 更新嵌套消息的长度字段
        # 从内到外更新: user_query -> UserInput -> inputs -> UserInputs -> Input
        
        # Input 在 field 2 (12 xx)
        input_tag_pos = 2  # 跳过 0a 00 (TaskContext)
        if request_bytes[input_tag_pos] == 0x12:
            old_input_len = request_bytes[input_tag_pos + 1]
            new_input_len = old_input_len + 2  # 增加 2 字节 (1a 00)
            request_bytes = request_bytes[:input_tag_pos + 1] + bytes([new_input_len]) + request_bytes[input_tag_pos + 2:]
            logger.debug(f"Updated Input length from {old_input_len} to {new_input_len}")
        
        # 更新 UserInputs (field 6 = 32)
        ui_tag_pos = request_bytes.find(b'\x32')
        if ui_tag_pos != -1:
            old_ui_len = request_bytes[ui_tag_pos + 1]
            new_ui_len = old_ui_len + 2
            request_bytes = request_bytes[:ui_tag_pos + 1] + bytes([new_ui_len]) + request_bytes[ui_tag_pos + 2:]
            logger.debug(f"Updated UserInputs length from {old_ui_len} to {new_ui_len}")
        
        # 更新 inputs (field 1 = 0a) 在 UserInputs 内部
        inputs_tag_pos = ui_tag_pos + 2  # 32 xx 后面
        if request_bytes[inputs_tag_pos] == 0x0a:
            old_inputs_len = request_bytes[inputs_tag_pos + 1]
            new_inputs_len = old_inputs_len + 2
            request_bytes = request_bytes[:inputs_tag_pos + 1] + bytes([new_inputs_len]) + request_bytes[inputs_tag_pos + 2:]
            logger.debug(f"Updated inputs length from {old_inputs_len} to {new_inputs_len}")
        
        # 更新 UserInput (field 1 = 0a) 在 inputs 内部 - 这是 user_query oneof
        user_input_tag_pos = inputs_tag_pos + 2
        if request_bytes[user_input_tag_pos] == 0x0a:
            old_user_input_len = request_bytes[user_input_tag_pos + 1]
            new_user_input_len = old_user_input_len + 2
            request_bytes = request_bytes[:user_input_tag_pos + 1] + bytes([new_user_input_len]) + request_bytes[user_input_tag_pos + 2:]
            logger.debug(f"Updated UserInput/user_query length from {old_user_input_len} to {new_user_input_len}")
    
    logger.debug(f"Built protobuf request: {len(request_bytes)} bytes")
    return request_bytes
