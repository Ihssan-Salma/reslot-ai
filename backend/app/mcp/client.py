"""Validated official SDK client for future LangGraph callers."""

from types import TracebackType
from typing import Any, Self

from mcp import Client
from mcp.server import MCPServer
from pydantic import ValidationError

from app.mcp.schemas import INPUTS, ToolResult
from app.mcp.server import invalid


class GoogleToolsClient:
    def __init__(self, server: MCPServer[Any]):
        self._client = Client(server, read_timeout_seconds=120)

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, traceback)

    async def discover(self) -> list[dict[str, Any]]:
        tools = await self._client.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "output_schema": tool.output_schema,
            }
            for tool in tools.tools
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in INPUTS or "exclude_event_id" in arguments:
            return invalid()
        try:
            args = INPUTS[name].model_validate(arguments)
        except ValidationError:
            return invalid()
        try:
            result = await self._client.call_tool(name, args.model_dump(mode="json"))
            return ToolResult.model_validate(result.structured_content)
        except Exception:
            return ToolResult(
                category="GOOGLE_API_ERROR", message="MCP request could not be completed"
            )
