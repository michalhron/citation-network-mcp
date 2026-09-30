import asyncio
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from . import __version__, jobs
# fetch_bibtex and fetch_oa_fulltext are imported here so tools resolve them
# through this module at call time (tests replace them here).
from .bibtex import fetch_bibtex  # noqa: F401
from .client import ScopusClient
from .openalex import OpenAlexClient
from .oa_fulltext import fetch_oa_fulltext  # noqa: F401

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scopus-plus-mcp")

SERVER_VERSION = __version__

# Initialize Server
SERVER_INSTRUCTIONS = (
    "Literature and citation-network tools over Scopus (default) or OpenAlex "
    "(source='openalex'). When a Scopus call fails, especially with 'Error "
    "translating query', run diagnose_connection: it names the tools the "
    "current access cannot use. Without Scopus subscriber access, pass "
    "source='openalex'. Keep one source per analysis: IDs and citation graphs "
    "differ between them. Large results are written to files; report the paths."
)

server = Server(
    "scopus-plus-mcp",
    version=__version__,
    instructions=SERVER_INSTRUCTIONS,
    website_url="https://github.com/michalhron/scopus-plus-mcp",
)
client = ScopusClient()
# OpenAlex backend: the same analyses without Scopus subscriber entitlement.
openalex = OpenAlexClient()

# Tools live in scopus_mcp.tools, one module per group; imported after the
# clients exist, although tools only read them at call time.
from .tools import HANDLERS, TOOLS  # noqa: E402


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return list(TOOLS)


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if not arguments:
        arguments = {}

    try:
        handler = HANDLERS.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        if name in jobs.LONG_TOOLS:
            return await jobs.run(name, arguments, handler)
        return await handler(arguments)

    except Exception as e:
        logger.error(f"Error executing tool {name}: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="research-summary",
            description="Search for papers on a topic and generate a research summary",
            arguments=[
                types.PromptArgument(
                    name="topic",
                    description="The research topic (e.g., 'machine learning healthcare')",
                    required=True
                )
            ]
        ),
        types.Prompt(
            name="author-analysis",
            description="Analyze an author's research impact and recent work",
            arguments=[
                types.PromptArgument(
                    name="author_id",
                    description="The Scopus Author ID",
                    required=True
                )
            ]
        )
    ]

@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    if not arguments:
        arguments = {}

    if name == "research-summary":
        topic = arguments.get("topic", "unknown topic")
        return types.GetPromptResult(
            description=f"Research summary for {topic}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Please search specifically for high-cited papers related to '{topic}' published in the last 5 years using the search_scopus tool. Sort by cited references if possible. After retrieving the results, please summarize the key trends and findings in this field."
                    )
                )
            ]
        )

    if name == "author-analysis":
        author_id = arguments.get("author_id", "")
        return types.GetPromptResult(
            description=f"Analysis of author {author_id}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Please call the get_author_profile tool for Author ID '{author_id}'. Based on the returned data, analyze their research impact (citations, h-index if available), identify their main affiliation, and summarize their academic standing."
                    )
                )
            ]
        )

    raise ValueError(f"Unknown prompt: {name}")

async def main():
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options()
            )
    finally:
        # Ensure client is closed on shutdown
        await client.close()

def start():
    """Entry point for the package script."""
    asyncio.run(main())

if __name__ == "__main__":
    start()
