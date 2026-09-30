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
    plugin = json.loads((ROOT / 'plugins' / 'scopus-plus-mcp' / '.claude-plugin' / 'plugin.json')
                        .read_text(encoding='utf-8'))
    assert plugin['version'] == scopus_mcp.__version__, (
        "plugin.json is stale; run: uv run python scripts/build_extension.py")
    marketplace = json.loads((ROOT / '.claude-plugin' / 'marketplace.json').read_text(encoding='utf-8'))
    entry = marketplace['plugins'][0]
    assert entry['name'] == plugin['name']  # install id and manifest name must match
    assert (ROOT / entry['source']).is_dir()
    servers = json.loads((ROOT / 'plugins' / 'scopus-plus-mcp' / '.mcp.json').read_text())['mcpServers']
    assert servers['scopus-plus']['args'][-1] == 'scopus-plus-mcp'


def test_registry_entry_matches_package():
    """server.json (MCP registry) names this package at the current version,
    and the README carries the mcp-name marker the registry checks on PyPI."""
    import re
    registry = json.loads((ROOT / 'server.json').read_text(encoding='utf-8'))
    # tomllib needs Python 3.11; CI also runs 3.10, so read the two fields directly.
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    project_name = re.search(r'^name = "([^"]+)"', pyproject, re.M).group(1)
    readme = re.search(r'^readme = "([^"]+)"', pyproject, re.M).group(1)
    package = registry['packages'][0]
    assert registry['version'] == package['version'] == scopus_mcp.__version__, (
        "server.json is stale; run: uv run python scripts/build_extension.py")
    assert package['identifier'] == project_name
    assert f"mcp-name: {registry['name']}" in (ROOT / readme).read_text(encoding='utf-8')
    required = {e['name'] for e in package['environmentVariables'] if e['isRequired']}
    assert required == {'SCOPUS_API_KEY'}  # the server does not start without it


def test_missing_key_exits_cleanly():
    """No traceback for a first-time user without a key: a message and exit 1."""
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k != 'SCOPUS_API_KEY'}
    env['SCOPUS_DISABLE_SECRET_STORE'] = '1'
    result = subprocess.run(
        [sys.executable, '-c', 'from scopus_mcp.cli import main; main()'],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 1
    assert 'Scopus API Key not found' in result.stderr
    assert '#install' in result.stderr
    assert 'Traceback' not in result.stderr
