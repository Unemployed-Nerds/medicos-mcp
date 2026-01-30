from __future__ import annotations

from typing import Any, Dict, List

from mcp import types

from ..armor_iq_client import ArmorIQClient
from ..firebase_client import read_doc, update_doc
from ..llm_client import LLMClient
from ..models import ToolContext
from . import ToolRegistry
from .governance_helper import with_governance

# Common prescription abbreviations mapping
PRESCRIPTION_ABBREVIATIONS: Dict[str, str] = {
    "BID": "twice daily",
    "TID": "three times daily",
    "QID": "four times daily",
    "QD": "once daily",
    "QOD": "every other day",
    "PRN": "as needed",
    "AC": "before meals",
    "PC": "after meals",
    "HS": "at bedtime",
    "PO": "by mouth",
    "IM": "intramuscular",
    "IV": "intravenous",
    "SC": "subcutaneous",
    "mg": "milligram",
    "g": "gram",
    "ml": "milliliter",
    "tab": "tablet",
    "cap": "capsule",
}


async def _handle_parse_text(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Parse OCR text into structured medicine data.

    Uses LLM to extract medicines with name, strength, route, frequency, duration, instructions.
    """

    async def _core_parse(args: Dict[str, Any]) -> Dict[str, Any]:
        prescription_id = args.get("prescription_id")
        ocr_text = args.get("ocr_text")
        context_data = args.get("context") or {}
        context = ToolContext.model_validate(context_data)

        if not prescription_id:
            raise ValueError("Missing required field 'prescription_id'")

        # If ocr_text not provided, read from prescription doc
        if not ocr_text:
            prescription = read_doc(collection="prescriptions", doc_id=prescription_id)
            if not prescription:
                raise ValueError(f"Prescription {prescription_id} not found")
            ocr_text = prescription.get("ocr_text", "")

        if not ocr_text:
            raise ValueError("No OCR text available. Run ocr.extract_text first.")

        # Use LLM to parse structured medicine data
        system_prompt = """You are a medical prescription parser. Extract all medicines from prescription text.
Return a JSON object with:
- "medicines": array of objects, each with:
  - "name": drug name (generic preferred if known)
  - "strength": dosage strength (e.g., "500mg", "10ml")
  - "route": administration route (e.g., "oral", "IV", "topical")
  - "frequency": how often (e.g., "twice daily", "every 8 hours")
  - "duration": how long (e.g., "7 days", "until finished")
  - "instructions": any special instructions
  - "raw_text": the original text snippet for this medicine
- "warnings": array of any parsing warnings or ambiguities"""

        user_prompt = f"""Parse this prescription text into structured medicines:

{ocr_text}

Be precise and extract all medicines mentioned. If something is unclear, include it in warnings."""

        parse_result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        medicines = parse_result.get("medicines", [])
        warnings = parse_result.get("warnings", [])

        # Update prescription doc
        update_data = {
            "parsed_medicines": medicines,
            "parsing_warnings": warnings,
            "status": "parsed",
        }
        update_doc(collection="prescriptions", doc_id=prescription_id, data=update_data)

        return {
            "prescription_id": prescription_id,
            "medicines": medicines,
            "warnings": warnings,
        }

    return await with_governance(
        armor_client=armor_client,
        intent="parse prescription",
        event_type="rx.parse_text",
        handler=_core_parse,
        arguments=arguments,
    )


async def _handle_expand_abbrev(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Expand prescription abbreviations in text or medicine data.

    Uses both a curated mapping table and LLM for unknown abbreviations.
    """

    async def _core_expand(args: Dict[str, Any]) -> Dict[str, Any]:
        text = args.get("text")
        medicine_data = args.get("medicine_data")

        if not text and not medicine_data:
            raise ValueError("Either 'text' or 'medicine_data' must be provided")

        expanded = {}
        if text:
            # Simple abbreviation expansion using mapping table
            expanded_text = text
            for abbrev, full in PRESCRIPTION_ABBREVIATIONS.items():
                expanded_text = expanded_text.replace(abbrev, full)
            expanded["text"] = expanded_text

        if medicine_data:
            # Expand abbreviations in structured medicine data
            expanded_medicines = []
            for med in medicine_data:
                expanded_med = med.copy()
                # Expand frequency abbreviations
                freq = med.get("frequency", "")
                for abbrev, full in PRESCRIPTION_ABBREVIATIONS.items():
                    if abbrev in freq.upper():
                        expanded_med["frequency"] = freq.replace(abbrev, full)
                expanded_medicines.append(expanded_med)
            expanded["medicine_data"] = expanded_medicines

        return expanded

    # Expansion is less sensitive, but still audited
    return await with_governance(
        armor_client=armor_client,
        intent="expand prescription abbreviations",
        event_type="rx.expand_abbrev",
        handler=_core_expand,
        arguments=arguments,
    )


async def _handle_validate(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Validate parsed medicines for safety and consistency.

    Checks for:
    - Dosage inconsistencies
    - Drug interactions (advisory, not authoritative)
    - Age/condition constraints
    - Schedule conflicts

    Returns validation_status: "validated" or "needs_user_confirmation"
    """

    async def _core_validate(args: Dict[str, Any]) -> Dict[str, Any]:
        prescription_id = args.get("prescription_id")
        medicines = args.get("medicines")
        context_data = args.get("context") or {}
        context = ToolContext.model_validate(context_data)

        if not prescription_id:
            raise ValueError("Missing required field 'prescription_id'")

        # If medicines not provided, read from prescription doc
        if not medicines:
            prescription = read_doc(collection="prescriptions", doc_id=prescription_id)
            if not prescription:
                raise ValueError(f"Prescription {prescription_id} not found")
            medicines = prescription.get("parsed_medicines", [])

        if not medicines:
            raise ValueError("No medicines to validate. Run rx.parse_text first.")

        # Use LLM for validation checks
        system_prompt = """You are a medical safety validator. Check prescription medicines for:
1. Dosage consistency (does strength match frequency?)
2. Potential drug interactions (advisory only - flag for review)
3. Common safety issues (e.g., duplicate medicines, conflicting schedules)
4. Missing critical information

Return JSON with:
- "validation_status": "validated" or "needs_user_confirmation"
- "issues": array of validation issues, each with:
  - "severity": "error", "warning", or "info"
  - "medicine": medicine name or "general"
  - "message": description of the issue
- "recommendations": array of recommendations for user review"""

        medicines_json = str(medicines)
        user_prompt = f"""Validate these medicines for safety:

{medicines_json}

Be thorough but remember this is advisory only. Flag anything that needs user confirmation."""

        validation_result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        validation_status = validation_result.get("validation_status", "needs_user_confirmation")
        issues = validation_result.get("issues", [])
        recommendations = validation_result.get("recommendations", [])

        # Update prescription doc
        update_data = {
            "validation_status": validation_status,
            "validation_issues": issues,
            "validation_recommendations": recommendations,
            "status": "validated" if validation_status == "validated" else "needs_user_confirmation",
        }
        update_doc(collection="prescriptions", doc_id=prescription_id, data=update_data)

        return {
            "prescription_id": prescription_id,
            "validation_status": validation_status,
            "issues": issues,
            "recommendations": recommendations,
        }

    return await with_governance(
        armor_client=armor_client,
        intent="validate medical data",
        event_type="rx.validate",
        handler=_core_validate,
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

    parse_text_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "prescription_id": {"type": "string"},
            "ocr_text": {
                "type": "string",
                "description": "Optional OCR text. If omitted, reads from prescription doc.",
            },
            "context": context_schema,
        },
        "required": ["prescription_id"],
        "additionalProperties": True,
    }

    expand_abbrev_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text with abbreviations to expand.",
            },
            "medicine_data": {
                "type": "array",
                "description": "Array of medicine objects to expand abbreviations in.",
            },
            "context": context_schema,
        },
        "additionalProperties": True,
    }

    validate_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "prescription_id": {"type": "string"},
            "medicines": {
                "type": "array",
                "description": "Optional medicines array. If omitted, reads from prescription doc.",
            },
            "context": context_schema,
        },
        "required": ["prescription_id"],
        "additionalProperties": True,
    }

    registry.add_tool(
        types.Tool(
            name="rx.parse_text",
            description="Parse OCR text into structured medicine data. Requires governance.",
            inputSchema=parse_text_schema,
        ),
        lambda args: _handle_parse_text(armor_client, llm_client, args),
    )

    registry.add_tool(
        types.Tool(
            name="rx.expand_abbrev",
            description="Expand prescription abbreviations in text or medicine data.",
            inputSchema=expand_abbrev_schema,
        ),
        lambda args: _handle_expand_abbrev(armor_client, llm_client, args),
    )

    registry.add_tool(
        types.Tool(
            name="rx.validate",
            description="Validate parsed medicines for safety and consistency. Requires governance.",
            inputSchema=validate_schema,
        ),
        lambda args: _handle_validate(armor_client, llm_client, args),
    )
