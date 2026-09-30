"""Scopus Plus MCP: search, full text, citation networks and bibliometrics over
Scopus, with OpenAlex and other open sources.

Distributed as `scopus-plus-mcp` (formerly `citation-network-mcp`); the import
package keeps its original name, `scopus_mcp`, so existing configurations keep
working.
"""

# The single source of the version: pyproject.toml reads it (hatch dynamic
# version), and the server and every User-Agent header import it.
__version__ = "0.23.0"
USER_AGENT = f"ScopusPlusMCP/{__version__}"
