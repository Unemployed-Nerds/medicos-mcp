from __future__ import annotations

from typing import Any, Dict, Optional
import asyncio

try:
    from armoriq_sdk import ArmorIQClient as SDKClient
    from armoriq_sdk.models import PlanCapture, IntentToken
    from armoriq_sdk.exceptions import (
        InvalidTokenException,
        IntentMismatchException,
        ConfigurationException,
    )
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    SDKClient = None
    PlanCapture = None
    IntentToken = None

from .config import Settings


class ArmorIQClient:
    """
    Wrapper around the official ArmorIQ SDK.

    Provides simplified async interfaces for:
    - check_intent: Validates intent by getting an intent token via SDK
    - log_audit: Logs audit events (writes to Firestore audit_logs collection)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        
        if not SDK_AVAILABLE:
            raise ImportError(
                "ArmorIQ SDK not found. "
                "Install it with: pip install armoriq-sdk"
            )
        
        # Default user_id and agent_id (can be overridden per call)
        self._default_user_id = "medicos-user"
        self._default_agent_id = "medicos-mcp-backend"
        
        # Initialize the official SDK client
        # Note: SDK is synchronous, so we'll wrap calls in async
        self._sdk_client = SDKClient(
            api_key=settings.armoriq_api_key,
            user_id=self._default_user_id,
            agent_id=self._default_agent_id,
            use_production=settings.env == "prod",
        )
        
        # Cache for intent tokens (intent -> token)
        self._token_cache: Dict[str, IntentToken] = {}

    async def check_intent(
        self,
        intent: str,
        user_id: Optional[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Validate an intent by creating a plan and getting an intent token.
        
        This wraps the SDK's capture_plan() + get_intent_token() flow.
        If token issuance succeeds, intent is allowed.
        """
        # Run SDK calls in thread pool since SDK is synchronous
        loop = asyncio.get_event_loop()
        
        try:
            # Create a simple plan from the intent string
            plan_structure = {
                "goal": intent,
                "steps": [
                    {
                        "action": intent,
                        "mcp": "medicos-mcp",
                        "description": intent,
                    }
                ],
            }
            
            # Capture the plan (sync call in thread pool)
            plan_capture = await loop.run_in_executor(
                None,
                lambda: self._sdk_client.capture_plan(
                    llm="gpt-4",
                    prompt=intent,
                    plan=plan_structure,
                ),
            )
            
            # Try to get intent token (this validates the intent)
            token = await loop.run_in_executor(
                None,
                lambda: self._sdk_client.get_intent_token(
                    plan_capture=plan_capture,
                    validity_seconds=60.0,  # Short validity for intent checks
                ),
            )
            
            # Cache the token
            self._token_cache[intent] = token
            
            # Return success result
            return {
                "allowed": True,
                "reason": "Intent validated successfully",
                "token_id": token.token_id,
                "plan_hash": token.plan_hash,
            }
            
        except (InvalidTokenException, ConfigurationException) as e:
            # Token issuance failed = intent denied
            return {
                "allowed": False,
                "reason": f"Intent validation failed: {str(e)}",
                "error": str(e),
            }
        except Exception as e:
            # Other errors
            return {
                "allowed": False,
                "reason": f"Intent validation error: {str(e)}",
                "error": str(e),
            }

    async def log_audit(
        self,
        event_type: str,
        user_id: Optional[str],
        payload: Dict[str, Any],
    ) -> None:
        """
        Log an audit event to Firestore.
        
        The SDK doesn't have a direct audit.log method, so we write to Firestore
        audit_logs collection directly. This ensures all audit events are persisted.
        """
        from .firebase_client import write_doc
        from datetime import datetime
        
        # Write audit log to Firestore
        audit_entry = {
            "event_type": event_type,
            "user_id": user_id,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
            "source": "armoriq-governance",
        }
        
        # Write to audit_logs collection
        write_doc(
            collection="audit_logs",
            doc_id=None,  # Auto-generate ID
            data=audit_entry,
        )

    def close(self) -> None:
        """Close the SDK client."""
        if self._sdk_client:
            self._sdk_client.close()

    async def aclose(self) -> None:
        """Async close (delegates to sync close)."""
        self.close()

