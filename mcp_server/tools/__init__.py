"""
Tool registration utilities.

Each module in this package exposes a `register_tools(registry)` function that
adds its tools to the central registry used by the MCP server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from mcp import types


ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass
class RegisteredTool:
    spec: types.Tool
    handler: ToolHandler


class ToolRegistry:
    """
    In-memory registry mapping MCP tool names to their specifications and handlers.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, RegisteredTool] = {}

    def add_tool(self, tool: types.Tool, handler: ToolHandler) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = RegisteredTool(spec=tool, handler=handler)

    def list_tools(self) -> List[types.Tool]:
        return [rt.spec for rt in self._tools.values()]

    def get_handler(self, name: str) -> ToolHandler:
        if name not in self._tools:
            raise KeyError(f"Unknown tool '{name}'")
        return self._tools[name].handler

