"""Backward and forward citations of one document."""
import mcp.types as types

from ..openalex import clean_openalex_work, short_id
from ..records import clean_references, clean_search_results
from .common import SOURCE_SCHEMA, _resolve_openalex_work, _source, server_module


TOOLS = [
    types.Tool(
        name="get_references",
        description=(
            "Retrieve the cited-reference list of a document (Backward Citations) "
            "via the Abstract Retrieval REF view. Complements get_citing_papers, "
            "which returns forward citations. Scopus requires an entitled "
            "(subscriber) key; source='openalex' does not."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scopus_id": {
                    "type": "string",
                    "description": (
                        "The Scopus ID (or EID) of the document whose references to "
                        "retrieve. With source='openalex', a DOI or OpenAlex work ID also works."
                    )
                },
                "source": SOURCE_SCHEMA,
                "count": {
                    "type": "integer",
                    "description": "Maximum number of references to return (default 25).",
                    "default": 25
                }
            },
            "required": ["scopus_id"]
        }
    ),
    types.Tool(
        name="get_citing_papers",
        description="Retrieve a list of papers that have cited the specified document (Forward Citations).",
        inputSchema={
            "type": "object",
            "properties": {
                "scopus_id": {
                    "type": "string",
                    "description": (
                        "The Scopus ID of the document to find citations for. With "
                        "source='openalex', a DOI or OpenAlex work ID also works."
                    )
                },
                "source": SOURCE_SCHEMA,
                "count": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 25).",
                    "default": 5,
                    "maximum": 25
                },
                "sort": {
                    "type": "string",
                    "description": "Sort order (e.g., 'coverDate', 'relevancy').",
                    "default": "coverDate"
                }
            },
            "required": ["scopus_id"]
        }
    ),
]


async def _get_references(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    scopus_id = arguments.get("scopus_id")
    count = arguments.get("count", 25)
    if not scopus_id:
        raise ValueError("scopus_id is required")

    if _source(arguments) == 'openalex':
        work = await _resolve_openalex_work(scopus_id)
        refs = await openalex.references(work, limit=count)
        references = [clean_openalex_work(w) for w in refs]
        if not references:
            return [types.TextContent(
                type="text",
                text=("OpenAlex lists no references for this work. Reference "
                      "coverage varies by publisher; try source='scopus'.")
            )]
        return [types.TextContent(type="text", text=str(references))]

    raw_data = await client.get_references(scopus_id)
    references = clean_references(raw_data, limit=count)
    if not references:
        return [types.TextContent(
            type="text",
            text=("No references returned. The document may have no indexed "
                  "references, or your API key may lack REF-view entitlement.")
        )]
    return [types.TextContent(type="text", text=str(references))]


async def _get_citing_papers(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    scopus_id = arguments.get("scopus_id")
    count = arguments.get("count", 5)
    sort = arguments.get("sort", "coverDate")

    if not scopus_id:
        raise ValueError("scopus_id is required")

    if _source(arguments) == 'openalex':
        work = await _resolve_openalex_work(scopus_id)
        works, _ = await openalex.citing(short_id(work['id']), max_results=count, sort=sort)
        results = [clean_openalex_work(w) for w in works]
    else:
        # Forward citations via centralized REF(2-s2.0-<id>) construction.
        raw_data = await client.get_citing_papers(scopus_id, count=count, sort=sort)
        results = clean_search_results(raw_data)

    return [types.TextContent(type="text", text=str(results))]


HANDLERS = {
    'get_references': _get_references,
    'get_citing_papers': _get_citing_papers,
}
