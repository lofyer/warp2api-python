#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warp Client Login Module

Handles the client login flow required before making API calls.
"""
import os
import uuid
import hashlib
import secrets
import httpx
from typing import Optional, Tuple

from ..config.settings import CLIENT_ID, CLIENT_VERSION, OS_CATEGORY, OS_NAME, OS_VERSION
from ..core.logging import logger
from ..core.auth import get_valid_jwt


# Global session state
_session_cookies: Optional[dict] = None
_experiment_id: Optional[str] = None
_experiment_bucket: Optional[str] = None


def generate_experiment_params() -> Tuple[str, str]:
    """Generate experiment ID and bucket for A/B testing"""
    experiment_id = str(uuid.uuid4())
    experiment_bucket = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    return experiment_id, experiment_bucket


async def client_login(show_info: Optional[bool] = None) -> bool:
    """
    Perform client login to establish session with Warp server.
    
    This must be called before making any AI API requests.
    Returns True if login successful, False otherwise.
    
    Args:
        show_info: If True, fetch and display model choices and usage info after login.
                   If None, check WARP_SHOW_LOGIN_INFO environment variable (default: True)
    """
    global _session_cookies, _experiment_id, _experiment_bucket
    
    # Determine whether to show info
    if show_info is None:
        show_info_env = os.getenv("WARP_SHOW_LOGIN_INFO", "true").lower()
        show_info = show_info_env in ("1", "true", "yes")
    
    try:
        logger.info("Performing Warp client login...")
        
        # Generate experiment parameters
        _experiment_id, _experiment_bucket = generate_experiment_params()
        
        # Get JWT token
        jwt = await get_valid_jwt()
        
        # Prepare login request
        login_url = "https://app.warp.dev/client/login"
        headers = {
            "x-warp-client-id": CLIENT_ID,
            "x-warp-client-version": CLIENT_VERSION,
            "x-warp-os-category": OS_CATEGORY,
            "x-warp-os-name": OS_NAME,
            "x-warp-os-version": OS_VERSION,
            "authorization": f"Bearer {jwt}",
            "x-warp-experiment-id": _experiment_id,
            "x-warp-experiment-bucket": _experiment_bucket,
            "accept": "*/*",
            "accept-encoding": "gzip,br",
            "content-length": "0"
        }
        
        # Check if SSL verification should be disabled
        verify_opt = True
        insecure_env = os.getenv("WARP_INSECURE_TLS", "").lower()
        if insecure_env in ("1", "true", "yes"):
            verify_opt = False
            logger.warning("TLS verification disabled via WARP_INSECURE_TLS for login")
        
        # Make login request
        async with httpx.AsyncClient(http2=True, timeout=httpx.Timeout(30.0), verify=verify_opt, trust_env=True) as client:
            response = await client.post(login_url, headers=headers)
            
            if response.status_code == 204:
                # Extract cookies from response
                _session_cookies = dict(response.cookies)
                logger.info(f"✅ Client login successful")
                logger.info(f"Session cookies: {list(_session_cookies.keys())}")
                logger.info(f"Experiment ID: {_experiment_id}")
                
                # Fetch and display additional info if requested
                if show_info:
                    await _fetch_and_display_info()
                
                return True
            else:
                logger.error(f"❌ Client login failed: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Client login exception: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def _fetch_and_display_info():
    """Fetch and display model choices and usage information"""
    try:
        from .graphql_client import (
            get_feature_model_choices,
            get_request_limit_info,
            format_model_choices,
            format_request_limit_info
        )
        
        # Fetch usage info first (more important)
        logger.info("Fetching usage information...")
        usage_data = await get_request_limit_info()
        if usage_data:
            usage_info = format_request_limit_info(usage_data)
            logger.info(usage_info)
        else:
            logger.warning("Failed to fetch usage information")
        
        # Fetch model choices
        logger.info("Fetching available models...")
        model_data = await get_feature_model_choices()
        if model_data:
            model_info = format_model_choices(model_data)
            logger.info(model_info)
        else:
            logger.warning("Failed to fetch model choices")
            
    except Exception as e:
        logger.warning(f"Failed to fetch additional info: {e}")


def get_session_cookies() -> Optional[dict]:
    """Get current session cookies"""
    return _session_cookies


def get_experiment_headers() -> dict:
    """Get experiment headers for API requests"""
    if _experiment_id and _experiment_bucket:
        return {
            "x-warp-experiment-id": _experiment_id,
            "x-warp-experiment-bucket": _experiment_bucket
        }
    return {}


def is_logged_in() -> bool:
    """Check if client is logged in"""
    return _session_cookies is not None


async def ensure_logged_in() -> bool:
    """Ensure client is logged in, perform login if necessary"""
    if is_logged_in():
        logger.debug("Client already logged in")
        return True
    
    logger.info("Client not logged in, performing login...")
    return await client_login()
