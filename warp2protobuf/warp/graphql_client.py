#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warp GraphQL Client

Handles GraphQL queries to Warp API for model choices and usage information.
"""
import httpx
import os
from typing import Optional, Dict, Any

from ..config.settings import CLIENT_VERSION, OS_CATEGORY, OS_NAME, OS_VERSION
from ..core.logging import logger
from ..core.auth import get_valid_jwt


GRAPHQL_URL = "https://app.warp.dev/graphql/v2"


async def query_graphql(operation_name: str, query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Execute a GraphQL query against Warp API"""
    try:
        jwt = await get_valid_jwt()
        
        headers = {
            "x-warp-client-id": "warp-app",
            "x-warp-client-version": CLIENT_VERSION,
            "x-warp-os-category": OS_CATEGORY,
            "x-warp-os-name": OS_NAME,
            "x-warp-os-version": OS_VERSION,
            "content-type": "application/json",
            "authorization": f"Bearer {jwt}",
            "accept": "*/*",
            "accept-encoding": "gzip,br"
        }
        
        payload = {
            "query": query,
            "variables": variables,
            "operationName": operation_name
        }
        
        verify_opt = True
        insecure_env = os.getenv("WARP_INSECURE_TLS", "").lower()
        if insecure_env in ("1", "true", "yes"):
            verify_opt = False
            logger.warning("TLS verification disabled via WARP_INSECURE_TLS for GraphQL")
        
        async with httpx.AsyncClient(http2=True, timeout=httpx.Timeout(30.0), verify=verify_opt, trust_env=True) as client:
            response = await client.post(
                f"{GRAPHQL_URL}?op={operation_name}",
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"GraphQL query failed: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return None
                
    except Exception as e:
        logger.error(f"GraphQL query exception: {e}")
        return None


async def get_feature_model_choices() -> Optional[Dict[str, Any]]:
    """Get available model choices for different features"""
    query = """query GetFeatureModelChoices($requestContext: RequestContext!) {
  user(requestContext: $requestContext) {
    __typename
    ... on UserOutput {
      user {
        workspaces {
          featureModelChoice {
            agentMode {
              defaultId
              choices {
                displayName
                baseModelName
                id
                reasoningLevel
                usageMetadata {
                  creditMultiplier
                  requestMultiplier
                }
                description
                disableReason
                visionSupported
                spec {
                  cost
                  quality
                  speed
                }
                provider
              }
            }
            planning {
              defaultId
              choices {
                displayName
                baseModelName
                id
                reasoningLevel
                usageMetadata {
                  creditMultiplier
                  requestMultiplier
                }
                description
                disableReason
                visionSupported
                spec {
                  cost
                  quality
                  speed
                }
                provider
              }
            }
            coding {
              defaultId
              choices {
                displayName
                baseModelName
                id
                reasoningLevel
                usageMetadata {
                  creditMultiplier
                  requestMultiplier
                }
                description
                disableReason
                visionSupported
                spec {
                  cost
                  quality
                  speed
                }
                provider
              }
            }
            cliAgent {
              defaultId
              choices {
                displayName
                baseModelName
                id
                reasoningLevel
                usageMetadata {
                  creditMultiplier
                  requestMultiplier
                }
                description
                disableReason
                visionSupported
                spec {
                  cost
                  quality
                  speed
                }
                provider
              }
            }
          }
        }
      }
    }
  }
}
"""
    
    variables = {
        "requestContext": {
            "clientContext": {
                "version": CLIENT_VERSION
            },
            "osContext": {
                "category": OS_CATEGORY,
                "linuxKernelVersion": None,
                "name": OS_NAME,
                "version": OS_VERSION
            }
        }
    }
    
    return await query_graphql("GetFeatureModelChoices", query, variables)


async def get_request_limit_info() -> Optional[Dict[str, Any]]:
    """Get request limit and usage information"""
    query = """query GetRequestLimitInfo($requestContext: RequestContext!) {
  user(requestContext: $requestContext) {
    __typename
    ... on UserOutput {
      user {
        workspaces {
          uid
          bonusGrantsInfo {
            grants {
              createdAt
              costCents
              expiration
              reason
              userFacingMessage
              requestCreditsGranted
              requestCreditsRemaining
            }
            spendingInfo {
              currentMonthCreditsPurchased
              currentMonthPeriodEnd
              currentMonthSpendCents
            }
          }
        }
        requestLimitInfo {
          isUnlimited
          nextRefreshTime
          requestLimit
          requestsUsedSinceLastRefresh
          requestLimitRefreshDuration
          isUnlimitedAutosuggestions
          acceptedAutosuggestionsLimit
          acceptedAutosuggestionsSinceLastRefresh
          isUnlimitedVoice
          voiceRequestLimit
          voiceRequestsUsedSinceLastRefresh
          voiceTokenLimit
          voiceTokensUsedSinceLastRefresh
          isUnlimitedCodebaseIndices
          maxCodebaseIndices
          maxFilesPerRepo
          embeddingGenerationBatchSize
          requestLimitPooling
        }
        bonusGrants {
          createdAt
          costCents
          expiration
          reason
          userFacingMessage
          requestCreditsGranted
          requestCreditsRemaining
        }
      }
    }
    ... on UserFacingError {
      error {
        __typename
        ... on SharedObjectsLimitExceeded {
          limit
          objectType
          message
        }
        ... on PersonalObjectsLimitExceeded {
          limit
          objectType
          message
        }
        ... on AccountDelinquencyError {
          message
        }
        ... on GenericStringObjectUniqueKeyConflict {
          message
        }
        ... on BudgetExceededError {
          message
        }
        ... on PaymentMethodDeclinedError {
          message
        }
        message
      }
      responseContext {
        serverVersion
      }
    }
  }
}
"""
    
    variables = {
        "requestContext": {
            "clientContext": {
                "version": CLIENT_VERSION
            },
            "osContext": {
                "category": OS_CATEGORY,
                "linuxKernelVersion": None,
                "name": OS_NAME,
                "version": OS_VERSION
            }
        }
    }
    
    return await query_graphql("GetRequestLimitInfo", query, variables)


def format_model_choices(data: Dict[str, Any]) -> str:
    """Format model choices data for display"""
    try:
        user_data = data.get("data", {}).get("user", {})
        if user_data.get("__typename") != "UserOutput":
            return "❌ Failed to retrieve model choices"
        
        workspaces = user_data.get("user", {}).get("workspaces", [])
        if not workspaces:
            return "❌ No workspace data available"
        
        feature_model_choice = workspaces[0].get("featureModelChoice", {})
        
        output = ["\n📋 Available Models:\n"]
        
        for feature_name, feature_key in [
            ("Agent Mode", "agentMode"),
            ("Planning", "planning"),
            ("Coding", "coding"),
            ("CLI Agent", "cliAgent")
        ]:
            feature_data = feature_model_choice.get(feature_key, {})
            default_id = feature_data.get("defaultId", "N/A")
            choices = feature_data.get("choices", [])
            
            output.append(f"\n  {feature_name} (default: {default_id}):")
            output.append(f"    Total models: {len(choices)}")
            
            if choices:
                output.append(f"    Top 5 models:")
                for model in choices[:5]:
                    model_id = model.get("id", "unknown")
                    display_name = model.get("displayName", "N/A")
                    provider = model.get("provider", "UNKNOWN")
                    spec = model.get("spec", {})
                    cost = spec.get("cost", 0) if spec else 0
                    quality = spec.get("quality", 0) if spec else 0
                    speed = spec.get("speed", 0) if spec else 0
                    
                    output.append(f"      - {display_name} ({model_id})")
                    output.append(f"        Provider: {provider}, Cost: {cost}, Quality: {quality}, Speed: {speed}")
        
        return "\n".join(output)
        
    except Exception as e:
        logger.error(f"Error formatting model choices: {e}")
        return f"❌ Error formatting model data: {e}"


def format_request_limit_info(data: Dict[str, Any]) -> str:
    """Format request limit info for display"""
    try:
        user_data = data.get("data", {}).get("user", {})
        if user_data.get("__typename") != "UserOutput":
            return "❌ Failed to retrieve usage information"
        
        user_info = user_data.get("user", {})
        limit_info = user_info.get("requestLimitInfo", {})
        
        output = ["\n📊 Usage Information:\n"]
        
        is_unlimited = limit_info.get("isUnlimited", False)
        request_limit = limit_info.get("requestLimit", 0)
        requests_used = limit_info.get("requestsUsedSinceLastRefresh", 0)
        next_refresh = limit_info.get("nextRefreshTime", "N/A")
        refresh_duration = limit_info.get("requestLimitRefreshDuration", "N/A")
        
        if is_unlimited:
            output.append("  ✨ Unlimited requests")
        else:
            remaining = request_limit - requests_used
            usage_percent = (requests_used / request_limit * 100) if request_limit > 0 else 0
            output.append(f"  Request Limit: {request_limit}")
            output.append(f"  Requests Used: {requests_used} ({usage_percent:.1f}%)")
            output.append(f"  Remaining: {remaining}")
        
        output.append(f"  Refresh Period: {refresh_duration}")
        output.append(f"  Next Refresh: {next_refresh}")
        
        voice_limit = limit_info.get("voiceRequestLimit", 0)
        voice_used = limit_info.get("voiceRequestsUsedSinceLastRefresh", 0)
        if voice_limit > 0:
            output.append(f"\n  Voice Requests: {voice_used}/{voice_limit}")
        
        bonus_grants = user_info.get("bonusGrants", [])
        if bonus_grants:
            output.append(f"\n  🎁 Bonus Grants: {len(bonus_grants)}")
            for grant in bonus_grants[:3]:
                credits_remaining = grant.get("requestCreditsRemaining", 0)
                message = grant.get("userFacingMessage", "N/A")
                output.append(f"    - {message}: {credits_remaining} credits remaining")
        
        return "\n".join(output)
        
    except Exception as e:
        logger.error(f"Error formatting request limit info: {e}")
        return f"❌ Error formatting usage data: {e}"
