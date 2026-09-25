"""MCP server template (official Python SDK v2, `mcp` >= 2).

Replace the example tool/resource/prompt with your real implementation.
Keep the three structural rules:
  1. module-level server object named `mcp` (so `mcp run` finds it);
  2. transport options go to run(), never to the constructor;
  3. run() stays under `if __name__ == "__main__":` (everything imports this file).

Verified against mcp 2.2.0 / spec 2026-07-28 (2026-09-25).
Docs: https://py.sdk.modelcontextprotocol.io/
"""

import logging

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError

logger = logging.getLogger(__name__)

mcp = MCPServer("My Server", instructions="One line describing what this server provides.")


# ---------------------------------------------------------------------------
# Tools (model-invoked). Type hints = input schema; docstring = description.
# ---------------------------------------------------------------------------


@mcp.tool()
def find_item(query: str, limit: int = 10) -> str:
    """Find items matching a query.

    Describe WHAT it does, WHEN the model should use it, and WHAT it returns.
    """
    if not query.strip():
        # Model can fix this: give the fix in the message.
        raise ToolError("query is empty. Provide a non-empty search string.")
    try:
        results = _search(query, limit)
    except TimeoutError:
        # Transient upstream fault the model can retry.
        raise ToolError("Search backend timed out. Try again.") from None
    logger.info("find_item query=%r limit=%d hits=%d", query, limit, len(results))
    return "\n".join(f"{i}. {r}" for i, r in enumerate(results, 1)) or "No matches."


# Structured output: the return annotation IS the output schema
# (scalars are wrapped as {"result": ...}; Pydantic models pass through as-is).
@mcp.tool()
def count_items() -> int:
    """Count all items in the catalog."""
    return 42


# ---------------------------------------------------------------------------
# Resources (host/application-read, read-only, addressed by URI).
# ---------------------------------------------------------------------------


@mcp.resource("catalog://stats")
def catalog_stats() -> str:
    """Live catalog statistics."""
    return "items=42"


@mcp.resource("catalog://items/{item_id}")
def get_item(item_id: str) -> str:
    """One catalog item by id."""
    item = _CATALOG.get(item_id)
    if item is None:
        # Protocol -32602 with the URI in `data` — host-side error, not the model's.
        raise ResourceNotFoundError(f"catalog://items/{item_id}")
    return item


# ---------------------------------------------------------------------------
# Prompts (user-picked message templates; flat string args, no JSON schema).
# ---------------------------------------------------------------------------


@mcp.prompt()
def summarize_catalog(topic: str) -> str:
    """Summarize the catalog with focus on a topic."""
    return f"Summarize the catalog below, focusing on {topic}:\n\n<catalog here>"


# ---------------------------------------------------------------------------
# Internals (replace with real logic; keep heavy deps imported lazily if slow).
# ---------------------------------------------------------------------------

_CATALOG: dict[str, str] = {"42": "The Answer"}


def _search(query: str, limit: int) -> list[str]:
    return [v for v in _CATALOG.values() if query.lower() in v.lower()][:limit]


if __name__ == "__main__":
    mcp.run()  # stdio (default). HTTP: mcp.run(transport="streamable-http", port=3001)
