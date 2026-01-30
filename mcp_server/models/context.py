from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ToolContext(BaseModel):
    """
    Standard context object passed to tools for auditing and governance.

    This is intended to be included as a field in tool input schemas.
    """

    user_id: Optional[str] = Field(
        default=None,
        description="End-user identifier (e.g. Firebase UID).",
    )
    prescription_id: Optional[str] = Field(
        default=None,
        description="Current prescription document ID, if applicable.",
    )
    agent_name: Optional[str] = Field(
        default=None,
        description="Logical agent name invoking the tool (e.g. 'Intake Agent').",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Correlation ID for tracing across tools and audits.",
    )

