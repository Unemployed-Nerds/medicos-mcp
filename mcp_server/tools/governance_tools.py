from __future__ import annotations

import json
from typing import Any, Dict

from mcp import types

from ..armor_iq_client import ArmorIQClient
from ..models import ToolContext
from . import ToolRegistry


def governance_tools(armor_client: ArmorIQClient) -> Dict[str, Any]:
    """
    Factory to produce handlers with a bound ArmorIQ client.
    """

    async def policy_check_intent(arguments: Dict[str, Any]) -> Dict[str, Any]:
        intent = arguments.get("intent")
        if not intent:
            raise ValueError("Missing required field 'intent'")

        context_data = arguments.get("context") or {}
        context = ToolContext.model_validate(context_data)

        result = await armor_client.check_intent(
            intent=intent,
            user_id=context.user_id,
            context=context.model_dump(),
        )
        return {"result": result}

    async def audit_log(arguments: Dict[str, Any]) -> Dict[str, Any]:
        event_type = arguments.get("event_type")
        if not event_type:
            raise ValueError("Missing required field 'event_type'")

        payload = arguments.get("payload") or {}
        context_data = arguments.get("context") or {}
        context = ToolContext.model_validate(context_data)

        await armor_client.log_audit(
            event_type=event_type,
            user_id=context.user_id,
            payload={
                "context": context.model_dump(),
                "payload": payload,
            },
        )
        # Optionally, the audit entry could also be written to Firestore by a higher-level tool.
        return {"status": "ok"}

    # JSON Schemas for tool inputs
    policy_input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "description": "High-level intent description."},
            "context": {
                "type": "object",
                "description": "Standard ToolContext object.",
            },
        },
        "required": ["intent"],
        "additionalProperties": True,
    }

    audit_input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "event_type": {"type": "string", "description": "Audit event type."},
            "payload": {
                "type": "object",
                "description": "Arbitrary structured event payload to log.",
            },
            "context": {
                "type": "object",
                "description": "Standard ToolContext object.",
            },
        },
        "required": ["event_type"],
        "additionalProperties": True,
    }

    return {
        "policy.check_intent": {
            "schema": policy_input_schema,
            "handler": policy_check_intent,
            "description": "Validate a sensitive intent with ArmorIQ before executing it.",
        },
        "audit.log": {
            "schema": audit_input_schema,
            "handler": audit_log,
            "description": "Log an audited event to ArmorIQ (and optionally other sinks).",
        },
    }


def register_tools(registry: ToolRegistry, armor_client: ArmorIQClient) -> None:
    tool_defs = governance_tools(armor_client)
    for name, meta in tool_defs.items():
        registry.add_tool(
            types.Tool(
                name=name,
                description=meta["description"],
                inputSchema=meta["schema"],
            ),
            meta["handler"],
        )

