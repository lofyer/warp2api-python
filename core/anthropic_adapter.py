#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Anthropic 格式适配器 - 在 Anthropic Messages API 格式和 Warp 格式之间转换

转换流程:
- Anthropic 请求 -> Warp 请求 (anthropic_to_warp_messages)
- Warp 响应 -> Anthropic 响应 (warp_to_anthropic_stream / warp_to_anthropic_response)
"""
import uuid
import time
from typing import AsyncGenerator, Dict, Any, List, Optional, Union
import logging
import json

logger = logging.getLogger(__name__)

try:
    from warp2protobuf.warp.response import extract_openai_sse_deltas_from_response
    USE_WARP_PARSER = True
except ImportError:
    USE_WARP_PARSER = False
    logger.warning("Could not import warp2protobuf.warp.response, tool calls may not work")


class AnthropicAdapter:
    """Anthropic 格式适配器"""
    
    @staticmethod
    def anthropic_to_warp_messages(
        system: Optional[str],
        messages: List[dict]
    ) -> List[dict]:
        """
        将 Anthropic 消息格式转换为 Warp 格式
        
        Anthropic: system 是单独参数，tool_result 在 user 消息的 content 中
        Warp: system 是 messages 中的一条，tool 结果是单独的 tool role 消息
        """
        result = []
        
        if system:
            result.append({"role": "system", "content": system})
        
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            
            if role == "user":
                if isinstance(content, str):
                    result.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    for block in content:
                        block_type = block.get("type")
                        if block_type == "text":
                            text_parts.append(block.get("text", ""))
                        elif block_type == "tool_result":
                            tool_use_id = block.get("tool_use_id")
                            tool_content = block.get("content", "")
                            if isinstance(tool_content, list):
                                tool_content = json.dumps(tool_content, ensure_ascii=False)
                            result.append({
                                "role": "tool",
                                "tool_call_id": tool_use_id,
                                "content": tool_content
                            })
                    if text_parts:
                        result.append({"role": "user", "content": "\n".join(text_parts)})
            
            elif role == "assistant":
                if isinstance(content, str):
                    result.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    for block in content:
                        block_type = block.get("type")
                        if block_type == "text":
                            text_parts.append(block.get("text", ""))
                        elif block_type == "tool_use":
                            tool_calls.append({
                                "id": block.get("id"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name"),
                                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)
                                }
                            })
                    
                    assistant_msg = {"role": "assistant"}
                    if text_parts:
                        assistant_msg["content"] = "\n".join(text_parts)
                    else:
                        assistant_msg["content"] = ""
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls
                    result.append(assistant_msg)
        
        return result
    
    @staticmethod
    async def warp_to_anthropic_stream(
        warp_stream: AsyncGenerator[dict, None],
        model: str,
        message_id: str = None,
        input_tokens: int = 0
    ) -> AsyncGenerator[str, None]:
        """
        将 Warp SSE 流转换为 Anthropic SSE 格式
        """
        if not message_id:
            message_id = f"msg_{uuid.uuid4().hex[:24]}"
        
        content_started = False
        content_index = 0
        output_tokens = 0
        tool_calls = []
        current_tool_index = -1
        
        yield f"event: message_start\ndata: {AnthropicAdapter._json_dumps({'type': 'message_start', 'message': {'id': message_id, 'type': 'message', 'role': 'assistant', 'model': model, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': input_tokens, 'output_tokens': 0}}})}\n\n"
        
        try:
            async for event in warp_stream:
                if USE_WARP_PARSER and "raw_payload" in event:
                    try:
                        payload = event["raw_payload"]
                        deltas = extract_openai_sse_deltas_from_response(payload)
                        
                        for delta in deltas:
                            choices = delta.get("choices", [])
                            if not choices:
                                continue
                            
                            choice = choices[0]
                            delta_content = choice.get("delta", {})
                            finish_reason = choice.get("finish_reason")
                            
                            if "content" in delta_content and delta_content["content"]:
                                text = delta_content["content"]
                                
                                if not content_started:
                                    yield f"event: content_block_start\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_start', 'index': content_index, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                                    content_started = True
                                
                                yield f"event: content_block_delta\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_delta', 'index': content_index, 'delta': {'type': 'text_delta', 'text': text}})}\n\n"
                                output_tokens += len(text) // 4
                            
                            if "tool_calls" in delta_content:
                                for tc_delta in delta_content["tool_calls"]:
                                    tc_index = tc_delta.get("index", 0)
                                    
                                    while len(tool_calls) <= tc_index:
                                        tool_calls.append({
                                            "id": "",
                                            "name": "",
                                            "arguments": ""
                                        })
                                    
                                    if "id" in tc_delta:
                                        tool_calls[tc_index]["id"] = tc_delta["id"]
                                    if "function" in tc_delta:
                                        func = tc_delta["function"]
                                        if "name" in func:
                                            tool_calls[tc_index]["name"] += func["name"]
                                        if "arguments" in func:
                                            tool_calls[tc_index]["arguments"] += func["arguments"]
                                    
                                    if tc_index > current_tool_index and tool_calls[tc_index]["id"]:
                                        if content_started:
                                            yield f"event: content_block_stop\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_stop', 'index': content_index})}\n\n"
                                            content_index += 1
                                            content_started = False
                                        
                                        current_tool_index = tc_index
                            
                            if finish_reason:
                                if content_started:
                                    yield f"event: content_block_stop\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_stop', 'index': content_index})}\n\n"
                                    content_index += 1
                                
                                for i, tc in enumerate(tool_calls):
                                    if tc["id"] and tc["name"]:
                                        try:
                                            input_obj = json.loads(tc["arguments"]) if tc["arguments"] else {}
                                        except json.JSONDecodeError:
                                            input_obj = {}
                                        
                                        tool_id = tc["id"]
                                        if not tool_id.startswith("toolu_"):
                                            tool_id = f"toolu_{tool_id}"
                                        
                                        yield f"event: content_block_start\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_start', 'index': content_index, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': tc['name'], 'input': {}}})}\n\n"
                                        yield f"event: content_block_delta\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_delta', 'index': content_index, 'delta': {'type': 'input_json_delta', 'partial_json': json.dumps(input_obj, ensure_ascii=False)}})}\n\n"
                                        yield f"event: content_block_stop\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_stop', 'index': content_index})}\n\n"
                                        content_index += 1
                                
                                stop_reason = "end_turn"
                                if finish_reason == "tool_calls" or tool_calls:
                                    stop_reason = "tool_use"
                                elif finish_reason == "length":
                                    stop_reason = "max_tokens"
                                
                                yield f"event: message_delta\ndata: {AnthropicAdapter._json_dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}})}\n\n"
                                yield f"event: message_stop\ndata: {AnthropicAdapter._json_dumps({'type': 'message_stop'})}\n\n"
                                return
                        
                        continue
                    except Exception as e:
                        logger.warning(f"Failed to use warp parser: {e}")
                
                if "client_actions" in event or "clientActions" in event:
                    actions_data = event.get("client_actions") or event.get("clientActions")
                    actions = actions_data.get("actions") or actions_data.get("Actions") or []
                    
                    for action in actions:
                        if "append_to_message_content" in action or "appendToMessageContent" in action:
                            append_data = action.get("append_to_message_content") or action.get("appendToMessageContent")
                            message_data = append_data.get("message", {})
                            agent_output = message_data.get("agent_output") or message_data.get("agentOutput", {})
                            text_delta = agent_output.get("text", "")
                            
                            if text_delta:
                                if not content_started:
                                    yield f"event: content_block_start\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_start', 'index': content_index, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                                    content_started = True
                                
                                yield f"event: content_block_delta\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_delta', 'index': content_index, 'delta': {'type': 'text_delta', 'text': text_delta}})}\n\n"
                                output_tokens += len(text_delta) // 4
                        
                        elif "add_messages_to_task" in action or "addMessagesToTask" in action:
                            msg_data = action.get("add_messages_to_task") or action.get("addMessagesToTask")
                            messages = msg_data.get("messages", [])
                            for msg in messages:
                                agent_output = msg.get("agent_output") or msg.get("agentOutput", {})
                                text_delta = agent_output.get("text", "")
                                if text_delta:
                                    if not content_started:
                                        yield f"event: content_block_start\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_start', 'index': content_index, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                                        content_started = True
                                    
                                    yield f"event: content_block_delta\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_delta', 'index': content_index, 'delta': {'type': 'text_delta', 'text': text_delta}})}\n\n"
                                    output_tokens += len(text_delta) // 4
                
                elif "finished" in event:
                    if content_started:
                        yield f"event: content_block_stop\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_stop', 'index': content_index})}\n\n"
                    
                    yield f"event: message_delta\ndata: {AnthropicAdapter._json_dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}})}\n\n"
                    yield f"event: message_stop\ndata: {AnthropicAdapter._json_dumps({'type': 'message_stop'})}\n\n"
                    return
        
        except Exception as e:
            logger.error(f"Error in warp_to_anthropic_stream: {e}")
            if content_started:
                yield f"event: content_block_stop\ndata: {AnthropicAdapter._json_dumps({'type': 'content_block_stop', 'index': content_index})}\n\n"
            yield f"event: message_delta\ndata: {AnthropicAdapter._json_dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}})}\n\n"
            yield f"event: message_stop\ndata: {AnthropicAdapter._json_dumps({'type': 'message_stop'})}\n\n"
    
    @staticmethod
    async def warp_to_anthropic_response(
        warp_stream: AsyncGenerator[dict, None],
        model: str,
        message_id: str = None
    ) -> dict:
        """
        将 Warp 流转换为 Anthropic 非流式响应
        """
        if not message_id:
            message_id = f"msg_{uuid.uuid4().hex[:24]}"
        
        content_parts = []
        tool_calls = []
        input_tokens = 0
        output_tokens = 0
        stop_reason = "end_turn"
        
        try:
            async for event in warp_stream:
                if USE_WARP_PARSER and "raw_payload" in event:
                    try:
                        payload = event["raw_payload"]
                        deltas = extract_openai_sse_deltas_from_response(payload)
                        
                        for delta in deltas:
                            choices = delta.get("choices", [])
                            if choices:
                                choice = choices[0]
                                delta_content = choice.get("delta", {})
                                
                                if "content" in delta_content:
                                    content_parts.append(delta_content["content"])
                                
                                if "tool_calls" in delta_content:
                                    for tc_delta in delta_content["tool_calls"]:
                                        tc_index = tc_delta.get("index", 0)
                                        
                                        while len(tool_calls) <= tc_index:
                                            tool_calls.append({
                                                "id": "",
                                                "name": "",
                                                "arguments": ""
                                            })
                                        
                                        if "id" in tc_delta:
                                            tool_calls[tc_index]["id"] = tc_delta["id"]
                                        if "function" in tc_delta:
                                            func = tc_delta["function"]
                                            if "name" in func:
                                                tool_calls[tc_index]["name"] += func["name"]
                                            if "arguments" in func:
                                                tool_calls[tc_index]["arguments"] += func["arguments"]
                                
                                if choice.get("finish_reason"):
                                    fr = choice["finish_reason"]
                                    if fr == "tool_calls":
                                        stop_reason = "tool_use"
                                    elif fr == "length":
                                        stop_reason = "max_tokens"
                        
                        continue
                    except Exception as e:
                        logger.debug(f"Could not parse with warp parser: {e}")
                
                if "client_actions" in event or "clientActions" in event:
                    actions_data = event.get("client_actions") or event.get("clientActions")
                    actions = actions_data.get("actions") or actions_data.get("Actions") or []
                    
                    for action in actions:
                        if "add_messages_to_task" in action or "addMessagesToTask" in action:
                            msg_data = action.get("add_messages_to_task") or action.get("addMessagesToTask")
                            messages = msg_data.get("messages", [])
                            for msg in messages:
                                agent_output = msg.get("agent_output") or msg.get("agentOutput", {})
                                text_delta = agent_output.get("text", "")
                                if text_delta:
                                    content_parts.append(text_delta)
                        
                        elif "append_to_message_content" in action or "appendToMessageContent" in action:
                            append_data = action.get("append_to_message_content") or action.get("appendToMessageContent")
                            message_data = append_data.get("message", {})
                            agent_output = message_data.get("agent_output") or message_data.get("agentOutput", {})
                            text_delta = agent_output.get("text", "")
                            if text_delta:
                                content_parts.append(text_delta)
                
                elif "finished" in event:
                    finished_data = event["finished"]
                    token_usage = finished_data.get("token_usage") or finished_data.get("tokenUsage") or []
                    if token_usage:
                        for usage in token_usage:
                            input_tokens += usage.get("total_input") or usage.get("totalInput") or 0
                            output_tokens += usage.get("output") or 0
        
        except Exception as e:
            logger.error(f"Error in warp_to_anthropic_response: {e}")
            raise
        
        content = []
        full_text = "".join(content_parts)
        if full_text:
            content.append({"type": "text", "text": full_text})
        
        for tc in tool_calls:
            if tc["id"] and tc["name"]:
                try:
                    input_obj = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    input_obj = {}
                
                tool_id = tc["id"]
                if not tool_id.startswith("toolu_"):
                    tool_id = f"toolu_{tool_id}"
                
                content.append({
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tc["name"],
                    "input": input_obj
                })
                stop_reason = "tool_use"
        
        if not content:
            content.append({"type": "text", "text": ""})
        
        return {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            }
        }
    
    @staticmethod
    def _json_dumps(obj: dict) -> str:
        """JSON 序列化（紧凑格式）"""
        return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
