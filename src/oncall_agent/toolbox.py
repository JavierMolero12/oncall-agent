"""The agent's side of MCP: list the server's tools, offer the safe ones to the model, call them."""

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters


@dataclass(frozen=True)
class ToolInfo:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    data: dict[str, Any] | None
    text: str


class ToolBox:
    """Wraps an MCP client. The model only ever sees the read-only tools; the web app can still
    call open_pull_request itself after a person presses the button."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._tools: dict[str, ToolInfo] = {}

    async def refresh(self) -> list[ToolInfo]:
        listed, cursor = [], None
        while True:
            page = await self._client.list_tools(cursor=cursor)
            listed.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
        self._tools = {
            tool.name: ToolInfo(
                name=tool.name,
                title=tool.title or tool.name,
                description=tool.description or "",
                input_schema=tool.input_schema,
                # Fail closed: a tool that does not say it is read-only counts as a writer.
                read_only=bool(tool.annotations and tool.annotations.read_only_hint),
            )
            for tool in listed
        }
        return list(self._tools.values())

    def model_tools(self) -> list[dict[str, Any]]:
        """The tools in the format of the Anthropic API, read-only ones only."""
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self._tools.values()
            if t.read_only
        ]

    def title(self, name: str) -> str:
        tool = self._tools.get(name)
        return tool.title if tool else name

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """A failing tool comes back as ok=False with its Spanish message. If the server itself
        is gone, the exception is raised: the turn fails and is rolled back."""
        result = await self._client.call_tool(name, arguments)
        text = "".join(getattr(block, "text", "") for block in result.content)
        if result.is_error:
            message = text.removeprefix(f"Error executing tool {name}: ")
            return ToolOutcome(ok=False, data=None, text=message)
        data = result.structured_content
        return ToolOutcome(
            ok=True, data=data if data is not None else _json_object(text), text=text
        )


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


@asynccontextmanager
async def connect_toolbox() -> AsyncIterator[ToolBox]:
    """Start the MCP server as a subprocess, talk to it over stdio and hand out a ready ToolBox."""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "oncall_agent.mcp_server"],
        env=dict(os.environ),  # same kubeconfig, PATH and .env settings as this process
    )
    async with Client(server) as client:
        toolbox = ToolBox(client)
        await toolbox.refresh()
        yield toolbox
