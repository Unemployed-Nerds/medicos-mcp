from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp import types

from ..firebase_client import (
    FirestoreFilter,
    query_collection,
    read_doc,
    store_file,
    update_doc,
    write_doc,
)
from ..models import ToolContext
from . import ToolRegistry


async def _handle_store_file(arguments: Dict[str, Any]) -> Dict[str, Any]:
    path = arguments.get("path")
    content = arguments.get("content")
    content_type = arguments.get("content_type") or "application/octet-stream"
    metadata = arguments.get("metadata") or {}

    if not path:
        raise ValueError("Missing required field 'path'")
    if content is None:
        raise ValueError("Missing required field 'content' (base64-encoded bytes)")

    import base64

    data = base64.b64decode(content)
    url = store_file(path=path, data=data, content_type=content_type, metadata=metadata)
    return {"url": url, "path": path}


async def _handle_write_doc(arguments: Dict[str, Any]) -> Dict[str, Any]:
    collection = arguments.get("collection")
    doc_id: Optional[str] = arguments.get("doc_id")
    data = arguments.get("data")

    if not collection:
        raise ValueError("Missing required field 'collection'")
    if data is None:
        raise ValueError("Missing required field 'data'")

    new_id = write_doc(collection=collection, doc_id=doc_id, data=data)
    return {"doc_id": new_id}


async def _handle_update_doc(arguments: Dict[str, Any]) -> Dict[str, Any]:
    collection = arguments.get("collection")
    doc_id: Optional[str] = arguments.get("doc_id")
    data = arguments.get("data")

    if not collection:
        raise ValueError("Missing required field 'collection'")
    if not doc_id:
        raise ValueError("Missing required field 'doc_id'")
    if data is None:
        raise ValueError("Missing required field 'data'")

    update_doc(collection=collection, doc_id=doc_id, data=data)
    return {"status": "ok"}


async def _handle_read_doc(arguments: Dict[str, Any]) -> Dict[str, Any]:
    collection = arguments.get("collection")
    doc_id: Optional[str] = arguments.get("doc_id")

    if not collection:
        raise ValueError("Missing required field 'collection'")
    if not doc_id:
        raise ValueError("Missing required field 'doc_id'")

    data = read_doc(collection=collection, doc_id=doc_id)
    return {"data": data}


async def _handle_query(arguments: Dict[str, Any]) -> Dict[str, Any]:
    collection = arguments.get("collection")
    filters_arg: List[Dict[str, Any]] = arguments.get("filters") or []
    limit = arguments.get("limit")

    if not collection:
        raise ValueError("Missing required field 'collection'")

    filters = [
        FirestoreFilter(field=f["field"], op=f["op"], value=f["value"])
        for f in filters_arg
        if "field" in f and "op" in f and "value" in f
    ]
    docs = query_collection(collection=collection, filters=filters, limit=limit)
    return {"results": docs}


def register_tools(registry: ToolRegistry) -> None:
    # Minimal context field for auditing, even though this module does not
    # talk to ArmorIQ directly. Agents are expected to call policy.check_intent
    # before sensitive usages of these tools.
    context_schema: Dict[str, Any] = {
        "type": "object",
        "description": "Standard ToolContext fields for auditing.",
    }

    store_file_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Logical storage path in the bucket."},
            "content": {
                "type": "string",
                "description": "Base64-encoded file bytes.",
            },
            "content_type": {
                "type": "string",
                "description": "MIME type of the file.",
            },
            "metadata": {
                "type": "object",
                "description": "Optional key-value metadata to attach to the object.",
            },
            "context": context_schema,
        },
        "required": ["path", "content"],
        "additionalProperties": True,
    }

    write_doc_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "doc_id": {"type": "string"},
            "data": {"type": "object"},
            "context": context_schema,
        },
        "required": ["collection", "data"],
        "additionalProperties": True,
    }

    update_doc_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "doc_id": {"type": "string"},
            "data": {"type": "object"},
            "context": context_schema,
        },
        "required": ["collection", "doc_id", "data"],
        "additionalProperties": True,
    }

    read_doc_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "doc_id": {"type": "string"},
            "context": context_schema,
        },
        "required": ["collection", "doc_id"],
        "additionalProperties": True,
    }

    query_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op": {"type": "string"},
                        "value": {},
                    },
                    "required": ["field", "op", "value"],
                },
            },
            "limit": {"type": "integer"},
            "context": context_schema,
        },
        "required": ["collection"],
        "additionalProperties": True,
    }

    registry.add_tool(
        types.Tool(
            name="firebase.store_file",
            description="Store a file in Firebase Storage and return its URL.",
            inputSchema=store_file_schema,
        ),
        _handle_store_file,
    )
    registry.add_tool(
        types.Tool(
            name="firebase.write_doc",
            description="Write a document to Firestore (auto ID if doc_id omitted).",
            inputSchema=write_doc_schema,
        ),
        _handle_write_doc,
    )
    registry.add_tool(
        types.Tool(
            name="firebase.update_doc",
            description="Patch an existing Firestore document.",
            inputSchema=update_doc_schema,
        ),
        _handle_update_doc,
    )
    registry.add_tool(
        types.Tool(
            name="firebase.read_doc",
            description="Read a single Firestore document by ID.",
            inputSchema=read_doc_schema,
        ),
        _handle_read_doc,
    )
    registry.add_tool(
        types.Tool(
            name="firebase.query",
            description="Query a Firestore collection with simple filters.",
            inputSchema=query_schema,
        ),
        _handle_query,
    )

