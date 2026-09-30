"""Citation Network MCP: literature and citation-network analysis over Scopus and OpenAlex.

Distributed as `citation-network-mcp`; the import package keeps its original
name, `scopus_mcp`, so existing configurations keep working.
"""

# The single source of the version: pyproject.toml reads it (hatch dynamic
# version), and the server and every User-Agent header import it.
__version__ = "0.13.0"
USER_AGENT = f"CitationNetworkMCP/{__version__}"
