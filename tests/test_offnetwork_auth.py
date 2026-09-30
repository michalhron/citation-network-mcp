"""Offline tests for off-network entitlement: secret-store credentials
(macOS Keychain, Windows Credential Manager / Linux Secret Service via
keyring), the Elsevier-only proxy, and the diagnostics that report which
route is used.

No network and no real secret store: `security` and `keyring` are faked, so
every platform's path runs on every OS.
"""
import asyncio
import os
import subprocess
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scopus_mcp import config
from scopus_mcp.client import ScopusClient, CANARY_SCOPUS_ID

from tests.diag_fakes import capability_ok


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _completed(stdout='', returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr='')


def _fake_keyring(items, error=None):
    """A stand-in keyring module; get_password raises `error` when given."""
    module = types.ModuleType('keyring')

    def get_password(service, account):
        if error is not None:
            raise error
        return items.get((service, account))

    module.get_password = get_password
    return module


@pytest.fixture
def keychain(monkeypatch):
    """Enables the macOS Keychain path on any OS and serves items from a dict."""
    items = {}
    monkeypatch.delenv('SCOPUS_DISABLE_SECRET_STORE', raising=False)
    monkeypatch.setattr(config.sys, 'platform', 'darwin')

    def fake_run(argv, **kwargs):
        account = argv[argv.index('-a') + 1]
        if account in items:
            return _completed(items[account] + '\n')
        return _completed(returncode=44)

    monkeypatch.setattr(config.subprocess, 'run', fake_run)
    return items


@pytest.fixture
def win_keyring(monkeypatch):
    """Enables the Windows keyring path on any OS; tests set items by account."""
    monkeypatch.delenv('SCOPUS_DISABLE_SECRET_STORE', raising=False)
    monkeypatch.setattr(config.sys, 'platform', 'win32')
    store = {}
    monkeypatch.setitem(sys.modules, 'keyring', _fake_keyring(store))

    class _ByAccount:
        def __setitem__(self, account, value):
            store[('scopus-mcp', account)] = value

    return _ByAccount()


@pytest.fixture
def no_config_file(monkeypatch):
    monkeypatch.setattr(config, 'load_config_file', lambda: {})


def _make_client(env=None):
    with (
        patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy', **(env or {})}),
        patch('scopus_mcp.client.CacheManager') as MockCache,
    ):
        MockCache.return_value.get.return_value = None
        return ScopusClient()


# ---------------------------------------------------------------------------
# macOS: Keychain via the `security` CLI
# ---------------------------------------------------------------------------

def test_keychain_lookup_reads_service_and_account(monkeypatch):
    monkeypatch.delenv('SCOPUS_DISABLE_SECRET_STORE', raising=False)
    monkeypatch.setattr(config.sys, 'platform', 'darwin')
    run = MagicMock(return_value=_completed('tok-123\n'))
    monkeypatch.setattr(config.subprocess, 'run', run)

    assert config.secret_store_lookup('insttoken') == 'tok-123'
    argv = run.call_args.args[0]
    assert argv[1:] == ['find-generic-password', '-s', 'scopus-mcp',
                        '-a', 'insttoken', '-w']


def test_keychain_lookup_missing_item_returns_none(keychain):
    assert config.secret_store_lookup('insttoken') is None


@pytest.mark.parametrize('exc', [OSError('no security'),
                                 subprocess.TimeoutExpired('security', 5)])
def test_keychain_lookup_errors_degrade_to_none(monkeypatch, exc):
    monkeypatch.delenv('SCOPUS_DISABLE_SECRET_STORE', raising=False)
    monkeypatch.setattr(config.sys, 'platform', 'darwin')
    monkeypatch.setattr(config.subprocess, 'run', MagicMock(side_effect=exc))
    assert config.secret_store_lookup('insttoken') is None


def test_macos_does_not_use_keyring(keychain, monkeypatch):
    """Keyring would re-prompt after every uvx rebuild; macOS must avoid it."""
    monkeypatch.setitem(sys.modules, 'keyring',
                        _fake_keyring({('scopus-mcp', 'insttoken'): 'via-keyring'}))
    assert config.secret_store_lookup('insttoken') is None


# ---------------------------------------------------------------------------
# Windows / Linux: keyring
# ---------------------------------------------------------------------------

def test_windows_reads_credential_manager_via_keyring(win_keyring, monkeypatch):
    run = MagicMock()
    monkeypatch.setattr(config.subprocess, 'run', run)
    win_keyring['insttoken'] = 'win-token'
    assert config.secret_store_lookup('insttoken') == 'win-token'
    run.assert_not_called()


def test_windows_missing_item_returns_none(win_keyring):
    assert config.secret_store_lookup('insttoken') is None


def test_windows_source_label_is_keyring(win_keyring, no_config_file):
    win_keyring['insttoken'] = 'win-token'
    assert config.resolve_insttoken() == ('win-token', 'keyring')


def test_linux_keyring_backend_error_degrades_to_none(monkeypatch):
    """Headless Linux has no Secret Service; keyring raises NoKeyringError."""
    monkeypatch.delenv('SCOPUS_DISABLE_SECRET_STORE', raising=False)
    monkeypatch.setattr(config.sys, 'platform', 'linux')
    monkeypatch.setitem(sys.modules, 'keyring',
                        _fake_keyring({}, error=RuntimeError('No recommended backend')))
    assert config.secret_store_lookup('insttoken') is None


def test_keyring_not_installed_returns_none(monkeypatch):
    monkeypatch.delenv('SCOPUS_DISABLE_SECRET_STORE', raising=False)
    monkeypatch.setattr(config.sys, 'platform', 'linux')
    monkeypatch.setitem(sys.modules, 'keyring', None)  # import raises ImportError
    assert config.secret_store_lookup('insttoken') is None


@pytest.mark.parametrize('platform', ['darwin', 'win32', 'linux'])
def test_secret_store_skipped_when_disabled(monkeypatch, platform):
    monkeypatch.setattr(config.sys, 'platform', platform)
    run = MagicMock()
    monkeypatch.setattr(config.subprocess, 'run', run)
    monkeypatch.setitem(sys.modules, 'keyring', _fake_keyring({}, error=AssertionError))
    assert config.secret_store_lookup('insttoken') is None
    run.assert_not_called()


# ---------------------------------------------------------------------------
# Precedence: env > secret store > config.json
# ---------------------------------------------------------------------------

def test_insttoken_env_beats_keychain(keychain, monkeypatch):
    keychain['insttoken'] = 'from-keychain'
    monkeypatch.setenv('SCOPUS_INSTTOKEN', 'from-env')
    assert config.resolve_insttoken() == ('from-env', 'env')


def test_elsevier_insttoken_alias(keychain, monkeypatch):
    monkeypatch.setenv('ELSEVIER_INSTTOKEN', 'from-alias')
    assert config.resolve_insttoken() == ('from-alias', 'env')


def test_insttoken_keychain_beats_config(keychain, monkeypatch):
    keychain['insttoken'] = 'from-keychain'
    monkeypatch.setattr(config, 'load_config_file', lambda: {'insttoken': 'from-config'})
    assert config.resolve_insttoken() == ('from-keychain', 'keychain')


def test_insttoken_falls_back_to_config(keychain, monkeypatch):
    monkeypatch.setattr(config, 'load_config_file', lambda: {'insttoken': 'from-config'})
    assert config.resolve_insttoken() == ('from-config', 'config')


def test_insttoken_absent(keychain, no_config_file):
    assert config.resolve_insttoken() == (None, None)
    assert config.get_insttoken() is None


def test_api_key_from_keychain(keychain, no_config_file, monkeypatch):
    monkeypatch.delenv('SCOPUS_API_KEY', raising=False)
    keychain['api_key'] = 'kc-key'
    assert config.resolve_api_key() == ('kc-key', 'keychain')
    assert config.get_api_key() == 'kc-key'


def test_api_key_from_windows_keyring(win_keyring, no_config_file, monkeypatch):
    monkeypatch.delenv('SCOPUS_API_KEY', raising=False)
    win_keyring['api_key'] = 'win-key'
    assert config.resolve_api_key() == ('win-key', 'keyring')


def test_api_key_missing_error_mentions_secret_store(keychain, no_config_file, monkeypatch):
    monkeypatch.delenv('SCOPUS_API_KEY', raising=False)
    with pytest.raises(ValueError, match='secret store'):
        config.get_api_key()


# ---------------------------------------------------------------------------
# Proxy setting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', ['socks5h://127.0.0.1:1080',
                                 'socks5://127.0.0.1:1080',
                                 'http://proxy.example.edu:3128'])
def test_proxy_accepts_supported_schemes(monkeypatch, url):
    monkeypatch.setenv('SCOPUS_PROXY', url)
    assert config.get_proxy() == url


def test_proxy_from_config_file(no_config_file, monkeypatch):
    monkeypatch.setattr(config, 'load_config_file',
                        lambda: {'proxy': 'socks5h://127.0.0.1:1080'})
    assert config.get_proxy() == 'socks5h://127.0.0.1:1080'


def test_proxy_unset(no_config_file):
    assert config.get_proxy() is None


def test_proxy_rejects_unknown_scheme(monkeypatch):
    monkeypatch.setenv('SCOPUS_PROXY', 'sock5://127.0.0.1:1080')
    with pytest.raises(ValueError, match='Unsupported SCOPUS_PROXY scheme'):
        config.get_proxy()


def test_proxy_scheme_hides_host_and_credentials():
    assert config.proxy_scheme('http://user:pw@proxy.example.edu:3128') == 'http'
    assert config.proxy_scheme(None) is None


# ---------------------------------------------------------------------------
# Client wiring
# ---------------------------------------------------------------------------

def test_client_sends_keychain_insttoken_header(keychain):
    keychain['insttoken'] = 'kc-token'
    client = _make_client()
    assert client.headers['X-ELS-Insttoken'] == 'kc-token'
    assert client.insttoken_source == 'keychain'
    _run(client.close())


def test_client_without_insttoken_sends_no_header():
    client = _make_client()
    assert 'X-ELS-Insttoken' not in client.headers
    assert client.has_insttoken is False
    _run(client.close())


def test_client_builds_socks_proxy_transport():
    """A real AsyncClient with socks5h proves the socks extra is installed."""
    client = _make_client({'SCOPUS_PROXY': 'socks5h://127.0.0.1:1080'})
    assert client.proxy == 'socks5h://127.0.0.1:1080'
    # httpx wraps every proxy in AsyncHTTPTransport; the SOCKS pool is inside.
    pools = [type(t._pool).__name__ for t in client.client._mounts.values()]
    assert pools == ['AsyncSOCKSProxy'], pools
    _run(client.close())


def test_client_without_proxy_has_no_proxy_mounts():
    client = _make_client()
    assert client.proxy is None
    assert not any(client.client._mounts.values())
    _run(client.close())


def _response_401():
    return httpx.Response(401, request=httpx.Request(
        'GET', 'https://api.elsevier.com/content/search/scopus'))


def test_401_with_insttoken_points_at_token():
    client = _make_client({'SCOPUS_INSTTOKEN': 'SECRET-TOK-9'})
    with patch.object(client.client, 'request', new_callable=AsyncMock,
                      return_value=_response_401()):
        with pytest.raises(Exception) as exc_info:
            _run(client._request('GET', 'content/search/scopus', use_cache=False))
    assert 'insttoken is configured' in str(exc_info.value)
    assert 'SECRET-TOK-9' not in str(exc_info.value)
    _run(client.close())


def test_401_without_insttoken_unchanged():
    client = _make_client()
    with patch.object(client.client, 'request', new_callable=AsyncMock,
                      return_value=_response_401()):
        with pytest.raises(Exception) as exc_info:
            _run(client._request('GET', 'content/search/scopus', use_cache=False))
    assert str(exc_info.value) == 'Authentication failed: Invalid API Key'
    _run(client.close())


# ---------------------------------------------------------------------------
# diagnose_connection
# ---------------------------------------------------------------------------

ENTITLEMENT_400_MSG = (
    "Scopus API error 400 for https://api.elsevier.com/content/search/scopus "
    "(query='ALL(gene)'): {\"service-error\":{\"status\":{\"statusCode\":"
    "\"INVALID_INPUT\",\"statusText\":\"Error translating query\"}}}"
)


def _outcomes(abstract_result, search_result):
    def side_effect(method, endpoint, params=None, *args, **kwargs):
        capability = capability_ok(endpoint, params)
        if capability is not None:
            return capability
        outcome = abstract_result if CANARY_SCOPUS_ID in endpoint else search_result
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return side_effect


def _diagnose(client, side_effect):
    writer = MagicMock()
    with (
        patch('asyncio.open_connection', new_callable=AsyncMock,
              return_value=(MagicMock(), writer)) as open_conn,
        patch.object(client.client, 'get', new_callable=AsyncMock,
                     return_value=httpx.Response(
                         200, request=httpx.Request('GET', 'https://api.elsevier.com/'))),
        patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect),
    ):
        report = _run(client.diagnose_connection())
    return report, open_conn


def test_diagnose_reports_sources_and_route_for_insttoken(keychain):
    keychain['insttoken'] = 'kc-token'
    client = _make_client()
    report, _ = _diagnose(client, _outcomes({'ok': True}, {'ok': True}))
    assert report['config']['insttoken_source'] == 'keychain'
    assert report['config']['api_key_source'] == 'env'
    assert report['entitlement_via'] == 'insttoken'
    assert 'kc-token' not in str(report)
    _run(client.close())


def test_diagnose_route_network_ip_when_nothing_configured():
    client = _make_client()
    report, _ = _diagnose(client, _outcomes({'ok': True}, {'ok': True}))
    assert report['entitlement_via'] == 'network_ip'
    assert report['config']['proxy'] is None
    _run(client.close())


def test_diagnose_route_none_when_search_fails():
    client = _make_client()
    report, _ = _diagnose(client, _outcomes({'ok': True}, Exception(ENTITLEMENT_400_MSG)))
    assert report['entitlement_via'] is None
    assert 'SCOPUS_PROXY' in report['verdict']
    _run(client.close())


def test_diagnose_insttoken_configured_but_unentitled():
    client = _make_client({'SCOPUS_INSTTOKEN': 'tok'})
    report, _ = _diagnose(client, _outcomes({'ok': True}, Exception(ENTITLEMENT_400_MSG)))
    assert report['search']['status'] == 'entitlement_missing'
    assert 'not associated' in report['verdict']
    assert "off your institution's network" not in report['verdict']
    _run(client.close())


def test_diagnose_proxy_skips_direct_probe_and_reports_scheme_only():
    client = _make_client({'SCOPUS_PROXY': 'http://user:pw@proxy.example.edu:3128'})
    report, open_conn = _diagnose(client, _outcomes({'ok': True}, {'ok': True}))
    open_conn.assert_not_called()
    assert report['reachability']['via_proxy'] is True
    assert report['config']['proxy'] == 'http'
    assert 'proxy.example.edu' not in str(report) and 'pw' not in str(report)
    assert report['entitlement_via'] == 'proxy'
    assert report['verdict'] == 'Connection and entitlement healthy.'
    _run(client.close())


def test_diagnose_proxy_unentitled_blames_exit_ip():
    client = _make_client({'SCOPUS_PROXY': 'socks5h://127.0.0.1:1080'})
    report, _ = _diagnose(client, _outcomes({'ok': True}, Exception(ENTITLEMENT_400_MSG)))
    assert 'exit IP' in report['verdict']
    _run(client.close())


def test_degraded_check_uses_total_time_behind_proxy():
    report = {
        'config': {'insttoken_present': False, 'proxy': 'socks5h'},
        'reachability': {'reachable': True, 'connect_seconds': None,
                         'total_seconds': 0.8, 'via_proxy': True},
        'metadata': {'status': 'ok'},
        'search': {'status': 'ok'},
    }
    assert ScopusClient._build_verdict(report) == 'Connection and entitlement healthy.'
    report['reachability']['total_seconds'] = 6.0
    assert 'degraded' in ScopusClient._build_verdict(report)


# ---------------------------------------------------------------------------
# Real Windows Credential Manager (runs only on Windows, e.g. CI)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Credential Manager only')
def test_windows_credential_manager_round_trip(monkeypatch):
    import keyring  # a Windows dependency, so present on this platform
    import uuid

    monkeypatch.delenv('SCOPUS_DISABLE_SECRET_STORE', raising=False)
    monkeypatch.setattr(config, 'SECRET_SERVICE', f'scopus-mcp-test-{uuid.uuid4().hex}')
    try:
        # Two accounts under one service exercises keyring's compound
        # target naming, which is how api_key and insttoken coexist.
        keyring.set_password(config.SECRET_SERVICE, 'api_key', 'real-key')
        keyring.set_password(config.SECRET_SERVICE, 'insttoken', 'real-token')
        assert config.secret_store_lookup('api_key') == 'real-key'
        assert config.resolve_insttoken() == ('real-token', 'keyring')
    finally:
        for account in ('api_key', 'insttoken'):
            try:
                keyring.delete_password(config.SECRET_SERVICE, account)
            except Exception:
                pass
