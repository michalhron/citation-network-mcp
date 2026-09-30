"""Suite-wide isolation from the developer's machine.

Credential and proxy lookups read the real environment and the OS secret
store (macOS Keychain, Windows Credential Manager, Linux Secret Service). A
token stored there for daily use would otherwise leak into every test that
builds a ScopusClient, flipping insttoken assertions and sending the real
token to mocked transports. Tests that exercise these lookups opt back in
explicitly.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_credentials(monkeypatch):
    monkeypatch.setenv('SCOPUS_DISABLE_SECRET_STORE', '1')
    for var in ('SCOPUS_INSTTOKEN', 'ELSEVIER_INSTTOKEN', 'SCOPUS_PROXY'):
        monkeypatch.delenv(var, raising=False)
