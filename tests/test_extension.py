"""The Claude Desktop extension manifest and Claude Code plugin files match the code."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

import scopus_mcp

ROOT = Path(__file__).resolve().parent.parent


def _server_tools():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(server.handle_list_tools())
    finally:
        loop.close()


def _build_script():
    spec = importlib.util.spec_from_file_location(
        'build_extension', ROOT / 'scripts' / 'build_extension.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_matches_code():
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    expected = _build_script().manifest_with_code(
        manifest, scopus_mcp.__version__, _server_tools())
    assert manifest == expected, (
        "manifest.json is stale; run: uv run python scripts/build_extension.py")


def test_manifest_passes_every_secret_through_env():
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    env = manifest['server']['mcp_config']['env']
    for key in manifest['user_config']:
        assert f'${{user_config.{key}}}' in env.values(), key
    assert manifest['user_config']['scopus_api_key']['sensitive'] is True
    assert (ROOT / manifest['icon']).exists()


def test_empty_optional_settings_count_as_unset(monkeypatch):
    """Claude Desktop substitutes blank optional fields as empty strings."""
    from scopus_mcp import config
    monkeypatch.setenv('SCOPUS_INSTTOKEN', '')
    monkeypatch.setenv('OPENALEX_API_KEY', '')
    monkeypatch.setattr(config, 'load_config_file', lambda: {})
    assert config.resolve_insttoken() == (None, None)
    assert config.resolve_openalex_key() == (None, None)


def test_server_reports_package_version_and_instructions():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    options = server.server.create_initialization_options()
    assert options.server_version == scopus_mcp.__version__
    assert 'diagnose_connection' in options.instructions


def test_claude_code_plugin_matches_code():
    plugin = json.loads((ROOT / 'plugins' / 'scopus-mcp' / '.claude-plugin' / 'plugin.json')
                        .read_text(encoding='utf-8'))
    assert plugin['version'] == scopus_mcp.__version__, (
        "plugin.json is stale; run: uv run python scripts/build_extension.py")
    marketplace = json.loads((ROOT / '.claude-plugin' / 'marketplace.json').read_text(encoding='utf-8'))
    entry = marketplace['plugins'][0]
    assert entry['name'] == plugin['name']  # install id and manifest name must match
    assert (ROOT / entry['source']).is_dir()
    servers = json.loads((ROOT / 'plugins' / 'scopus-mcp' / '.mcp.json').read_text())['mcpServers']
    assert servers['scopus']['args'][-1] == 'scopus-mcp'
