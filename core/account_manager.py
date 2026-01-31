#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
账号管理器 - 管理多个Warp账号的JWT token和轮询策略
"""
import asyncio
import json
import time
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class StrategyType(str, Enum):
    """轮询策略类型"""
    ROUND_ROBIN = "round-robin"
    RANDOM = "random"
    LEAST_USED = "least-used"
    QUOTA_AWARE = "quota-aware"


class Account:
    """账号信息"""
    
    def __init__(self, name: str, refresh_token: Optional[str], enabled: bool = True, status_code: Optional[str] = None, last_refreshed: Optional[str] = None, last_attempt: Optional[str] = None, jwt_token: Optional[str] = None, jwt_expires_at: Optional[str] = None):
        self.name = name
        self.refresh_token = refresh_token
        self.enabled = enabled
        self.status_code = status_code  # 账户状态码：None(正常), "403"(封禁), "429"(限流), "quota_exceeded"(配额用尽)
        
        # Token 刷新时间（ISO 格式字符串，持久化到配置文件）
        self.last_refreshed: Optional[datetime] = None
        if last_refreshed:
            try:
                self.last_refreshed = datetime.fromisoformat(last_refreshed)
            except (ValueError, TypeError):
                self.last_refreshed = None
        
        # 上次尝试时间（用于 429 限流恢复判断）
        self.last_attempt: Optional[datetime] = None
        if last_attempt:
            try:
                self.last_attempt = datetime.fromisoformat(last_attempt)
            except (ValueError, TypeError):
                self.last_attempt = None
        
        # JWT 运行时状态（只在内存中，不持久化）
        # 忽略从配置文件传入的 jwt_token 和 jwt_expires_at
        self.jwt_token: Optional[str] = None
        self.jwt_expires_at: Optional[datetime] = None
        
        self.is_logged_in: bool = False
        self.quota_limit: int = 0
        self.quota_used: int = 0
        self.quota_reset_date: Optional[datetime] = None  # 配额重置日期（月初）
        self.last_used: Optional[datetime] = None
        self.request_count: int = 0
        self.error_count: int = 0
        self.last_error: Optional[str] = None
        
        # 会话ID（用于多轮对话）
        self.conversation_id: Optional[str] = None
        self.active_task_id: Optional[str] = None
        
        # WarpClient 实例（每个账户一个实例，用于维护会话状态）
        self._warp_client = None
        
        # AccountManager 引用（用于保存配置）
        self.account_manager = None
        
        # 429 重试间隔（分钟），从 AccountManager 获取
        self.retry_429_interval: int = 60
        
    def get_warp_client(self):
        """获取或创建 WarpClient 实例"""
        if self._warp_client is None:
            from .warp_client import WarpClient
            self._warp_client = WarpClient(self)
        return self._warp_client
        
    def is_jwt_expired(self, buffer_minutes: int = 10) -> bool:
        """检查JWT是否过期（默认提前10分钟）"""
        if not self.jwt_token or not self.jwt_expires_at:
            return True
        return datetime.now() + timedelta(minutes=buffer_minutes) >= self.jwt_expires_at
    
    def is_available(self) -> bool:
        """检查账号是否可用"""
        # 未启用的账户不可用
        if not self.enabled:
            return False
        
        # 检查配额是否已重置（月初自动重置）
        if self.status_code == "quota_exceeded" and self.quota_reset_date:
            if datetime.now() >= self.quota_reset_date:
                # 自动重置配额
                logger.info(f"Auto-resetting quota for '{self.name}' (monthly reset)")
                self.status_code = None
                self.quota_used = 0
                self.quota_reset_date = None
        
        # 基于 status_code 判断账户状态
        if self.status_code:
            try:
                code = int(self.status_code)
                # 403 封禁 - 不可用
                if code == 403:
                    return False
                # 429 限流 - 检查是否超过重试间隔
                if code == 429:
                    if self.last_attempt:
                        elapsed_minutes = (datetime.now() - self.last_attempt).total_seconds() / 60
                        if elapsed_minutes >= self.retry_429_interval:
                            # 超过重试间隔，清除 429 状态，允许重试
                            logger.info(f"Account '{self.name}' 429 retry interval ({self.retry_429_interval}min) elapsed, allowing retry")
                            self.status_code = None
                            self.last_attempt = None
                            return True
                        return False
                    else:
                        # 没有 last_attempt 记录，允许尝试
                        logger.info(f"Account '{self.name}' has 429 status but no last_attempt, allowing retry")
                        self.status_code = None
                        return True
            except (ValueError, TypeError):
                # 非数字状态，检查特殊状态
                if self.status_code == "quota_exceeded":
                    return False
        
        return True
    
    def should_refresh_token(self) -> bool:
        """检查是否应该刷新 token"""
        # 如果账户未启用，不刷新
        if not self.enabled:
            return False
        # 如果有有效的 JWT token，检查是否即将过期（10分钟内）
        if self.jwt_token and self.jwt_expires_at:
            buffer_minutes = 10  # 提前10分钟刷新
            if datetime.now() + timedelta(minutes=buffer_minutes) < self.jwt_expires_at:
                return False  # Token 还有效，不需要刷新
        # 其他情况需要刷新
        return True
    
    def get_quota_remaining(self) -> int:
        """获取剩余配额"""
        return max(0, self.quota_limit - self.quota_used)
    
    def mark_used(self):
        """标记账号被使用"""
        self.last_used = datetime.now()
        self.request_count += 1
        self.quota_used += 1
    
    def mark_error(self, error: str):
        """标记错误"""
        self.error_count += 1
        self.last_error = error
        logger.warning(f"Account '{self.name}' error: {error}")
    
    def mark_blocked(self, status_code: int = 403, status_description: str = "Blocked"):
        """标记账户状态（临时标记，不禁用账户）
        
        Args:
            status_code: HTTP状态码，如 403, 429
            status_description: 状态描述，如 "Blocked", "Too Many Requests"
        """
        self.status_code = f"{status_code}"
        self.last_error = f"{status_code} {status_description}"
        self.last_attempt = datetime.now()  # 记录尝试时间
        logger.warning(f"Account '{self.name}' marked with status: {status_code} {status_description}")
        
        # 触发单个账户配置保存
        if self.account_manager:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.account_manager.save_account(self))
                else:
                    loop.run_until_complete(self.account_manager.save_account(self))
            except Exception as e:
                logger.error(f"Failed to save account after marking blocked: {e}")
    
    def mark_quota_exceeded(self):
        """标记账户配额用尽（临时状态，月初会自动重置）"""
        self.status_code = "quota_exceeded"
        
        # 计算下次重置日期（下个月1号 00:00:00）
        today = datetime.now()
        if today.month == 12:
            next_reset = datetime(today.year + 1, 1, 1)
        else:
            next_reset = datetime(today.year, today.month + 1, 1)
        
        self.quota_reset_date = next_reset
        days_until_reset = (next_reset - today).days
        
        logger.warning(
            f"Quota exceeded for '{self.name}'. "
            f"Will reset on {next_reset.strftime('%Y-%m-%d')} "
            f"({days_until_reset} days remaining)"
        )
    
    def mark_token_refreshed(self):
        """标记 token 已刷新"""
        self.last_refreshed = datetime.now()
        logger.debug(f"Account '{self.name}' token refreshed at {self.last_refreshed.isoformat()}")
    
    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "name": self.name,
            "enabled": self.enabled,
            "status_code": self.status_code,
            "is_logged_in": self.is_logged_in,
            "quota_limit": self.quota_limit,
            "quota_used": self.quota_used,
            "quota_remaining": self.get_quota_remaining(),
            "quota_reset_date": self.quota_reset_date.isoformat() if self.quota_reset_date else None,
            "request_count": self.request_count,
            "error_count": self.error_count,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "last_error": self.last_error,
            "jwt_expired": self.is_jwt_expired(),
            "last_refreshed": self.last_refreshed.isoformat() if self.last_refreshed else None,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None
        }
        return result
    
    def to_config_dict(self) -> dict:
        """转换为配置文件格式（只包含持久化字段）"""
        result = {
            "name": self.name,
            "refresh_token": self.refresh_token,
            "enabled": self.enabled
        }
        if self.status_code:
            result["status_code"] = self.status_code
        if self.last_refreshed:
            result["last_refreshed"] = self.last_refreshed.isoformat()
        if self.last_attempt:
            result["last_attempt"] = self.last_attempt.isoformat()
        return result


class AccountManager:
    """账号管理器"""
    
    def __init__(
        self, 
        accounts: List[Account], 
        strategy: StrategyType = StrategyType.ROUND_ROBIN,
        accounts_dir: Optional[str] = None,
        auto_save: bool = True,
        retry_429_interval: int = 60
    ):
        self.accounts = accounts
        self.strategy = strategy
        self.current_index = 0
        self.lock = asyncio.Lock()
        self.accounts_dir = accounts_dir  # 账户文件目录
        self.auto_save = auto_save
        self.retry_429_interval = retry_429_interval  # 429 重试间隔（分钟）
        
        # 设置每个账户的 account_manager 引用和 429 重试间隔
        for account in self.accounts:
            account.account_manager = self
            account.retry_429_interval = retry_429_interval
        
        logger.info(f"AccountManager initialized with {len(accounts)} accounts, strategy: {strategy}")
        logger.info(f"Auto-save tokens: {auto_save}, accounts_dir: {accounts_dir}, retry_429_interval: {retry_429_interval}min")
    
    async def save_account(self, account: Account):
        """保存单个账户到文件"""
        if not self.accounts_dir or not self.auto_save:
            return
        
        try:
            accounts_path = Path(self.accounts_dir)
            accounts_path.mkdir(parents=True, exist_ok=True)
            
            # 使用账户名作为文件名
            safe_name = account.name.replace("/", "_").replace("\\", "_")
            account_file = accounts_path / f"{safe_name}.json"
            
            # 写入账户配置
            config = account.to_config_dict()
            with open(account_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"Account saved to: {account_file}")
            
        except Exception as e:
            logger.error(f"Failed to save account '{account.name}': {e}")
    
    async def save_all_accounts(self):
        """保存所有账户到文件"""
        if not self.accounts_dir or not self.auto_save:
            return
        
        for account in self.accounts:
            await self.save_account(account)
        
        logger.info(f"All {len(self.accounts)} accounts saved to: {self.accounts_dir}")
    
    async def delete_account_file(self, account_name: str):
        """删除账户文件"""
        if not self.accounts_dir:
            return
        
        try:
            accounts_path = Path(self.accounts_dir)
            safe_name = account_name.replace("/", "_").replace("\\", "_")
            account_file = accounts_path / f"{safe_name}.json"
            
            if account_file.exists():
                account_file.unlink()
                logger.info(f"Account file deleted: {account_file}")
        except Exception as e:
            logger.error(f"Failed to delete account file '{account_name}': {e}")
    
    async def get_next_account(self) -> Account:
        """获取下一个可用账号"""
        async with self.lock:
            if self.strategy == StrategyType.ROUND_ROBIN:
                return await self._round_robin()
            elif self.strategy == StrategyType.RANDOM:
                return await self._random()
            elif self.strategy == StrategyType.LEAST_USED:
                return await self._least_used()
            elif self.strategy == StrategyType.QUOTA_AWARE:
                return await self._quota_aware()
            else:
                raise ValueError(f"Unknown strategy: {self.strategy}")
    
    async def _round_robin(self) -> Account:
        """轮询策略"""
        attempts = 0
        max_attempts = len(self.accounts) * 2  # 允许循环两次
        
        while attempts < max_attempts:
            account = self.accounts[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.accounts)
            attempts += 1
            
            if account.is_available():
                logger.debug(f"Selected account (round-robin): {account.name}")
                return account
            else:
                logger.debug(f"Skipping unavailable account: {account.name}")
        
        raise NoAvailableAccountError("所有账号都不可用")
    
    async def _random(self) -> Account:
        """随机策略"""
        import random
        available = [acc for acc in self.accounts if acc.is_available()]
        
        if not available:
            raise NoAvailableAccountError("所有账号都不可用")
        
        account = random.choice(available)
        logger.debug(f"Selected account (random): {account.name}")
        return account
    
    async def _least_used(self) -> Account:
        """最少使用策略"""
        available = [acc for acc in self.accounts if acc.is_available()]
        
        if not available:
            raise NoAvailableAccountError("所有账号都不可用")
        
        # 按使用次数排序
        available.sort(key=lambda x: x.request_count)
        account = available[0]
        logger.debug(f"Selected account (least-used): {account.name} (used: {account.request_count})")
        return account
    
    async def _quota_aware(self) -> Account:
        """配额感知策略"""
        available = [acc for acc in self.accounts if acc.is_available()]
        
        if not available:
            raise NoAvailableAccountError("所有账号都不可用")
        
        # 按剩余配额排序（降序）
        available.sort(key=lambda x: x.get_quota_remaining(), reverse=True)
        account = available[0]
        logger.debug(f"Selected account (quota-aware): {account.name} (remaining: {account.get_quota_remaining()})")
        return account
    
    def get_account_by_name(self, name: str) -> Optional[Account]:
        """根据名称获取账号"""
        for account in self.accounts:
            if account.name == name:
                return account
        return None
    
    def get_available_accounts(self) -> List[Account]:
        """获取所有可用账号"""
        return [acc for acc in self.accounts if acc.is_available()]
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        total_requests = sum(acc.request_count for acc in self.accounts)
        total_errors = sum(acc.error_count for acc in self.accounts)
        available_count = len(self.get_available_accounts())
        
        return {
            "total_accounts": len(self.accounts),
            "enabled_accounts": len([acc for acc in self.accounts if acc.enabled]),
            "available_accounts": available_count,
            "logged_in_accounts": len([acc for acc in self.accounts if acc.is_logged_in]),
            "quota_exceeded_accounts": len([acc for acc in self.accounts if acc.status_code == "quota_exceeded"]),
            "total_requests": total_requests,
            "total_errors": total_errors,
            "strategy": self.strategy.value,
            "accounts": [acc.to_dict() for acc in self.accounts]
        }
    
    async def refresh_all_tokens(self, delay_between_requests: float = 1.0):
        """
        串行刷新所有过期的token，避免触发429限流
        
        Args:
            delay_between_requests: 每个请求之间的延迟秒数（默认1秒）
        """
        from .warp_client import WarpClient
        
        # 只刷新需要刷新的账户（跳过403等被封禁账户）
        accounts_to_refresh = [acc for acc in self.accounts if acc.should_refresh_token()]
        
        if not accounts_to_refresh:
            logger.info("No accounts need token refresh")
            return
        
        total_accounts = len(accounts_to_refresh)
        logger.info(f"Refreshing {total_accounts} tokens serially (delay: {delay_between_requests}s between requests)...")
        
        success_count = 0
        for idx, account in enumerate(accounts_to_refresh, 1):
            logger.info(f"[{idx}/{total_accounts}] Refreshing token for: {account.name}")
            
            try:
                success = await self._refresh_single_token(account)
                if success:
                    success_count += 1
                    logger.info(f"[{idx}/{total_accounts}] ✓ Success: {account.name}")
                else:
                    logger.warning(f"[{idx}/{total_accounts}] ✗ Failed: {account.name}")
            except Exception as e:
                logger.error(f"[{idx}/{total_accounts}] ✗ Error for {account.name}: {e}")
            
            # 每个请求之间延迟（最后一个不需要延迟）
            if idx < total_accounts:
                logger.debug(f"Waiting {delay_between_requests}s before next request...")
                await asyncio.sleep(delay_between_requests)
        
        logger.info(f"Token refresh completed: {success_count}/{total_accounts} succeeded")
        
        # 保存配置（一次性保存所有更新）
        if success_count > 0:
            await self.save_all_accounts()
    
    async def _refresh_single_token(self, account: Account) -> bool:
        """刷新单个账户的token"""
        from .warp_client import WarpClient
        
        try:
            logger.info(f"Refreshing token for account: {account.name}")
            client = WarpClient(account)
            success = await client.refresh_token()
            if success:
                # 标记刷新时间
                account.mark_token_refreshed()
                logger.info(f"Token refreshed successfully for: {account.name}")
                return True
            else:
                logger.error(f"Failed to refresh token for: {account.name}")
                account.mark_error("Token refresh failed")
                return False
        except Exception as e:
            logger.error(f"Error refreshing token for {account.name}: {e}")
            account.mark_error(str(e))
            return False
    
    async def initialize_all_sessions(self, delay_between_requests: float = 1.0):
        """
        串行初始化所有账户的会话，获取 task_id，避免触发429限流
        
        Args:
            delay_between_requests: 每个请求之间的延迟秒数（默认1秒）
        """
        from .warp_client import WarpClient
        
        # 只初始化需要初始化的账户
        accounts_to_init = [acc for acc in self.accounts if acc.enabled and not acc.active_task_id]
        
        if not accounts_to_init:
            logger.info("No accounts need session initialization")
            return
        
        total_accounts = len(accounts_to_init)
        logger.info(f"Initializing {total_accounts} sessions serially (delay: {delay_between_requests}s between requests)...")
        
        success_count = 0
        for idx, account in enumerate(accounts_to_init, 1):
            logger.info(f"[{idx}/{total_accounts}] Initializing session for: {account.name}")
            
            try:
                success = await self._initialize_single_session(account)
                if success:
                    success_count += 1
                    logger.info(f"[{idx}/{total_accounts}] ✓ Success: {account.name} (task_id: {account.active_task_id})")
                else:
                    logger.warning(f"[{idx}/{total_accounts}] ✗ Failed: {account.name}")
            except Exception as e:
                logger.error(f"[{idx}/{total_accounts}] ✗ Error for {account.name}: {e}")
            
            # 每个请求之间延迟（最后一个不需要延迟）
            if idx < total_accounts:
                logger.debug(f"Waiting {delay_between_requests}s before next request...")
                await asyncio.sleep(delay_between_requests)
        
        logger.info(f"Session initialization completed: {success_count}/{total_accounts} succeeded")
    
    async def _initialize_single_session(self, account: Account) -> bool:
        """初始化单个账户的会话"""
        from .warp_client import WarpClient
        
        try:
            logger.info(f"Initializing session for account: {account.name}")
            client = WarpClient(account)
            success = await client.initialize_session()
            if success:
                logger.info(f"Session initialized for: {account.name}, task_id: {account.active_task_id}")
                return True
            else:
                logger.warning(f"Failed to initialize session for: {account.name}")
                return False
        except Exception as e:
            logger.error(f"Error initializing session for {account.name}: {e}")
            account.mark_error(str(e))
            return False


class NoAvailableAccountError(Exception):
    """没有可用账号的异常"""
    pass


def load_accounts_from_directory(accounts_dir: str, strategy: StrategyType = StrategyType.ROUND_ROBIN, auto_save: bool = True, retry_429_interval: int = 60) -> AccountManager:
    """从目录加载所有账户文件
    
    Args:
        accounts_dir: 账户文件目录路径，如 config/accounts/warp
        strategy: 轮询策略
        auto_save: 是否自动保存
        retry_429_interval: 429 限流重试间隔（分钟）
    
    Returns:
        AccountManager 实例
    """
    accounts_path = Path(accounts_dir)
    
    if not accounts_path.exists():
        accounts_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created accounts directory: {accounts_path}")
    
    accounts = []
    
    # 遍历目录中的所有 JSON 文件
    for account_file in sorted(accounts_path.glob("*.json")):
        try:
            with open(account_file, 'r', encoding='utf-8') as f:
                acc_config = json.load(f)
            
            account = Account(
                name=acc_config.get("name", account_file.stem),
                refresh_token=acc_config.get("refresh_token"),
                enabled=acc_config.get("enabled", True),
                status_code=acc_config.get("status_code"),
                last_refreshed=acc_config.get("last_refreshed"),
                last_attempt=acc_config.get("last_attempt")
            )
            accounts.append(account)
            
            # 记录账户状态
            status_info = f" (status_code: {account.status_code})" if account.status_code else ""
            refresh_info = f" (last_refreshed: {account.last_refreshed.isoformat()})" if account.last_refreshed else ""
            attempt_info = f" (last_attempt: {account.last_attempt.isoformat()})" if account.last_attempt else ""
            logger.info(f"Loaded account: {account.name} (enabled: {account.enabled}){status_info}{refresh_info}{attempt_info}")
            
        except Exception as e:
            logger.error(f"Failed to load account from {account_file}: {e}")
    
    if not accounts:
        logger.warning(f"No accounts found in {accounts_dir}")
    
    logger.info(f"Loaded {len(accounts)} accounts from {accounts_dir}")
    
    return AccountManager(accounts, strategy, accounts_dir=accounts_dir, auto_save=auto_save, retry_429_interval=retry_429_interval)
