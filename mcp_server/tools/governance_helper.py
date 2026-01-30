from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from ..armor_iq_client import ArmorIQClient
from ..models import ToolContext


async def with_governance(
    armor_client: ArmorIQClient,
    intent: str,
    event_type: str,
    handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Wrapper that enforces governance for sensitive tool operations.

    This helper:
    1. Extracts ToolContext from arguments
    2. Calls policy.check_intent via ArmorIQ
    3. Aborts if denied
    4. Executes the handler
    5. Logs audit event

    Tools that need governance should call this helper.
    """
    context_data = arguments.get("context") or {}
    context = ToolContext.model_validate(context_data)

    # Step 1: Check intent
    intent_result = await armor_client.check_intent(
        intent=intent,
        user_id=context.user_id,
        context=context.model_dump(),
    )

    # ArmorIQ returns a structure like {"allowed": bool, "reason": str, ...}
    # Adapt this to your actual API response shape.
    if not intent_result.get("allowed", False):
        reason = intent_result.get("reason", "Intent denied by ArmorIQ")
        raise PermissionError(f"Intent '{intent}' denied: {reason}")

    # Step 2: Execute handler
    try:
        result = await handler(arguments)
    except Exception as e:
        # Log failure
        await armor_client.log_audit(
            event_type=f"{event_type}.failed",
            user_id=context.user_id,
            payload={
                "context": context.model_dump(),
                "error": str(e),
                "arguments": arguments,
            },
        )
        raise

    # Step 3: Log success
    await armor_client.log_audit(
        event_type=event_type,
        user_id=context.user_id,
        payload={
            "context": context.model_dump(),
            "result": result,
            "arguments": arguments,
        },
    )

    return result
