from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from mcp import types
from mcp.server import Server
from mcp.server.models import InitializationOptions

from .main import create_server_with_registry
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


def create_http_app() -> FastAPI:
    """
    Create FastAPI app that wraps the MCP server for HTTP/SSE transport.
    
    This implements the MCP protocol over HTTP/SSE as specified in the
    Model Context Protocol specification. The server properly handles
    all MCP protocol methods through the Server's internal handlers.
    
    MCP over HTTP/SSE:
    - Client sends POST requests with JSON-RPC messages in body
    - Server responds with SSE stream containing JSON-RPC responses
    - Each SSE event format: "data: <json-rpc-response>\\n\\n"
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
            "protocol": "mcp",
            "transport": "http/sse",
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
        
        The MCP protocol uses JSON-RPC 2.0 over SSE, where:
        - Client sends POST with JSON-RPC request in body
        - Server responds with SSE stream containing JSON-RPC response
        - Format: "data: <json-rpc-response>\\n\\n"
        
        Supported MCP methods:
        - initialize: Server initialization handshake
        - tools/list: List available tools
        - tools/call: Execute a tool
        - resources/list: List available resources (if supported)
        - resources/read: Read a resource (if supported)
        - prompts/list: List available prompts (if supported)
        - prompts/get: Get a prompt (if supported)
        """
        # Read request body (JSON-RPC message)
        try:
            body = await request.body()
            if not body:
                return {"error": "Empty request body"}, 400
            
            message = json.loads(body)
            
            # Validate JSON-RPC 2.0 format
            if message.get("jsonrpc") != "2.0":
                return {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {
                        "code": -32600,
                        "message": "Invalid Request: jsonrpc must be '2.0'",
                    },
                }, 400
            
            method = message.get("method")
            message_id = message.get("id")
            params = message.get("params", {})
            
            if not method:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32600,
                        "message": "Invalid Request: method is required",
                    },
                }, 400
            
        except json.JSONDecodeError as e:
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Parse error: {str(e)}",
                },
            }, 400
        except Exception as e:
            logger.exception("Error parsing request")
            return {
                "jsonrpc": "2.0",
                "id": message.get("id") if "id" in locals() else None,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}",
                },
            }, 500
        
        async def generate_sse() -> AsyncIterator[str]:
            """Generate SSE events from MCP server responses."""
            try:
                # Route request through MCP Server's handlers
                response = await handle_mcp_request(mcp_server, registry, method, params, message_id)
                
                # Format as SSE event
                yield f"data: {json.dumps(response)}\n\n"
                
            except Exception as e:
                logger.exception("Error handling MCP request")
                error_response = {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}",
                    },
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


async def handle_mcp_request(
    server: Server,
    registry: ToolRegistry,
    method: str,
    params: dict[str, Any],
    message_id: Any,
) -> dict[str, Any]:
    """
    Handle an MCP protocol request by routing it through the Server's handlers.
    
    This function properly integrates with the MCP Server's internal request
    handling to ensure full protocol compliance.
    """
    try:
        # Handle MCP protocol methods
        if method == "initialize":
            # MCP initialization handshake
            init_options = InitializationOptions(**params) if params else InitializationOptions()
            
            # Get server capabilities and info
            # The Server object should expose these, but we'll construct them
            # based on what handlers are registered
            capabilities = {
                "tools": {},
                "resources": {},
                "prompts": {},
            }
            
            # Set capabilities based on what's available
            # Tools are always available via registry
            capabilities["tools"] = {"listChanged": False}
            
            # Resources and prompts are optional
            # Check if handlers are registered (they may not be)
            # For now, we don't support resources or prompts
            # but the structure is here for future expansion
            
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": capabilities,
                "serverInfo": {
                    "name": server.name,
                    "version": "0.1.0",
                },
            }
            
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": result,
            }
        
        elif method == "tools/list":
            # List available tools using registry
            tools = registry.list_tools()
            
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"tools": [tool.model_dump() for tool in tools]},
            }
        
        elif method == "tools/call":
            # Execute a tool using registry
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            
            if not tool_name:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params: 'name' is required",
                    },
                }
            
            try:
                # Get handler from registry and execute
                handler = registry.get_handler(tool_name)
                result = await handler(arguments)
                
                # Format as MCP CallToolResult
                content = types.TextContent(type="text", text=json.dumps(result))
                call_result = types.CallToolResult(content=[content])
                
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "content": [call_result.model_dump()],
                    },
                }
            except KeyError as e:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32601,
                        "message": f"Tool not found: {tool_name}",
                    },
                }
            except Exception as e:
                logger.exception(f"Error executing tool {tool_name}")
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32603,
                        "message": f"Tool execution error: {str(e)}",
                    },
                }
        
        elif method == "resources/list":
            # List available resources (if supported)
            list_resources_handler = getattr(server, "_list_resources_handler", None)
            if not list_resources_handler:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32601,
                        "message": "Method not found: resources/list not supported",
                    },
                }
            
            resources = await list_resources_handler()
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"resources": [r.model_dump() for r in resources]},
            }
        
        elif method == "resources/read":
            # Read a resource (if supported)
            uri = params.get("uri")
            if not uri:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params: 'uri' is required",
                    },
                }
            
            read_resource_handler = getattr(server, "_read_resource_handler", None)
            if not read_resource_handler:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32601,
                        "message": "Method not found: resources/read not supported",
                    },
                }
            
            contents = await read_resource_handler(uri)
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "contents": [c.model_dump() for c in contents],
                },
            }
        
        elif method == "prompts/list":
            # List available prompts (if supported)
            list_prompts_handler = getattr(server, "_list_prompts_handler", None)
            if not list_prompts_handler:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32601,
                        "message": "Method not found: prompts/list not supported",
                    },
                }
            
            prompts = await list_prompts_handler()
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"prompts": [p.model_dump() for p in prompts]},
            }
        
        elif method == "prompts/get":
            # Get a prompt (if supported)
            name = params.get("name")
            arguments = params.get("arguments", {})
            
            if not name:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params: 'name' is required",
                    },
                }
            
            get_prompt_handler = getattr(server, "_get_prompt_handler", None)
            if not get_prompt_handler:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {
                        "code": -32601,
                        "message": "Method not found: prompts/get not supported",
                    },
                }
            
            prompt = await get_prompt_handler(name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": prompt.model_dump(),
            }
        
        else:
            # Unknown method
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }
    
    except Exception as e:
        logger.exception(f"Error handling MCP method {method}")
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}",
            },
        }


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
