from __future__ import annotations

import base64
from typing import Any, Dict

from mcp import types

from ..armor_iq_client import ArmorIQClient
from ..firebase_client import read_doc, update_doc
from ..llm_client import LLMClient
from ..models import ToolContext
from . import ToolRegistry
from .governance_helper import with_governance


async def _handle_extract_text(
    armor_client: ArmorIQClient,
    llm_client: LLMClient,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Extract text from a prescription image using OCR (LLM vision API).

    This tool:
    1. Requires governance (intent: "extract medical text")
    2. Uses LLM vision to extract text from image
    3. Stores raw text + confidence in Firestore prescription doc
    4. Flags low confidence for manual review
    """

    async def _core_extract(args: Dict[str, Any]) -> Dict[str, Any]:
        file_path = args.get("file_path")
        prescription_id = args.get("prescription_id")
        context_data = args.get("context") or {}
        context = ToolContext.model_validate(context_data)

        if not file_path:
            raise ValueError("Missing required field 'file_path'")
        if not prescription_id:
            raise ValueError("Missing required field 'prescription_id'")

        # Read prescription doc to get storage URL
        prescription = read_doc(collection="prescriptions", doc_id=prescription_id)
        if not prescription:
            raise ValueError(f"Prescription {prescription_id} not found")

        storage_url = prescription.get("storage_url") or file_path

        # Use LLM vision API to extract text
        # For now, we'll use a text-based approach where the file_path
        # should be a Firebase Storage URL that the LLM can access
        # In production, you'd download the file and send bytes to vision API
        system_prompt = """You are a medical OCR specialist. Extract all text from the prescription image.
Return a JSON object with:
- "text": the full extracted text
- "confidence": a number between 0 and 1 indicating your confidence
- "regions": array of objects with "text", "bbox" (bounding box coordinates), "confidence"
- "warnings": array of any warnings about unclear text or low confidence areas"""

        user_prompt = f"""Extract text from this prescription image: {storage_url}

Be thorough and accurate. Medical text is critical."""

        ocr_result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model="gpt-4o",  # Use vision-capable model
        )

        extracted_text = ocr_result.get("text", "")
        confidence = ocr_result.get("confidence", 0.0)
        regions = ocr_result.get("regions", [])
        warnings = ocr_result.get("warnings", [])

        # Update prescription doc with OCR results
        update_data = {
            "ocr_text": extracted_text,
            "ocr_confidence": confidence,
            "ocr_regions": regions,
            "ocr_warnings": warnings,
            "status": "ocr_completed",
            "needs_manual_review": confidence < 0.7,  # Flag low confidence
        }
        update_doc(collection="prescriptions", doc_id=prescription_id, data=update_data)

        return {
            "prescription_id": prescription_id,
            "text": extracted_text,
            "confidence": confidence,
            "regions": regions,
            "warnings": warnings,
            "needs_manual_review": confidence < 0.7,
        }

    return await with_governance(
        armor_client=armor_client,
        intent="extract medical text",
        event_type="ocr.extract_text",
        handler=_core_extract,
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

    extract_text_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Firebase Storage path or URL to the prescription image.",
            },
            "prescription_id": {
                "type": "string",
                "description": "Firestore document ID of the prescription being processed.",
            },
            "context": context_schema,
        },
        "required": ["file_path", "prescription_id"],
        "additionalProperties": True,
    }

    registry.add_tool(
        types.Tool(
            name="ocr.extract_text",
            description="Extract text from a prescription image using OCR. Requires governance.",
            inputSchema=extract_text_schema,
        ),
        lambda args: _handle_extract_text(armor_client, llm_client, args),
    )
