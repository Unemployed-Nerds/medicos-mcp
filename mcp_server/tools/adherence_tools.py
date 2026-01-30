from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from mcp import types

from ..armor_iq_client import ArmorIQClient
from ..firebase_client import get_firestore_client, read_doc, write_doc, update_doc
from ..llm_client import LLMClient
from ..models import ToolContext
from . import ToolRegistry
from .governance_helper import with_governance


async def _handle_log_action(
    armor_client: ArmorIQClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Log a medication action (Taken, Skipped, Snoozed).

    Writes to med_logs collection and maintains audit trail.
    """

    async def _core_log(args: Dict[str, Any]) -> Dict[str, Any]:
        user_id = args.get("user_id")
        schedule_id = args.get("schedule_id")
        event_index = args.get("event_index")  # Index in schedule_events array
        action = args.get("action")  # "taken", "skipped", "snoozed"
        timestamp = args.get("timestamp")  # ISO format, defaults to now
        notes = args.get("notes", "")
        context_data = args.get("context") or {}
        context = ToolContext.model_validate(context_data)

        if not user_id:
            raise ValueError("Missing required field 'user_id'")
        if not schedule_id:
            raise ValueError("Missing required field 'schedule_id'")
        if not action or action not in ["taken", "skipped", "snoozed"]:
            raise ValueError("Missing or invalid 'action' (must be 'taken', 'skipped', or 'snoozed')")

        # Read schedule to get event details
        schedule = read_doc(collection="schedules", doc_id=schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        schedule_events = schedule.get("schedule_events", [])
        if event_index is None or event_index >= len(schedule_events):
            raise ValueError(f"Invalid event_index: {event_index}")

        event = schedule_events[event_index]
        event_time = event.get("time")

        # Create log entry
        log_entry = {
            "user_id": user_id,
            "schedule_id": schedule_id,
            "event_index": event_index,
            "medicine_name": event.get("medicine_name"),
            "scheduled_time": event_time,
            "action": action,
            "timestamp": timestamp or datetime.utcnow().isoformat(),
            "notes": notes,
            "created_at": datetime.utcnow().isoformat(),
        }

        log_id = write_doc(collection="med_logs", doc_id=None, data=log_entry)

        # If snoozed, we might want to trigger a schedule adjustment later
        # (handled by Adjustment Agent, not here)

        return {
            "log_id": log_id,
            "action": action,
            "timestamp": log_entry["timestamp"],
        }

    return await with_governance(
        armor_client=armor_client,
        intent="log medication action",
        event_type="med.log_action",
        handler=_core_log,
        arguments=arguments,
    )


async def _handle_analyze(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Analyze medication adherence patterns.

    Aggregates med_logs and computes adherence statistics.
    Writes results to adherence_stats collection.
    """

    async def _core_analyze(args: Dict[str, Any]) -> Dict[str, Any]:
        user_id = args.get("user_id")
        schedule_id = args.get("schedule_id")
        days = args.get("days", 7)  # Analyze last N days
        context_data = args.get("context") or {}
        context = ToolContext.model_validate(context_data)

        if not user_id:
            raise ValueError("Missing required field 'user_id'")
        if not schedule_id:
            raise ValueError("Missing required field 'schedule_id'")

        db = get_firestore_client()

        # Read schedule
        schedule = read_doc(collection="schedules", doc_id=schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        schedule_events = schedule.get("schedule_events", [])

        # Query med_logs for this schedule in the last N days
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        logs_query = (
            db.collection("med_logs")
            .where("schedule_id", "==", schedule_id)
            .where("timestamp", ">=", cutoff_date.isoformat())
            .order_by("timestamp")
        )
        logs = [doc.to_dict() for doc in logs_query.stream()]

        # Compute basic adherence metrics
        total_expected = len(schedule_events) * days
        taken_count = sum(1 for log in logs if log.get("action") == "taken")
        skipped_count = sum(1 for log in logs if log.get("action") == "skipped")
        snoozed_count = sum(1 for log in logs if log.get("action") == "snoozed")

        adherence_rate = (taken_count / total_expected * 100) if total_expected > 0 else 0.0

        # Use LLM to analyze patterns and provide insights
        system_prompt = """You are an adherence analysis expert. Analyze medication adherence patterns.
Look for:
- Timing patterns (consistently late/early)
- Missed doses patterns
- Snooze patterns
- Medicine-specific adherence differences

Return JSON with:
- "adherence_rate": percentage (0-100)
- "patterns": array of pattern descriptions
- "recommendations": array of actionable recommendations
- "warnings": any adherence warnings"""

        logs_summary = {
            "total_expected": total_expected,
            "taken": taken_count,
            "skipped": skipped_count,
            "snoozed": snoozed_count,
            "schedule_events": schedule_events,
            "logs": logs[-20:],  # Last 20 logs for context
        }

        user_prompt = f"""Analyze adherence for this schedule:

{logs_summary}

Provide insights and recommendations."""

        analysis_result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        # Create adherence stats document
        stats_doc = {
            "user_id": user_id,
            "schedule_id": schedule_id,
            "period_days": days,
            "period_start": cutoff_date.isoformat(),
            "period_end": datetime.utcnow().isoformat(),
            "total_expected": total_expected,
            "taken_count": taken_count,
            "skipped_count": skipped_count,
            "snoozed_count": snoozed_count,
            "adherence_rate": adherence_rate,
            "patterns": analysis_result.get("patterns", []),
            "recommendations": analysis_result.get("recommendations", []),
            "warnings": analysis_result.get("warnings", []),
            "computed_at": datetime.utcnow().isoformat(),
        }

        stats_id = write_doc(collection="adherence_stats", doc_id=None, data=stats_doc)

        return {
            "stats_id": stats_id,
            "adherence_rate": adherence_rate,
            "taken_count": taken_count,
            "skipped_count": skipped_count,
            "snoozed_count": snoozed_count,
            "patterns": analysis_result.get("patterns", []),
            "recommendations": analysis_result.get("recommendations", []),
        }

    return await with_governance(
        armor_client=armor_client,
        intent="analyze adherence",
        event_type="adherence.analyze",
        handler=_core_analyze,
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

    log_action_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "schedule_id": {"type": "string"},
            "event_index": {
                "type": "integer",
                "description": "Index of the schedule event in schedule_events array.",
            },
            "action": {
                "type": "string",
                "enum": ["taken", "skipped", "snoozed"],
                "description": "Action taken by user.",
            },
            "timestamp": {
                "type": "string",
                "description": "ISO timestamp of action (defaults to now).",
            },
            "notes": {"type": "string", "description": "Optional notes about the action."},
            "context": context_schema,
        },
        "required": ["user_id", "schedule_id", "event_index", "action"],
        "additionalProperties": True,
    }

    analyze_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "schedule_id": {"type": "string"},
            "days": {
                "type": "integer",
                "description": "Number of days to analyze (default: 7).",
            },
            "context": context_schema,
        },
        "required": ["user_id", "schedule_id"],
        "additionalProperties": True,
    }

    registry.add_tool(
        types.Tool(
            name="med.log_action",
            description="Log a medication action (taken, skipped, snoozed). Requires governance.",
            inputSchema=log_action_schema,
        ),
        lambda args: _handle_log_action(armor_client, args),
    )

    registry.add_tool(
        types.Tool(
            name="adherence.analyze",
            description="Analyze medication adherence patterns and generate insights. Requires governance.",
            inputSchema=analyze_schema,
        ),
        lambda args: _handle_analyze(armor_client, llm_client, args),
    )
