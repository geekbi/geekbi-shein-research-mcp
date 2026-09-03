"""GeekBI platform research MCP server."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any
from urllib.error import URLError

from mcp.server import MCPServer

from . import __version__
from .auth import ActionRequired, GeekBIError
from .client import GeekBIClient
from .config import (
    PLATFORM_LABEL,
    SERVER_DESCRIPTION,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    SERVER_TITLE,
    TOOL_SPECS,
)
from .models import MODEL_CLASSES


mcp = MCPServer(
    name=SERVER_NAME,
    title=SERVER_TITLE,
    description=SERVER_DESCRIPTION,
    instructions=SERVER_INSTRUCTIONS,
    website_url="https://www.geekbi.com",
    version=__version__,
)


def _execute(callback):
    try:
        return callback()
    except ActionRequired as error:
        return error.public_payload()
    except GeekBIError as error:
        return {"error": True, "msg": str(error)}
    except TimeoutError:
        return {"error": True, "msg": "极鲸云请求超时，请稍后重试"}
    except URLError:
        return {"error": True, "msg": "暂时无法连接极鲸云，请稍后重试"}
    except (OSError, ValueError) as error:
        return {"error": True, "msg": str(error)}


def _register_site_tool(spec: dict[str, Any]) -> None:
    def site_tool(country=None):
        return _execute(lambda: GeekBIClient().sites(spec["endpoint"], country))

    site_tool.__name__ = spec["name"]
    site_tool.__doc__ = spec["description"]
    site_tool.__annotations__ = {"country": str | None, "return": dict[str, Any]}
    mcp.tool(name=spec["name"], title=spec["title"], description=spec["description"])(site_tool)


def _register_query_tool(spec: dict[str, Any]) -> None:
    model = MODEL_CLASSES[spec["key"]]

    def query_tool(query):
        client = GeekBIClient()
        if spec["kind"] == "image":
            return _execute(lambda: client.image(spec["endpoint"], query, spec["title"]))
        return _execute(lambda: client.query(spec["endpoint"], query, spec["title"]))

    query_tool.__name__ = spec["name"]
    query_tool.__doc__ = spec["description"]
    query_tool.__annotations__ = {"query": model, "return": dict[str, Any]}
    mcp.tool(name=spec["name"], title=spec["title"], description=spec["description"])(query_tool)


for _tool_spec in TOOL_SPECS:
    if _tool_spec["kind"] == "site":
        _register_site_tool(_tool_spec)
    else:
        _register_query_tool(_tool_spec)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"启动 GeekBI {PLATFORM_LABEL} Research MCP")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 监听端口")
    args = parser.parse_args()
    try:
        if args.transport == "streamable-http":
            mcp.run(transport="streamable-http", host=args.host, port=args.port, json_response=True)
            return
        mcp.run(transport="stdio")
    except (KeyboardInterrupt, asyncio.CancelledError):
        return
