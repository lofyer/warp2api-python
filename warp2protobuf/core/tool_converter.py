"""
OpenAI tools 到 Warp MCPTool 的转换器
"""
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def convert_openai_tools_to_mcp(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 OpenAI 格式的 tools 转换为 Warp MCPTool 格式
    
    OpenAI 格式:
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取天气信息",
            "parameters": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
    }
    
    Warp MCPTool 格式 (proto):
    message MCPTool {
        string name = 1;
        string description = 2;
        google.protobuf.Struct input_schema = 3;
    }
    
    Args:
        tools: OpenAI 格式的工具列表
        
    Returns:
        Warp MCPTool 格式的工具列表
    """
    mcp_tools = []
    
    for tool in tools:
        if tool.get("type") != "function":
            logger.warning(f"Skipping non-function tool: {tool.get('type')}")
            continue
        
        function = tool.get("function", {})
        name = function.get("name")
        description = function.get("description", "")
        parameters = function.get("parameters", {})
        
        if not name:
            logger.warning(f"Skipping tool without name: {tool}")
            continue
        
        mcp_tool = {
            "name": name,
            "description": description,
            "input_schema": parameters  # 直接使用 parameters 作为 input_schema
        }
        
        mcp_tools.append(mcp_tool)
        logger.debug(f"Converted tool: {name}")
    
    return mcp_tools


def add_mcp_tools_to_request(request, tools: List[Dict[str, Any]]):
    """
    将 MCP 工具添加到 Warp Request 的 mcp_context 中
    
    Args:
        request: Warp Request protobuf 对象
        tools: OpenAI 格式的工具列表
    """
    if not tools:
        return
    
    # 转换为 MCP 格式
    mcp_tools = convert_openai_tools_to_mcp(tools)
    
    if not mcp_tools:
        logger.warning("No valid tools to add after conversion")
        return
    
    # 添加到 request.mcp_context.tools
    try:
        from google.protobuf import struct_pb2
        
        for mcp_tool in mcp_tools:
            tool_msg = request.mcp_context.tools.add()
            tool_msg.name = mcp_tool["name"]
            tool_msg.description = mcp_tool["description"]
            
            # 将 input_schema (dict) 转换为 google.protobuf.Struct
            if mcp_tool.get("input_schema"):
                # 使用 struct_pb2.Struct 的 update 方法
                tool_msg.input_schema.update(mcp_tool["input_schema"])
        
        logger.info(f"Added {len(mcp_tools)} custom tools to request")
        
    except Exception as e:
        logger.error(f"Failed to add MCP tools to request: {e}", exc_info=True)
