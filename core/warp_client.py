#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warp API 客户端 - 复用现有的Warp通信逻辑
"""
import sys
import os
import asyncio
import httpx
import base64
import json
import uuid
import hashlib
import secrets
from typing import Optional, AsyncGenerator, Dict, Any
from datetime import datetime, timedelta
import logging
from pathlib import Path

# 添加当前目录到路径
current_dir = Path(__file__).parent.parent
sys.path.insert(0, str(current_dir))

from warp2protobuf.core.protobuf import build_request_bytes
from warp2protobuf.core.protobuf_utils import protobuf_to_dict

# 使用最新的客户端版本信息
CLIENT_VERSION = "v0.2026.01.14.08.15.stable_04"
OS_CATEGORY = "macOS"
OS_NAME = "macOS"
OS_VERSION = "26.3"

logger = logging.getLogger(__name__)


class WarpClient:
    """Warp API 客户端"""
    
    REFRESH_URL = "https://app.warp.dev/proxy/token?key=AIzaSyBdy3O3S9hrdayLJxJ7mriBR4qgUaUygAs"
    LOGIN_URL = "https://app.warp.dev/client/login"
    AI_URL = "https://app.warp.dev/ai/multi-agent"
    GRAPHQL_URL = "https://app.warp.dev/graphql/v2"
    
    def __init__(self, account):
        """
        初始化客户端
        
        Args:
            account: Account对象
        """
        self.account = account
        self.session_cookies: Optional[dict] = None
        self.experiment_id: Optional[str] = None
        self.experiment_bucket: Optional[str] = None
        
        # HTTP客户端配置
        self.insecure_tls = os.getenv("WARP_INSECURE_TLS", "true").lower() in ("1", "true", "yes")
        self.timeout = httpx.Timeout(60.0)
    
    async def refresh_token(self) -> bool:
        """刷新JWT token"""
        try:
            if self.account.refresh_token:
                # 使用提供的refresh_token
                payload = f"grant_type=refresh_token&refresh_token={self.account.refresh_token}".encode("utf-8")
            else:
                # 使用内置的免费refresh_token（来自原版实现）
                logger.info(f"Account '{self.account.name}' using built-in free refresh token")
                from warp2protobuf.config.settings import REFRESH_TOKEN_B64
                import base64
                payload = base64.b64decode(REFRESH_TOKEN_B64)
            
            headers = {
                "x-warp-client-version": CLIENT_VERSION,
                "x-warp-os-category": OS_CATEGORY,
                "x-warp-os-name": OS_NAME,
                "x-warp-os-version": OS_VERSION,
                "content-type": "application/x-www-form-urlencoded",
                "accept": "*/*",
                "accept-encoding": "gzip, br"
            }
            
            async with httpx.AsyncClient(http2=True, timeout=self.timeout, verify=not self.insecure_tls) as client:
                response = await client.post(self.REFRESH_URL, headers=headers, content=payload)
                
                if response.status_code == 200:
                    data = response.json()
                    self.account.jwt_token = data.get("access_token") or data.get("idToken")
                    expires_in = int(data.get("expires_in", 3600))
                    self.account.jwt_expires_at = datetime.now() + timedelta(seconds=expires_in)
                    
                    # 更新refresh_token（如果返回了新的）
                    new_refresh = data.get("refresh_token")
                    if new_refresh:
                        self.account.refresh_token = new_refresh
                    
                    # 标记刷新时间
                    self.account.mark_token_refreshed()
                    
                    logger.info(f"Token refreshed for account '{self.account.name}', expires in {expires_in}s")
                    
                    # 触发配置保存
                    if self.account.account_manager:
                        import asyncio
                        asyncio.create_task(self.account.account_manager.save_account(self.account))
                    
                    return True
                else:
                    logger.error(f"Token refresh failed for '{self.account.name}': HTTP {response.status_code}")
                    # 标记对应的状态
                    if response.status_code == 403:
                        self.account.mark_blocked(403, "Blocked")
                    elif response.status_code == 429:
                        self.account.mark_blocked(429, "Too Many Requests")
                    return False
                    
        except httpx.TimeoutException as e:
            logger.error(f"Token refresh timeout for '{self.account.name}': {e}")
            # 超时不标记账户状态，可能是网络问题
            return False
        except httpx.ConnectError as e:
            logger.error(f"Token refresh connection error for '{self.account.name}': {e}")
            # 连接错误不标记账户状态，可能是网络问题
            return False
        except Exception as e:
            logger.error(f"Error refreshing token for '{self.account.name}': {e}")
            return False
    
    async def _acquire_anonymous_token(self) -> bool:
        """获取匿名token"""
        try:
            # Step 1: 创建匿名用户
            query = """
            mutation CreateAnonymousUser($requestContext: RequestContext!) {
                createAnonymousUser(requestContext: $requestContext) {
                    __typename
                    ... on CreateAnonymousUserOutput {
                        customToken
                    }
                }
            }
            """
            
            variables = {
                "requestContext": {
                    "clientContext": {"version": CLIENT_VERSION},
                    "osContext": {
                        "category": OS_CATEGORY,
                        "linuxKernelVersion": None,
                        "name": OS_NAME,
                        "version": OS_VERSION
                    }
                }
            }
            
            async with httpx.AsyncClient(http2=True, timeout=self.timeout, verify=not self.insecure_tls) as client:
                response = await client.post(
                    f"{self.GRAPHQL_URL}?op=CreateAnonymousUser",
                    json={"query": query, "variables": variables, "operationName": "CreateAnonymousUser"},
                    headers={"content-type": "application/json"}
                )
                
                if response.status_code != 200:
                    logger.error(f"Failed to create anonymous user: HTTP {response.status_code}")
                    return False
                
                data = response.json()
                custom_token = data.get("data", {}).get("createAnonymousUser", {}).get("customToken")
                
                if not custom_token:
                    logger.error("No customToken in response")
                    return False
                
                # Step 2: 使用customToken获取JWT
                identity_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key=AIzaSyBdy3O3S9hrdayLJxJ7mriBR4qgUaUygAs"
                
                response = await client.post(
                    identity_url,
                    json={"token": custom_token, "returnSecureToken": True}
                )
                
                if response.status_code != 200:
                    logger.error(f"Failed to sign in with custom token: HTTP {response.status_code}")
                    return False
                
                data = response.json()
                self.account.jwt_token = data.get("idToken")
                self.account.refresh_token = data.get("refreshToken")
                expires_in = int(data.get("expiresIn", 3600))
                self.account.jwt_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                logger.info(f"Anonymous token acquired for '{self.account.name}'")
                return True
                
        except Exception as e:
            logger.error(f"Error acquiring anonymous token: {e}")
            return False
    
    async def login(self) -> bool:
        """执行客户端登录"""
        try:
            # 确保有有效的JWT
            if self.account.is_jwt_expired():
                if not await self.refresh_token():
                    return False
            
            # 生成实验参数
            self.experiment_id = str(uuid.uuid4())
            self.experiment_bucket = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            
            headers = {
                "x-warp-client-id": "warp-app",
                "x-warp-client-version": CLIENT_VERSION,
                "x-warp-os-category": OS_CATEGORY,
                "x-warp-os-name": OS_NAME,
                "x-warp-os-version": OS_VERSION,
                "authorization": f"Bearer {self.account.jwt_token}",
                "x-warp-experiment-id": self.experiment_id,
                "x-warp-experiment-bucket": self.experiment_bucket,
                "accept": "*/*",
                "accept-encoding": "gzip,br",
                "content-length": "0"
            }
            
            async with httpx.AsyncClient(http2=True, timeout=self.timeout, verify=not self.insecure_tls) as client:
                response = await client.post(self.LOGIN_URL, headers=headers)
                
                if response.status_code == 204:
                    self.session_cookies = dict(response.cookies)
                    self.account.is_logged_in = True
                    logger.info(f"Client login successful for '{self.account.name}'")
                    return True
                else:
                    logger.error(f"Client login failed for '{self.account.name}': HTTP {response.status_code}")
                    # 标记对应的状态
                    if response.status_code == 403:
                        self.account.mark_blocked(403, "Blocked")
                    elif response.status_code == 429:
                        self.account.mark_blocked(429, "Too Many Requests")
                    return False
                    
        except httpx.TimeoutException as e:
            logger.error(f"Login timeout for '{self.account.name}': {e}")
            return False
        except httpx.ConnectError as e:
            logger.error(f"Login connection error for '{self.account.name}': {e}")
            return False
        except Exception as e:
            logger.error(f"Error during login for '{self.account.name}': {e}")
            return False
    
    async def initialize_session(self) -> bool:
        """初始化会话，获取 task_id"""
        try:
            if not await self.ensure_ready():
                return False
            
            # 如果已经有 task_id，直接返回
            if self.account.active_task_id:
                logger.info(f"Account '{self.account.name}' already has task_id: {self.account.active_task_id}")
                return True
            
            # 使用 new 模板发送初始化请求
            logger.info(f"Initializing session for account '{self.account.name}'...")
            
            # 发送一个简单的初始化消息
            init_message = "Hello"
            task_id = None
            
            async for event in self.chat_completion([{"role": "user", "content": init_message}], stream=True):
                # 优先从 client_actions 中提取 task_id
                if 'client_actions' in event or 'clientActions' in event:
                    actions_data = event.get('client_actions') or event.get('clientActions', {})
                    actions = actions_data.get('actions') or actions_data.get('Actions') or []
                    
                    logger.debug(f"Found {len(actions)} client actions")
                    
                    for action in actions:
                        logger.debug(f"Action keys: {list(action.keys())}")
                        
                        # 检查 create_task 动作
                        if 'create_task' in action or 'createTask' in action:
                            task_data = action.get('create_task') or action.get('createTask', {})
                            task_obj = task_data.get('task', {})
                            task_id = task_obj.get('id')
                            if task_id:
                                self.account.active_task_id = task_id
                                logger.info(f"Got task_id from create_task for '{self.account.name}': {task_id}")
                                return True
            
            if not self.account.active_task_id:
                logger.warning(f"Failed to get task_id for '{self.account.name}'")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error initializing session for '{self.account.name}': {e}")
            return False
    
    async def ensure_ready(self) -> bool:
        """确保客户端已准备好（有token且已登录）"""
        # 检查并刷新token
        if self.account.is_jwt_expired():
            if not await self.refresh_token():
                # refresh_token 内部已经标记了状态码（403/429）
                raise RuntimeError(f"Failed to prepare client for account '{self.account.name}': token refresh failed")
        
        # 检查并执行登录
        if not self.account.is_logged_in:
            if not await self.login():
                raise RuntimeError(f"Failed to prepare client for account '{self.account.name}': login failed")
        
        return True
    
    async def chat_completion(
        self,
        messages: list,
        model: str = "claude-4-sonnet",
        stream: bool = True,
        disable_warp_tools: bool = False,
        tools: list = None
    ) -> AsyncGenerator[dict, None]:
        """
        发送聊天请求
        
        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            model: 模型名称
            stream: 是否流式返回
            disable_warp_tools: 是否禁用Warp内置工具
        
        Yields:
            解析后的事件字典
        """
        # 确保客户端已准备好
        if not await self.ensure_ready():
            raise RuntimeError(f"Failed to prepare client for account '{self.account.name}'")
        
        # 构建Protobuf请求
        try:
            # 提取最后一条用户消息、历史消息和工具结果
            user_message = ""
            history_messages = []
            tool_results = []
            
            # 找到最后一个 assistant 消息的位置
            last_assistant_idx = -1
            for i, msg in enumerate(messages):
                if msg.get("role") == "assistant":
                    last_assistant_idx = i
            
            # 找到最后一个用户消息的位置（用于确定哪些是"待处理"的工具结果）
            last_user_idx = -1
            for i, msg in enumerate(messages):
                if msg.get("role") == "user":
                    last_user_idx = i
            
            for i, msg in enumerate(messages):
                role = msg.get("role", "")
                content = msg.get("content")  # 可能是 None
                
                # 跳过 system 消息（Warp不支持）
                if role == "system":
                    continue
                
                # 处理工具结果消息：提取最后一个用户消息之后的所有工具结果
                # 这样可以包含多轮工具调用的结果
                if role == "tool":
                    # 只提取最后一个用户消息之后的工具结果
                    if i > last_user_idx:
                        tool_call_id = msg.get("tool_call_id", "")
                        tool_results.append({
                            "tool_call_id": tool_call_id,
                            "content": content or ""  # 确保 content 是字符串
                        })
                        logger.debug(f"Extracted tool result: tool_call_id={tool_call_id}, content_len={len(content or '')}")
                    continue
                
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
                else:
                    # 添加到历史（包括之前的 user 和 assistant）
                    # 注意：assistant 消息可能只有 tool_calls 而没有 content
                    if role == "user":
                        if content:  # user 消息必须有 content
                            history_messages.append({"role": role, "content": content})
                    elif role == "assistant":
                        # assistant 消息：可能有 content，也可能有 tool_calls
                        msg_dict = {"role": role}
                        # 如果有 content（即使是空字符串），也添加
                        if content is not None:
                            msg_dict["content"] = content
                        # 如果有 tool_calls，添加
                        if msg.get("tool_calls"):
                            msg_dict["tool_calls"] = msg.get("tool_calls")
                        # 只有当有 content 或 tool_calls 时才添加
                        if "content" in msg_dict or "tool_calls" in msg_dict:
                            history_messages.append(msg_dict)
            
            # 如果有工具结果但没有新的用户消息，使用空字符串
            if not user_message and tool_results:
                user_message = ""
                logger.debug("No user message, only tool results")
            elif not user_message and not tool_results:
                raise ValueError("No user message or tool results found")
            
            # 限制工具结果数量和历史消息数量，只保留最近的 N 个
            # 从配置文件中读取，如果没有则使用默认值
            max_tool_results = 10
            max_history_messages = 20
            split_toolcall_result = False
            try:
                import sys
                if 'server' in sys.modules:
                    from server import settings
                    max_tool_results = settings.get('max_tool_results', 10) if settings else 10
                    max_history_messages = settings.get('max_history_messages', 20) if settings else 20
                    split_toolcall_result = settings.get('split_toolcall_result', False) if settings else False
            except (ImportError, AttributeError):
                pass
            
            if tool_results and len(tool_results) > max_tool_results:
                logger.info(f"Limiting tool results from {len(tool_results)} to {max_tool_results}")
                tool_results = tool_results[-max_tool_results:]
            
            if history_messages and len(history_messages) > max_history_messages:
                logger.info(f"Limiting history messages from {len(history_messages)} to {max_history_messages}")
                history_messages = history_messages[-max_history_messages:]
            
            # 检查是否需要分开发送工具结果
            if split_toolcall_result and tool_results and len(tool_results) > 1:
                logger.info(f"split_toolcall_result=True: Will send {len(tool_results)} tool results separately (interleaved in history)")
                
                # 将工具结果穿插到历史消息中，而不是单独提取
                # 重新构建消息列表，将 tool 消息保留在 assistant 之后
                accumulated_history = list(history_messages)
                
                # 找到每个工具结果对应的 assistant 消息
                # 构建一个映射：tool_call_id -> tool_result
                tool_result_map = {tr["tool_call_id"]: tr for tr in tool_results}
                
                # 遍历原始消息，找到 assistant 和对应的 tool
                for i, msg in enumerate(messages):
                    if msg.get("role") == "assistant" and msg.get("tool_calls"):
                        # 找到这个 assistant 后面的 tool 消息
                        for tc in msg.get("tool_calls", []):
                            tc_id = tc.get("id")
                            if tc_id in tool_result_map:
                                # 将这个 tool 结果添加到历史中
                                accumulated_history.append({
                                    "role": "tool",
                                    "tool_call_id": tc_id,
                                    "content": tool_result_map[tc_id]["content"]
                                })
                                logger.debug(f"Added tool result to history: {tc_id}")
                
                # 分开发送每个工具结果
                for idx, single_tool_result in enumerate(tool_results):
                    logger.info(f"Sending tool result {idx+1}/{len(tool_results)}: {single_tool_result['tool_call_id']}")
                    
                    # 构建包含累积历史的请求（工具结果已经在 accumulated_history 中）
                    protobuf_bytes = build_request_bytes(
                        user_message if idx == len(tool_results) - 1 else "",  # 只在最后一个请求中包含用户消息
                        model, 
                        disable_warp_tools,
                        history_messages=accumulated_history[:len(history_messages) + idx + 1],  # 逐步增加历史
                        task_id=self.account.active_task_id,
                        tools=tools,
                        tool_results=None  # 不使用 tool_results，而是放在 history 中
                    )
                    
                    # 发送请求并处理响应
                    new_conversation_id = None
                    async for event in self._send_request_and_parse(protobuf_bytes, model):
                        # 提取新的 conversation_id
                        if 'init' in event:
                            new_conversation_id = event['init'].get('conversation_id')
                        
                        # 只在最后一个工具结果时才yield事件
                        if idx == len(tool_results) - 1:
                            yield event
                    
                    # 更新 task_id 为新的 conversation_id
                    if new_conversation_id:
                        self.account.active_task_id = new_conversation_id
                        logger.debug(f"Updated task_id after tool result {idx+1}: {new_conversation_id}")
                
                # 已经处理完所有工具结果，直接返回
                return
            
            # 构建请求逻辑：
            # 1. 第一次请求（没有历史消息和工具结果）：使用 new 模板（is_new_conversation=true），不传递 task_id
            # 2. 有工具结果：使用 build_request_bytes_with_history，传递 tool_results
            # 3. 后续请求（有历史消息）：使用 build_request_bytes_with_history，传递 task_id
            if not history_messages and not tool_results:
                # 第一次请求，使用 new 模板，不传递 task_id
                protobuf_bytes = build_request_bytes(
                    user_message, 
                    model, 
                    disable_warp_tools,
                    tools=tools
                )
            else:
                # 后续请求，使用 task_id、历史消息和工具结果
                protobuf_bytes = build_request_bytes(
                    user_message, 
                    model, 
                    disable_warp_tools,
                    history_messages=history_messages,
                    task_id=self.account.active_task_id,
                    tools=tools,
                    tool_results=tool_results
                )
            
            logger.debug(f"Built protobuf request: {len(protobuf_bytes)} bytes, history_count={len(history_messages)}, tool_results_count={len(tool_results)}")
            
            # DEBUG: 打印提交给Warp的请求内容
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("=" * 60)
                logger.debug("[Warp Request] Submitting to Warp API:")
                if user_message:
                    logger.debug(f"  Current query: {user_message[:200]}..." if len(user_message) > 200 else f"  Current query: {user_message}")
                logger.debug(f"  Model: {model}")
                logger.debug(f"  History messages: {len(history_messages)}")
                for i, h in enumerate(history_messages):
                    content_preview = h['content'][:100] + '...' if len(h['content']) > 100 else h['content']
                    logger.debug(f"    [{i}] {h['role']}: {content_preview}")
                logger.debug(f"  Tool results: {len(tool_results)}")
                for i, tr in enumerate(tool_results):
                    content_preview = tr['content'][:100] + '...' if len(tr['content']) > 100 else tr['content']
                    logger.debug(f"    [{i}] tool_call_id={tr['tool_call_id']}: {content_preview}")
                logger.debug(f"  Protobuf size: {len(protobuf_bytes)} bytes")
                logger.debug(f"  Protobuf hex (first 200): {protobuf_bytes[:100].hex()}")
                # 尝试解码protobuf为可读格式
                try:
                    from warp2protobuf.core.protobuf_utils import protobuf_to_dict
                    request_dict = protobuf_to_dict(protobuf_bytes, 'warp.multi_agent.v1.Request')
                    # 打印关键字段
                    tc = request_dict.get('task_context', {})
                    if tc.get('tasks'):
                        logger.debug(f"  task_context.active_task_id: {tc.get('active_task_id', 'N/A')}")
                        logger.debug(f"  task_context.tasks[0].messages count: {len(tc['tasks'][0].get('messages', []))}")
                except Exception as e:
                    logger.debug(f"  (Could not decode protobuf for display: {e})")
                logger.debug("=" * 60)
            
        except Exception as e:
            logger.error(f"Error building protobuf request: {e}")
            raise
        
        # 发送请求并解析响应
        async for event in self._send_request_and_parse(protobuf_bytes, model):
            yield event
    
    async def _send_request_and_parse(self, protobuf_bytes: bytes, model: str) -> AsyncGenerator[dict, None]:
        """
        发送 Protobuf 请求到 Warp API 并解析 SSE 响应流
        
        Args:
            protobuf_bytes: 编码后的 Protobuf 请求
            model: 模型名称（用于日志）
        
        Yields:
            解析后的 SSE 事件字典
        """
        # 发送请求 - 注意：不要使用压缩编码，否则会导致流式输出缓冲
        headers = {
            "x-warp-client-id": "warp-app",
            "accept": "text/event-stream",
            "content-type": "application/x-protobuf",
            "x-warp-client-version": CLIENT_VERSION,
            "x-warp-os-category": OS_CATEGORY,
            "x-warp-os-name": OS_NAME,
            "x-warp-os-version": OS_VERSION,
            "authorization": f"Bearer {self.account.jwt_token}",
            "accept-encoding": "identity",  # 禁用压缩以支持真正的流式输出
            "content-length": str(len(protobuf_bytes))
        }
        
        try:
            # 禁用HTTP/2以避免流式缓冲问题
            async with httpx.AsyncClient(
                http2=False,  # HTTP/2 可能导致流式缓冲
                timeout=self.timeout, 
                verify=not self.insecure_tls
            ) as client:
                # 构建请求并移除user-agent
                request = client.build_request("POST", self.AI_URL, headers=headers, content=protobuf_bytes)
                if 'user-agent' in request.headers:
                    del request.headers['user-agent']
                
                # 发送请求
                response = await client.send(request, stream=True)
                
                logger.info(f"Response status: {response.status_code}")
                logger.info(f"Response headers: {dict(response.headers)}")
                
                if response.status_code != 200:
                    error_text = await response.aread()
                    error_msg = error_text.decode('utf-8') if error_text else "No error content"
                    
                    # 检查 403 错误（账户被封禁）
                    if response.status_code == 403:
                        logger.error(f"Account '{self.account.name}' has been blocked (403)")
                        self.account.mark_blocked(403, "Blocked")
                    
                    # 检查 429 错误（请求过多）
                    elif response.status_code == 429:
                        logger.error(f"Account '{self.account.name}' rate limited (429)")
                        self.account.mark_blocked(429, "Too Many Requests")
                    
                    # 检查配额错误
                    elif "No remaining quota" in error_msg or "No AI requests remaining" in error_msg:
                        self.account.mark_quota_exceeded()
                    
                    raise RuntimeError(f"Warp API error: HTTP {response.status_code}: {error_msg}")
                
                # 标记账号被使用
                self.account.mark_used()
                
                # 解析SSE流
                event_count = 0
                response_texts = []  # 用于收集响应文本
                new_conversation_id = None  # 用于保存本次请求返回的 conversation_id
                
                async for event in self._parse_sse_stream(response):
                    event_count += 1
                    
                    # 从 init 事件中提取 conversation_id（这才是真正的 task_id）
                    if 'init' in event and not new_conversation_id:
                        init_data = event.get('init', {})
                        new_conversation_id = init_data.get('conversation_id')
                        if new_conversation_id:
                            logger.info(f"Captured conversation_id from init: {new_conversation_id}")
                    
                    # DEBUG: 收集响应内容
                    if logger.isEnabledFor(logging.DEBUG):
                        # 提取文本内容
                        if 'client_actions' in event or 'clientActions' in event:
                            actions_data = event.get('client_actions') or event.get('clientActions', {})
                            actions = actions_data.get('actions') or actions_data.get('Actions') or []
                            for action in actions:
                                if 'append_to_message_content' in action or 'appendToMessageContent' in action:
                                    append_data = action.get('append_to_message_content') or action.get('appendToMessageContent', {})
                                    msg_data = append_data.get('message', {})
                                    agent_output = msg_data.get('agent_output') or msg_data.get('agentOutput', {})
                                    text = agent_output.get('text', '')
                                    if text:
                                        response_texts.append(text)
                    
                    yield event
                
                # 请求完成后，更新账户的 task_id 为本次返回的 conversation_id
                if new_conversation_id:
                    self.account.active_task_id = new_conversation_id
                    logger.info(f"Updated task_id (conversation_id) for '{self.account.name}': {new_conversation_id}")
                
                logger.info(f"Total events yielded: {event_count}")
                
                # DEBUG: 打印Warp返回的完整响应
                if logger.isEnabledFor(logging.DEBUG) and response_texts:
                    full_response = ''.join(response_texts)
                    logger.debug("=" * 60)
                    logger.debug("[Warp Response] Response from Warp API:")
                    logger.debug(f"  Total events: {event_count}")
                    logger.debug(f"  Response length: {len(full_response)} chars")
                    response_preview = full_response[:500] + '...' if len(full_response) > 500 else full_response
                    logger.debug(f"  Content: {response_preview}")
                    logger.debug("=" * 60)
                    
        except Exception as e:
            self.account.mark_error(str(e))
            logger.error(f"Error in chat_completion for '{self.account.name}': {e}")
            
            # 如果是 403 错误，保存账户配置
            if "HTTP 403" in str(e):
                try:
                    if self.account.account_manager:
                        import asyncio
                        asyncio.create_task(self.account.account_manager.save_account(self.account))
                        logger.info(f"Triggered account save for '{self.account.name}' after 403 error")
                except Exception as save_error:
                    logger.error(f"Failed to trigger account save after 403: {save_error}")
            
            raise
    
    async def _parse_sse_stream(self, response: httpx.Response) -> AsyncGenerator[dict, None]:
        """解析SSE流 - 支持 event:data\ndata:<base64> 格式"""
        current_event_type = None
        current_data = ""
        line_count = 0
        buffer = b""
        
        # 使用较小的chunk size来减少缓冲
        async for chunk in response.aiter_bytes(chunk_size=256):
            buffer += chunk
            
            # 按行分割 - 处理所有完整的行
            while b"\n" in buffer:
                line_bytes, buffer = buffer.split(b"\n", 1)
                line_count += 1
                
                try:
                    line = line_bytes.decode('utf-8').strip()
                except UnicodeDecodeError:
                    logger.warning(f"Failed to decode line {line_count}")
                    continue
                
                # 空行表示事件结束，处理累积的数据
                if not line:
                    if current_data:
                        try:
                            # 解码base64 - 支持 URL-safe 编码并添加必要的填充
                            padded_data = current_data
                            padding_needed = len(current_data) % 4
                            if padding_needed:
                                padded_data += '=' * (4 - padding_needed)
                            
                            # URL-safe base64 解码
                            protobuf_bytes = base64.urlsafe_b64decode(padded_data)
                            
                            # 解析protobuf
                            event_dict = protobuf_to_dict(protobuf_bytes, "warp.multi_agent.v1.ResponseEvent")
                            
                            # Include raw payload for tool call parsing
                            event_dict["raw_payload"] = protobuf_bytes
                            
                            yield event_dict
                            
                        except Exception as e:
                            logger.warning(f"Failed to parse SSE event: {e}")
                        finally:
                            # 重置状态
                            current_event_type = None
                            current_data = ""
                    continue
                
                # 跳过注释行
                if line.startswith(":"):
                    continue
                
                # 解析 event: 行
                if line.startswith("event:"):
                    current_event_type = line[6:].strip()
                    continue
                
                # 解析 data: 行
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    
                    if data_str == "[DONE]":
                        logger.info("Stream finished: [DONE]")
                        return
                    
                    # 累积数据
                    current_data += data_str
                    continue
        
        # 处理缓冲区中剩余的数据
        if current_data:
            try:
                padded_data = current_data
                padding_needed = len(current_data) % 4
                if padding_needed:
                    padded_data += '=' * (4 - padding_needed)
                protobuf_bytes = base64.urlsafe_b64decode(padded_data)
                event_dict = protobuf_to_dict(protobuf_bytes, "warp.multi_agent.v1.ResponseEvent")
                # Include raw payload for tool call parsing
                event_dict["raw_payload"] = protobuf_bytes
                yield event_dict
            except Exception as e:
                logger.warning(f"Failed to parse final SSE event: {e}")
        
        logger.info(f"Stream ended after {line_count} lines")
    
    async def get_usage(self) -> dict:
        """获取账号用量信息"""
        try:
            if self.account.is_jwt_expired():
                if not await self.refresh_token():
                    return {}
            
            query = """
            query GetRequestLimitInfo($requestContext: RequestContext!) {
                user(requestContext: $requestContext) {
                    __typename
                    ... on UserOutput {
                        user {
                            requestLimitInfo {
                                isUnlimited
                                nextRefreshTime
                                requestLimit
                                requestsUsedSinceLastRefresh
                                requestLimitRefreshDuration
                            }
                        }
                    }
                }
            }
            """
            
            variables = {
                "requestContext": {
                    "clientContext": {"version": CLIENT_VERSION},
                    "osContext": {
                        "category": OS_CATEGORY,
                        "linuxKernelVersion": None,
                        "name": OS_NAME,
                        "version": OS_VERSION
                    }
                }
            }
            
            headers = {
                "x-warp-client-id": "warp-app",
                "x-warp-client-version": CLIENT_VERSION,
                "x-warp-os-category": OS_CATEGORY,
                "x-warp-os-name": OS_NAME,
                "x-warp-os-version": OS_VERSION,
                "content-type": "application/json",
                "authorization": f"Bearer {self.account.jwt_token}",
                "accept": "*/*",
                "accept-encoding": "gzip,br"
            }
            
            async with httpx.AsyncClient(http2=True, timeout=self.timeout, verify=not self.insecure_tls) as client:
                response = await client.post(
                    f"{self.GRAPHQL_URL}?op=GetRequestLimitInfo",
                    json={"query": query, "variables": variables, "operationName": "GetRequestLimitInfo"},
                    headers=headers
                )
                
                if response.status_code == 200:
                    data = response.json()
                    limit_info = data.get("data", {}).get("user", {}).get("user", {}).get("requestLimitInfo", {})
                    
                    # 更新账号配额信息
                    self.account.quota_limit = limit_info.get("requestLimit", 0)
                    self.account.quota_used = limit_info.get("requestsUsedSinceLastRefresh", 0)
                    
                    if self.account.quota_used >= self.account.quota_limit:
                        self.account.mark_quota_exceeded()
                    
                    return limit_info
                else:
                    logger.error(f"Failed to get usage for '{self.account.name}': HTTP {response.status_code}")
                    return {}
                    
        except Exception as e:
            logger.error(f"Error getting usage for '{self.account.name}': {e}")
            return {}
