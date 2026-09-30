import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlsplit
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Define base paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / 'config.json'

def load_config_file() -> Dict[str, Any]:
    """Loads the configuration from config.json if it exists."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

# OS secret-store service holding the secrets, one account per secret
# ('api_key', 'insttoken').  Store them with, on macOS:
#   security add-generic-password -U -s scopus-mcp -a insttoken -w
# (-w last with no value prompts, keeping the secret out of shell history);
# on Windows or Linux:
#   keyring set scopus-mcp insttoken
SECRET_SERVICE = 'scopus-mcp'


def secret_store_label() -> str:
    """Source label reported by diagnostics for the platform's secret store."""
    return 'keychain' if sys.platform == 'darwin' else 'keyring'


def _macos_keychain_lookup(account: str) -> Optional[str]:
    # The `security` CLI rather than the keyring library: an item created by
    # `security add-generic-password` trusts that binary, so reading it back
    # never raises a Keychain permission prompt.  Via keyring, the trusted app
    # would be a Python interpreter whose path changes each time uvx rebuilds
    # its environment, re-prompting after every upgrade.
    security = shutil.which('security') or '/usr/bin/security'
    try:
        result = subprocess.run(
            [security, 'find-generic-password',
             '-s', SECRET_SERVICE, '-a', account, '-w'],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _keyring_lookup(account: str) -> Optional[str]:
    # Windows Credential Manager (keyring is a dependency there) or, when the
    # optional extra is installed, the Linux Secret Service.
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(SECRET_SERVICE, account) or None
    except Exception:
        # No backend (headless Linux), locked store, or backend failure.
        return None


def secret_store_lookup(account: str) -> Optional[str]:
    """
    Reads a secret from the OS secret store: the macOS login Keychain, the
    Windows Credential Manager, or the Linux Secret Service.

    Returns None when the store is unavailable, the item is missing, or on any
    error, so a secret-store problem degrades to "not configured" instead of
    crashing startup.  SCOPUS_DISABLE_SECRET_STORE=1 skips the lookup.  The
    secret is never logged.
    """
    if os.getenv('SCOPUS_DISABLE_SECRET_STORE'):
        return None
    if sys.platform == 'darwin':
        return _macos_keychain_lookup(account)
    return _keyring_lookup(account)


def _resolve(env_vars: Tuple[str, ...], account: str) -> Tuple[Optional[str], Optional[str]]:
    for var in env_vars:
        value = os.getenv(var)
        if value:
            return value, 'env'
    value = secret_store_lookup(account)
    if value:
        return value, secret_store_label()
    value = load_config_file().get(account)
    if value:
        return value, 'config'
    return None, None


def resolve_api_key() -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (api_key, source), source being 'env', 'keychain' (macOS),
    'keyring' (Windows/Linux) or 'config'.  Precedence:
    1. Environment variable 'SCOPUS_API_KEY'
    2. OS secret store, service 'scopus-mcp', account 'api_key'
    3. config.json 'api_key' field
    """
    return _resolve(('SCOPUS_API_KEY',), 'api_key')


def get_api_key() -> str:
    """Retrieves the API key (see resolve_api_key for precedence)."""
    api_key, _ = resolve_api_key()
    if not api_key:
        raise ValueError(
            "Scopus API Key not found. Please set the 'SCOPUS_API_KEY' environment variable, "
            "store it in the OS secret store (service 'scopus-mcp', account 'api_key'), "
            "or add 'api_key' to config.json."
        )
    return api_key


def resolve_insttoken() -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (insttoken, source), labelled as in resolve_api_key.  Precedence:
    1. Environment variable 'ELSEVIER_INSTTOKEN' or 'SCOPUS_INSTTOKEN'
    2. OS secret store, service 'scopus-mcp', account 'insttoken'
    3. config.json 'insttoken' field
    Returns (None, None) when not configured.
    """
    return _resolve(('ELSEVIER_INSTTOKEN', 'SCOPUS_INSTTOKEN'), 'insttoken')


def get_insttoken() -> Optional[str]:
    """Retrieves the optional institutional token (see resolve_insttoken)."""
    token, _ = resolve_insttoken()
    return token


def resolve_openalex_key() -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (key, source) for the optional OpenAlex API key, labelled as in
    resolve_api_key.  Precedence:
    1. Environment variable 'OPENALEX_API_KEY'
    2. OS secret store, service 'scopus-mcp', account 'openalex_api_key'
    3. config.json 'openalex_api_key' field

    OpenAlex works without a key on a small anonymous daily budget; a free
    account key raises it tenfold.
    """
    return _resolve(('OPENALEX_API_KEY',), 'openalex_api_key')


def get_contact_email() -> Optional[str]:
    """
    Optional contact email sent to open scholarly APIs, which ask for one
    (OpenAlex's polite pool, arXiv and Semantic Scholar etiquette) or
    require it (Unpaywall). Environment variable 'CONTACT_EMAIL', else
    config.json 'contact_email'. None when unset or not an address: no email
    is ever sent on a user's behalf by default.
    """
    email = (os.getenv('CONTACT_EMAIL') or load_config_file().get('contact_email') or '').strip()
    return email if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) else None


def contact_user_agent(base: str) -> str:
    """User-Agent with a mailto: when a contact email is configured."""
    email = get_contact_email()
    return f"{base} (mailto:{email})" if email else base


def resolve_core_key() -> Tuple[Optional[str], Optional[str]]:
    """Optional CORE API key (core.ac.uk, free): 'CORE_API_KEY', the OS secret
    store account 'core_api_key', or config.json 'core_api_key'."""
    return _resolve(('CORE_API_KEY',), 'core_api_key')


def resolve_semantic_scholar_key() -> Tuple[Optional[str], Optional[str]]:
    """Optional Semantic Scholar API key, which raises its rate limit:
    'SEMANTIC_SCHOLAR_API_KEY', the OS secret store account
    'semantic_scholar_api_key', or config.json 'semantic_scholar_api_key'."""
    return _resolve(('SEMANTIC_SCHOLAR_API_KEY',), 'semantic_scholar_api_key')


PROXY_SCHEMES = ('http', 'https', 'socks5', 'socks5h')


def get_proxy() -> Optional[str]:
    """
    Retrieves the optional proxy for Elsevier API traffic:
    1. Environment variable 'SCOPUS_PROXY'
    2. config.json 'proxy' field

    Routing only api.elsevier.com requests through a host on the
    institutional network (e.g. `ssh -D 1080 host` then
    'socks5h://127.0.0.1:1080') gives them an entitled IP without a full VPN.
    OpenAlex, Crossref and Unpaywall calls are not proxied.

    Raises ValueError for an unsupported scheme so a typo fails loudly at
    startup instead of silently bypassing the proxy.
    """
    proxy = os.getenv('SCOPUS_PROXY') or load_config_file().get('proxy')
    if not proxy:
        return None
    scheme = urlsplit(proxy).scheme.lower()
    if scheme not in PROXY_SCHEMES:
        raise ValueError(
            f"Unsupported SCOPUS_PROXY scheme {scheme!r}; "
            f"use one of {', '.join(PROXY_SCHEMES)}."
        )
    return proxy


def proxy_scheme(proxy: Optional[str]) -> Optional[str]:
    """Scheme of a proxy URL, safe to report (host and credentials omitted)."""
    return urlsplit(proxy).scheme.lower() if proxy else None


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200


def get_page_size() -> int:
    """
    Retrieves the per-request page size used by ScopusClient.search_all:
    1. Environment variable 'SCOPUS_PAGE_SIZE'
    2. config.json 'page_size' field
    3. Default 25

    25 is the 'count' ceiling for non-institutional Scopus keys — asking for
    more returns 400 INVALID_INPUT.  Institutional (subscriber) keys accept up
    to 200, which cuts request count, and therefore quota burn, by 8x.  That
    entitlement cannot be detected from the key or from the presence of an
    insttoken, so raising the page size is an explicit opt-in.

    Non-numeric values fall back to the default; numeric values are clamped to
    1..200.
    """
    config = load_config_file()
    raw = os.getenv('SCOPUS_PAGE_SIZE', config.get('page_size', DEFAULT_PAGE_SIZE))
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return max(1, min(size, MAX_PAGE_SIZE))


def get_max_retries() -> int:
    """
    Retrieves the maximum retry count for transient request failures:
    1. Environment variable 'SCOPUS_MAX_RETRIES'
    2. config.json 'max_retries' field
    Defaults to 2 (3 attempts total); 0 disables retries.
    """
    raw = os.getenv('SCOPUS_MAX_RETRIES')
    if raw is None:
        config = load_config_file()
        raw = config.get('max_retries')
    if raw is None:
        return 2
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 2


def get_cache_config() -> Dict[str, int]:
    """
    Retrieves cache expiration settings from env vars or config.json.
    Defaults:
    - Search: 1 hour (3600s)
    - Abstract: 30 days (2592000s)
    - Author: 7 days (604800s)
    - Default: 24 hours (86400s)
    """
    config = load_config_file()
    
    return {
        'search': int(os.getenv('CACHE_TTL_SEARCH', config.get('cache_ttl_search', 3600))),
        'abstract': int(os.getenv('CACHE_TTL_ABSTRACT', config.get('cache_ttl_abstract', 2592000))),
        'author': int(os.getenv('CACHE_TTL_AUTHOR', config.get('cache_ttl_author', 604800))),
        'default': int(os.getenv('CACHE_TTL_DEFAULT', config.get('cache_ttl_default', 86400)))
    }
