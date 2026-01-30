from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from mcp import types

from .main import create_server_with_registry
from .tools import ToolRegistry


def create_http_app() -> FastAPI:
    """
    Create FastAPI app that wraps the MCP server for HTTP/SSE transport.
    
    This allows the MCP server to be accessed via HTTP endpoints,
    enabling reverse proxy deployment at URLs like mcp.p1ng.me
    
    The MCP protocol over HTTP uses Server-Sent Events (SSE) for streaming.
    """
    app = FastAPI(
        title="Medicos MCP Backend",
        version="0.1.0",
        description="MCP server for Hospital Medicine Reminder App",
    )
    
    # Create the MCP server instance and registry (shared across requests)
    mcp_server, registry = create_server_with_registry()
    
    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "ok", "service": "medicos-mcp-backend"}
    
    @app.get("/")
    async def root():
        """Root endpoint with service info."""
        return {
            "service": "medicos-mcp-backend",
            "version": "0.1.0",
            "endpoints": {
                "health": "/health",
                "mcp_stream": "/mcp/stream",
            },
        }
    
    @app.post("/mcp/stream")
    async def mcp_stream(request: Request):
        """
        MCP SSE stream endpoint.
        
        This endpoint handles MCP protocol communication over HTTP/SSE.
        Clients connect here to interact with the MCP server.
        
        The MCP protocol uses JSON-RPC over SSE, where each SSE event
        contains a JSON-RPC message.
        """
        # Read request body (JSON-RPC message)
        try:
            body = await request.body()
            if body:
                message = json.loads(body)
            else:
                # Initial connection - send server info
                message = {"jsonrpc": "2.0", "method": "initialize", "id": 1}
        except json.JSONDecodeError:
            return {"error": "Invalid JSON"}, 400
        
        async def generate_sse() -> AsyncIterator[str]:
            """Generate SSE events from MCP server responses."""
            try:
                method = message.get("method")
                message_id = message.get("id")
                
                # Handle MCP protocol methods
                if method == "tools/list":
                    # Use registry directly since Server doesn't expose list_tools()
                    tools = registry.list_tools()
                    response = {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "result": {"tools": [tool.model_dump() for tool in tools]},
                    }
                    yield f"data: {json.dumps(response)}\n\n"
                
                elif method == "tools/call":
                    params = message.get("params", {})
                    tool_name = params.get("name")
                    arguments = params.get("arguments", {})
                    
                    try:
                        # Use registry to get handler and call it directly
                        handler = registry.get_handler(tool_name)
                        result = await handler(arguments)
                        # Format as MCP CallToolResult
                        content = types.TextContent(type="text", text=json.dumps(result))
                        call_result = types.CallToolResult(content=[content])
                        response = {
                            "jsonrpc": "2.0",
                            "id": message_id,
                            "result": {"content": [call_result.model_dump()]},
                        }
                    except Exception as e:
                        response = {
                            "jsonrpc": "2.0",
                            "id": message_id,
                            "error": {"code": -32603, "message": str(e)},
                        }
                    yield f"data: {json.dumps(response)}\n\n"
                
                elif method == "initialize":
                    # MCP initialization
                    response = {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {
                                "name": "medicos-mcp-backend",
                                "version": "0.1.0",
                            },
                        },
                    }
                    yield f"data: {json.dumps(response)}\n\n"
                
                else:
                    # Unknown method
                    response = {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                    yield f"data: {json.dumps(response)}\n\n"
                    
            except Exception as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": message.get("id") if "id" in message else None,
                    "error": {"code": -32603, "message": str(e)},
                }
                yield f"data: {json.dumps(error_response)}\n\n"
        
        return StreamingResponse(
            generate_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )
    
    return app


async def run_http_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the HTTP server using uvicorn."""
    import uvicorn
    
    app = create_http_app()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    await server.serve()
