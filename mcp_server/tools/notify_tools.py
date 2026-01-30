from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp import types

import firebase_admin
from firebase_admin import messaging

from ..armor_iq_client import ArmorIQClient
from ..firebase_client import get_firestore_client, read_doc
from ..models import ToolContext
from . import ToolRegistry
from .governance_helper import with_governance


def _get_fcm_client():
    """Get FCM client (uses Firebase Admin messaging)."""
    try:
        app = firebase_admin.get_app()
    except ValueError:
        raise RuntimeError("Firebase not initialized. Call init_firebase() first.")
    return messaging


async def _handle_send(
    armor_client: ArmorIQClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Send a notification via Firebase Cloud Messaging (FCM).

    Supports sending to specific device tokens, user topic, or all user devices.
    """

    async def _core_send(args: Dict[str, Any]) -> Dict[str, Any]:
        user_id = args.get("user_id")
        device_tokens = args.get("device_tokens")  # List of FCM tokens
        topic = args.get("topic")  # FCM topic (e.g., "user_123")
        title = args.get("title", "Medication Reminder")
        body = args.get("body", "")
        data = args.get("data") or {}  # Custom data payload
        notification_type = args.get("notification_type", "reminder")
        context_data = args.get("context") or {}
        context = ToolContext.model_validate(context_data)

        if not user_id:
            raise ValueError("Missing required field 'user_id'")
        if not device_tokens and not topic:
            # Try to get device tokens from user profile
            db = get_firestore_client()
            user_doc = db.collection("users").document(user_id).get()
            if user_doc.exists:
                user_data = user_doc.to_dict() or {}
                device_tokens = user_data.get("fcm_tokens", [])
            if not device_tokens:
                raise ValueError("Must provide either 'device_tokens' or 'topic'")

        fcm = _get_fcm_client()

        # Build FCM message
        message_data = {
            "type": notification_type,
            "user_id": user_id,
            **data,
        }

        # Create notification payload
        notification = messaging.Notification(
            title=title,
            body=body,
        )

        success_count = 0
        failed_tokens = []

        if device_tokens:
            # Send to specific tokens
            if isinstance(device_tokens, str):
                device_tokens = [device_tokens]

            # FCM supports batch sending
            for token in device_tokens:
                try:
                    message = messaging.Message(
                        notification=notification,
                        data={k: str(v) for k, v in message_data.items()},
                        token=token,
                    )
                    fcm.send(message)
                    success_count += 1
                except Exception as e:
                    failed_tokens.append({"token": token, "error": str(e)})
        elif topic:
            # Send to topic
            message = messaging.Message(
                notification=notification,
                data={k: str(v) for k, v in message_data.items()},
                topic=topic,
            )
            try:
                fcm.send(message)
                success_count = 1
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "topic": topic,
                }

        return {
            "success": success_count > 0,
            "success_count": success_count,
            "failed_tokens": failed_tokens,
            "notification_type": notification_type,
        }

    return await with_governance(
        armor_client=armor_client,
        intent="send medical reminder",
        event_type="notify.send",
        handler=_core_send,
        arguments=arguments,
    )


def register_tools(
    registry: ToolRegistry,
    armor_client: ArmorIQClient,
) -> None:
    context_schema: Dict[str, Any] = {
        "type": "object",
        "description": "Standard ToolContext fields for auditing.",
    }

    send_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "device_tokens": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of FCM device tokens to send to.",
            },
            "topic": {
                "type": "string",
                "description": "FCM topic to send to (alternative to device_tokens).",
            },
            "title": {
                "type": "string",
                "description": "Notification title (default: 'Medication Reminder').",
            },
            "body": {"type": "string", "description": "Notification body text."},
            "data": {
                "type": "object",
                "description": "Custom data payload to include.",
            },
            "notification_type": {
                "type": "string",
                "description": "Type of notification (e.g., 'reminder', 'adjustment', 'alert').",
            },
            "context": context_schema,
        },
        "required": ["user_id"],
        "additionalProperties": True,
    }

    registry.add_tool(
        types.Tool(
            name="notify.send",
            description="Send a notification via Firebase Cloud Messaging. Requires governance.",
            inputSchema=send_schema,
        ),
        lambda args: _handle_send(armor_client, args),
    )
