"""MCP tool definitions and handlers, one module per tool group."""
from . import bibliometrics, citations, corpus, diagnostics, networks, search

_GROUPS = (search, citations, networks, corpus, bibliometrics, diagnostics)

TOOLS = [tool for group in _GROUPS for tool in group.TOOLS]
HANDLERS = {name: handler for group in _GROUPS for name, handler in group.HANDLERS.items()}
