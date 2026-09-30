"""MCP tool definitions and handlers, one module per tool group."""
from .. import jobs
from . import bibliometrics, citations, corpus, diagnostics, history, networks, search, transmission

_GROUPS = (search, citations, networks, corpus, history, transmission, bibliometrics, diagnostics, jobs)

TOOLS = [tool for group in _GROUPS for tool in group.TOOLS]
HANDLERS = {name: handler for group in _GROUPS for name, handler in group.HANDLERS.items()}
