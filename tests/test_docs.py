"""Documentation cannot drift from the code."""
import asyncio
import importlib.util
import os
import re
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def _generator():
    spec = importlib.util.spec_from_file_location('gen_tools_doc', ROOT / 'scripts' / 'gen_tools_doc.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tools_reference_is_current():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(server.handle_list_tools())
    finally:
        loop.close()
    expected = _generator().render(tools)
    actual = (ROOT / 'docs' / 'tools.md').read_text(encoding='utf-8')
    assert actual == expected, (
        "docs/tools.md is stale; run: uv run python scripts/gen_tools_doc.py")


def test_relative_links_resolve():
    """Every relative link in README.md and docs/*.md points at a real file."""
    pages = [ROOT / 'README.md', *sorted((ROOT / 'docs').glob('*.md'))]
    broken = []
    for page in pages:
        for target in re.findall(r'\]\(([^)#\s]+)(?:#[^)]*)?\)', page.read_text(encoding='utf-8')):
            if re.match(r'[a-z]+:', target):
                continue  # external URL
            if not (page.parent / target).exists():
                broken.append(f"{page.relative_to(ROOT)} -> {target}")
    assert not broken, broken
