from __future__ import annotations

from typing import Any, Dict, List

from mcp import types

from ..armor_iq_client import ArmorIQClient
from ..llm_client import LLMClient
from ..models import ToolContext
from . import ToolRegistry
from .governance_helper import with_governance


async def _handle_normalize(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Normalize drug names (brand to generic, common variations).

    Uses LLM to map drug names to normalized forms, preferring generic names.
    """

    async def _core_normalize(args: Dict[str, Any]) -> Dict[str, Any]:
        drug_name = args.get("drug_name")
        drug_names = args.get("drug_names")  # Batch processing

        if not drug_name and not drug_names:
            raise ValueError("Either 'drug_name' or 'drug_names' must be provided")

        names_to_normalize = [drug_name] if drug_name else drug_names

        # Use LLM to normalize drug names
        system_prompt = """You are a drug name normalizer. Convert drug names to their normalized form.
Prefer generic names over brand names. Handle common variations and misspellings.

Return JSON with:
- "normalized": array of objects, each with:
  - "original": original drug name
  - "normalized": normalized name (generic preferred)
  - "type": "generic" or "brand"
  - "confidence": confidence score 0-1
  - "alternatives": array of alternative names if applicable"""

        names_str = ", ".join(names_to_normalize)
        user_prompt = f"""Normalize these drug names:

{names_str}

Return normalized forms, preferring generic names."""

        normalize_result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        normalized = normalize_result.get("normalized", [])

        # If single drug_name provided, return single result
        if drug_name:
            result = normalized[0] if normalized else {"original": drug_name, "normalized": drug_name}
            return result

        return {"normalized": normalized}

    return await with_governance(
        armor_client=armor_client,
        intent="normalize medication",
        event_type="drug.normalize",
        handler=_core_normalize,
        arguments=arguments,
    )


async def _handle_rules(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Check drug-specific rules (age constraints, contraindications, dosage limits).

    This is rule-based validation that can be deterministic where possible,
    with LLM used for explanation and complex cases.
    """

    async def _core_rules(args: Dict[str, Any]) -> Dict[str, Any]:
        drug_name = args.get("drug_name")
        dosage = args.get("dosage")
        patient_age = args.get("patient_age")
        patient_conditions = args.get("patient_conditions") or []

        if not drug_name:
            raise ValueError("Missing required field 'drug_name'")

        # Use LLM to check drug rules
        system_prompt = """You are a drug safety rule checker. Check drug-specific rules:
1. Age restrictions (pediatric vs adult)
2. Contraindications based on conditions
3. Dosage limits (maximum safe doses)
4. Pregnancy/lactation warnings

Return JSON with:
- "allowed": boolean indicating if drug is generally safe
- "warnings": array of warnings, each with:
  - "severity": "error", "warning", or "info"
  - "rule": rule name
  - "message": description
- "recommendations": array of recommendations"""

        context_info = f"Drug: {drug_name}"
        if dosage:
            context_info += f", Dosage: {dosage}"
        if patient_age:
            context_info += f", Patient age: {patient_age}"
        if patient_conditions:
            context_info += f", Conditions: {', '.join(patient_conditions)}"

        user_prompt = f"""Check drug rules for:

{context_info}

Be thorough and flag any safety concerns."""

        rules_result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        return {
            "drug_name": drug_name,
            "allowed": rules_result.get("allowed", True),
            "warnings": rules_result.get("warnings", []),
            "recommendations": rules_result.get("recommendations", []),
        }

    return await with_governance(
        armor_client=armor_client,
        intent="check drug rules",
        event_type="drug.rules",
        handler=_core_rules,
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

    normalize_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "drug_name": {
                "type": "string",
                "description": "Single drug name to normalize.",
            },
            "drug_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Batch of drug names to normalize.",
            },
            "context": context_schema,
        },
        "additionalProperties": True,
    }

    rules_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "drug_name": {"type": "string"},
            "dosage": {"type": "string", "description": "Dosage strength (e.g., '500mg')."},
            "patient_age": {"type": "integer", "description": "Patient age in years."},
            "patient_conditions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of patient conditions/contraindications.",
            },
            "context": context_schema,
        },
        "required": ["drug_name"],
        "additionalProperties": True,
    }

    registry.add_tool(
        types.Tool(
            name="drug.normalize",
            description="Normalize drug names (brand to generic, handle variations).",
            inputSchema=normalize_schema,
        ),
        lambda args: _handle_normalize(armor_client, llm_client, args),
    )

    registry.add_tool(
        types.Tool(
            name="drug.rules",
            description="Check drug-specific safety rules (age, contraindications, dosage limits).",
            inputSchema=rules_schema,
        ),
        lambda args: _handle_rules(armor_client, llm_client, args),
    )
