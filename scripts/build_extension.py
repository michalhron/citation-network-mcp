"""Build the Claude Desktop extension (.mcpb) from this repository.

    uv run python scripts/build_extension.py

Fills manifest.json's version and tool list, and the Claude Code plugin's
version, from the code; validates the manifest;
and packs dist/scopus-mcp-<version>.mcpb with the official mcpb CLI (run
through npx, so Node.js is required). tests/test_extension.py fails when the
committed manifest.json is out of date; running this script fixes that.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCPB = ['npx', '-y', '@anthropic-ai/mcpb@2.1.2']

os.environ.setdefault('SCOPUS_API_KEY', 'extension-build')
os.environ.setdefault('SCOPUS_DISABLE_SECRET_STORE', '1')
sys.path.insert(0, str(ROOT / 'src'))


def _first_sentence(text: str) -> str:
    text = ' '.join(text.split())
    end = text.find('. ')
    return text if end == -1 else text[:end + 1]


def manifest_with_code(manifest: dict, version: str, tools) -> dict:
    """The manifest with version and tools taken from the code."""
    updated = dict(manifest)
    updated['version'] = version
    updated['tools'] = [{'name': t.name, 'description': _first_sentence(t.description)}
                        for t in tools]
    return updated


async def _code_tools():
    from scopus_mcp import server
    try:
        return await server.handle_list_tools()
    finally:
        await server.client.close()
        await server.openalex.close()


PLUGIN_JSON = ROOT / 'plugins' / 'scopus-mcp' / '.claude-plugin' / 'plugin.json'


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def sync_manifest() -> str:
    """Bring manifest.json and the Claude Code plugin's version up to date."""
    from scopus_mcp import __version__
    path = ROOT / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    _write_json(path, manifest_with_code(manifest, __version__, asyncio.run(_code_tools())))
    plugin = json.loads(PLUGIN_JSON.read_text(encoding='utf-8'))
    _write_json(PLUGIN_JSON, {**plugin, 'version': __version__})
    return __version__


def main():
    version = sync_manifest()
    subprocess.run([*MCPB, 'validate', str(ROOT / 'manifest.json')], check=True)
    out = ROOT / 'dist' / f'scopus-mcp-{version}.mcpb'
    out.parent.mkdir(exist_ok=True)
    subprocess.run([*MCPB, 'pack', str(ROOT), str(out)], check=True)
    print(f"built {out}")


if __name__ == '__main__':
    main()
