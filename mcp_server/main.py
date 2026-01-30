from __future__ import annotations

import json
from typing import Any, Dict, List

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .armor_iq_client import ArmorIQClient
from .config import get_settings
from .firebase_client import init_firebase
from .llm_client import LLMClient
from .tools import ToolRegistry
from .tools import (
    adherence_tools,
    drug_tools,
    firebase_tools,
    governance_tools,
    notify_tools,
    ocr_tools,
    rx_tools,
    schedule_tools,
)


def create_server() -> Server:
    """
    Create and configure the MCP server with all registered tools.
    """
    settings = get_settings()

    # Initialize shared infrastructure
    init_firebase(settings)
    armor_client = ArmorIQClient(settings)
    llm_client = LLMClient(settings)

    registry = ToolRegistry()

    # Register tool groups
    firebase_tools.register_tools(registry)
    governance_tools.register_tools(registry, armor_client=armor_client)
    ocr_tools.register_tools(registry, armor_client=armor_client, llm_client=llm_client)
    rx_tools.register_tools(registry, armor_client=armor_client, llm_client=llm_client)
    drug_tools.register_tools(registry, armor_client=armor_client, llm_client=llm_client)
    schedule_tools.register_tools(registry, armor_client=armor_client, llm_client=llm_client)
    notify_tools.register_tools(registry, armor_client=armor_client)
    adherence_tools.register_tools(registry, armor_client=armor_client, llm_client=llm_client)

    server = Server("medicos-mcp-backend")

    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        return registry.list_tools()

    @server.call_tool()
    async def call_tool(
        name: str,
        arguments: Dict[str, Any],
    ) -> List[types.CallToolResult]:
        handler = registry.get_handler(name)
        result = await handler(arguments)
        # For now we always return a single text content item containing JSON.
        content = types.TextContent(type="text", text=json.dumps(result))
        return [types.CallToolResult(content=[content])]

    return server


def main() -> None:
    """
    Entrypoint for running the MCP server.
    
    Supports two transport modes:
    - stdio: For direct process-to-process communication (default)
    - http: For HTTP/SSE transport behind reverse proxy
    """
    settings = get_settings()
    
    if settings.transport == "http":
        # Run HTTP server
        from .http_server import run_http_server
        anyio.run(run_http_server, settings.server_host, settings.server_port)
    else:
        # Run stdio server (default)
        server = create_server()
        anyio.run(stdio_server(server).run)


if __name__ == "__main__":
    main()

