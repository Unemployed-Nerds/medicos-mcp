from __future__ import annotations

from datetime import datetime, time
from typing import Any, Dict, List

from mcp import types

from ..armor_iq_client import ArmorIQClient
from ..firebase_client import read_doc, write_doc, update_doc
from ..llm_client import LLMClient
from ..models import ToolContext
from . import ToolRegistry
from .governance_helper import with_governance


async def _handle_generate(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate a medication schedule from validated medicines.

    Creates daily/weekly schedule considering wake/sleep times and constraints.
    Outputs schedule events: {medicine_id, time, dose, instructions, window}
    """

    async def _core_generate(args: Dict[str, Any]) -> Dict[str, Any]:
        prescription_id = args.get("prescription_id")
        user_id = args.get("user_id")
        wake_time = args.get("wake_time", "08:00")  # Default 8 AM
        sleep_time = args.get("sleep_time", "22:00")  # Default 10 PM
        context_data = args.get("context") or {}
        context = ToolContext.model_validate(context_data)

        if not prescription_id:
            raise ValueError("Missing required field 'prescription_id'")
        if not user_id:
            raise ValueError("Missing required field 'user_id'")

        # Read prescription to get validated medicines
        prescription = read_doc(collection="prescriptions", doc_id=prescription_id)
        if not prescription:
            raise ValueError(f"Prescription {prescription_id} not found")

        medicines = prescription.get("parsed_medicines", [])
        validation_status = prescription.get("validation_status")

        if not medicines:
            raise ValueError("No medicines found. Run rx.parse_text first.")
        if validation_status != "validated":
            raise ValueError(
                f"Prescription not validated (status: {validation_status}). "
                "Run rx.validate and get user confirmation first."
            )

        # Use LLM to generate schedule
        system_prompt = """You are a medication scheduling expert. Create an optimal daily schedule.
Consider:
- Spacing doses appropriately (e.g., every 8 hours for TID)
- Avoiding too many doses at once
- Respecting wake/sleep times
- Meal timing (AC/PC instructions)
- Bedtime medications (HS)

Return JSON with:
- "schedule": array of schedule events, each with:
  - "medicine_name": name of medicine
  - "time": time in HH:MM format (24-hour)
  - "dose": dosage to take
  - "instructions": any special instructions
  - "window_minutes": acceptable window (±minutes from scheduled time)
  - "meal_relation": "before", "after", "with", or null
- "warnings": any scheduling warnings"""

        medicines_json = str(medicines)
        user_prompt = f"""Create a schedule for these medicines:

{medicines_json}

Wake time: {wake_time}
Sleep time: {sleep_time}

Create an optimal daily schedule."""

        schedule_result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        schedule_events = schedule_result.get("schedule", [])
        warnings = schedule_result.get("warnings", [])

        # Create medicine documents in Firestore
        medicine_ids = []
        for med in medicines:
            med_doc = {
                "user_id": user_id,
                "prescription_id": prescription_id,
                "name": med.get("name"),
                "strength": med.get("strength"),
                "route": med.get("route"),
                "frequency": med.get("frequency"),
                "duration": med.get("duration"),
                "instructions": med.get("instructions"),
                "status": "active",
                "created_at": datetime.utcnow().isoformat(),
            }
            med_id = write_doc(collection="medicines", doc_id=None, data=med_doc)
            medicine_ids.append(med_id)

        # Create schedule document
        schedule_doc = {
            "user_id": user_id,
            "prescription_id": prescription_id,
            "schedule_events": schedule_events,
            "wake_time": wake_time,
            "sleep_time": sleep_time,
            "warnings": warnings,
            "status": "active",
            "created_at": datetime.utcnow().isoformat(),
        }
        schedule_id = write_doc(collection="schedules", doc_id=None, data=schedule_doc)

        # Update prescription status
        update_doc(
            collection="prescriptions",
            doc_id=prescription_id,
            data={"status": "scheduled", "schedule_id": schedule_id},
        )

        return {
            "prescription_id": prescription_id,
            "schedule_id": schedule_id,
            "medicine_ids": medicine_ids,
            "schedule_events": schedule_events,
            "warnings": warnings,
        }

    return await with_governance(
        armor_client=armor_client,
        intent="create medication schedule",
        event_type="schedule.generate",
        handler=_core_generate,
        arguments=arguments,
    )


async def _handle_adjust(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Adjust reminder timing only (HARD RULES: never change dosage, never add/remove medicines).

    Only shifts timing based on adherence patterns and snooze events.
    Must notify user of changes.
    """

    async def _core_adjust(args: Dict[str, Any]) -> Dict[str, Any]:
        schedule_id = args.get("schedule_id")
        adjustment_reason = args.get("adjustment_reason", "user_request")
        context_data = args.get("context") or {}
        context = ToolContext.model_validate(context_data)

        if not schedule_id:
            raise ValueError("Missing required field 'schedule_id'")

        # Read schedule
        schedule = read_doc(collection="schedules", doc_id=schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        original_events = schedule.get("schedule_events", [])

        # HARD SAFETY RULE: Verify we're only adjusting timing, not dosage/medicines
        # This is enforced by the LLM prompt and server-side validation

        # Use LLM to suggest timing adjustments
        system_prompt = """You are a medication schedule adjuster. You can ONLY adjust timing.
CRITICAL RULES:
- NEVER change dosage amounts
- NEVER add or remove medicines
- ONLY shift times (e.g., move 8:00 AM to 8:30 AM)
- Keep the same number of doses per day
- Respect wake/sleep windows

Return JSON with:
- "adjusted_events": array of adjusted schedule events (same structure as input)
- "changes": array describing what changed, each with:
  - "event_index": index in original array
  - "old_time": original time
  - "new_time": new time
  - "reason": why it changed
- "requires_user_confirmation": boolean (true if significant changes)"""

        events_json = str(original_events)
        user_prompt = f"""Adjust timing for this schedule:

{events_json}

Adjustment reason: {adjustment_reason}

ONLY adjust times. Do not change dosages or medicines."""

        adjust_result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        adjusted_events = adjust_result.get("adjusted_events", [])
        changes = adjust_result.get("changes", [])
        requires_confirmation = adjust_result.get("requires_user_confirmation", False)

        # Server-side validation: ensure no dosage/medicine changes
        if len(adjusted_events) != len(original_events):
            raise ValueError("Cannot add or remove medicines from schedule")

        for i, (orig, adj) in enumerate(zip(original_events, adjusted_events)):
            if orig.get("medicine_name") != adj.get("medicine_name"):
                raise ValueError(f"Cannot change medicine at index {i}")
            if orig.get("dose") != adj.get("dose"):
                raise ValueError(f"Cannot change dosage at index {i}")

        # Update schedule
        update_data = {
            "schedule_events": adjusted_events,
            "last_adjusted_at": datetime.utcnow().isoformat(),
            "adjustment_reason": adjustment_reason,
            "adjustment_changes": changes,
            "requires_user_confirmation": requires_confirmation,
        }
        update_doc(collection="schedules", doc_id=schedule_id, data=update_data)

        return {
            "schedule_id": schedule_id,
            "adjusted_events": adjusted_events,
            "changes": changes,
            "requires_user_confirmation": requires_confirmation,
        }

    return await with_governance(
        armor_client=armor_client,
        intent="adjust reminder timing",
        event_type="schedule.adjust",
        handler=_core_adjust,
        arguments=arguments,
    )


def register_tools(
    registry: ToolRegistry,
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
) -> None:
    context_schema: Dict[str, Any] = {
        "type": "object",
        "description": "Standard ToolContext fields for auditing.",
    }

    generate_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "prescription_id": {"type": "string"},
            "user_id": {"type": "string"},
            "wake_time": {
                "type": "string",
                "description": "Wake time in HH:MM format (default: 08:00).",
            },
            "sleep_time": {
                "type": "string",
                "description": "Sleep time in HH:MM format (default: 22:00).",
            },
            "context": context_schema,
        },
        "required": ["prescription_id", "user_id"],
        "additionalProperties": True,
    }

    adjust_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "schedule_id": {"type": "string"},
            "adjustment_reason": {
                "type": "string",
                "description": "Reason for adjustment (e.g., 'snooze_pattern', 'adherence_optimization').",
            },
            "context": context_schema,
        },
        "required": ["schedule_id"],
        "additionalProperties": True,
    }

    registry.add_tool(
        types.Tool(
            name="schedule.generate",
            description="Generate a medication schedule from validated medicines. Requires governance.",
            inputSchema=generate_schema,
        ),
        lambda args: _handle_generate(armor_client, llm_client, args),
    )

    registry.add_tool(
        types.Tool(
            name="schedule.adjust",
            description="Adjust reminder timing only (never changes dosage or medicines). Requires governance.",
            inputSchema=adjust_schema,
        ),
        lambda args: _handle_adjust(armor_client, llm_client, args),
    )
