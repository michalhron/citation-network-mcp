# Configuration

Every setting can be an environment variable (in your MCP client's `env`
block) or a field in `config.json` at the project root; the environment
variable wins. The three secrets can also live in the OS secret store, which
sits between the two.

| Environment variable | `config.json` field | Default | Purpose |
| --- | --- | --- | --- |
| `SCOPUS_API_KEY` | `api_key` | — | Elsevier API key. Required. |
| `SCOPUS_INSTTOKEN` | `insttoken` | — | Institutional token (`X-ELS-Insttoken`): subscriber access from any network. |
| `SCOPUS_PROXY` | `proxy` | — | Proxy for Elsevier traffic only, e.g. `socks5h://127.0.0.1:1080`. See [access](access.md). |
| `OPENALEX_API_KEY` | `openalex_api_key` | — | Free OpenAlex account key; raises the daily budget from $0.10 to $1. |
| `CONTACT_EMAIL` | `contact_email` | — | Your email, sent to open scholarly APIs that ask for one (OpenAlex, arXiv, Semantic Scholar) and required by Unpaywall. Unset by default: no email is sent. |
| `CORE_API_KEY` | `core_api_key` | — | Free key from core.ac.uk; adds CORE's repository copies to open-access full-text lookups. |
| `SEMANTIC_SCHOLAR_API_KEY` | `semantic_scholar_api_key` | — | Optional; raises Semantic Scholar's rate limit for open-access lookups and `citation_context`. |
| `SCOPUS_MCP_OUTPUT_DIR` | — | `~/scopus-mcp-output` | Where result, graph, BibTeX and full-text files go. |
| `SCOPUS_PAGE_SIZE` | `page_size` | `25` | Records per Scopus request in `search_all` (1–200). |
| `SCOPUS_MAX_RETRIES` | `max_retries` | `2` | Retries for timeouts and 5xx; `0` disables. |
| `SCOPUS_RATE_LIMIT_RETRIES` | `rate_limit_retries` | `5` | Retries after 429 (rate limited): `Retry-After` or `X-RateLimit-Reset` when sent, else backoff with jitter, 30 to 60 s in all. A spent quota fails at once. |
| `SCOPUS_SYNC_BUDGET` | — | `45` | Seconds a long tool (`citation_network`, `resolve_citers`, lineage, coupling) runs before it returns a job ID and continues in the background (`job_status`, `job_result`). Keep it under your client's timeout (Claude Desktop: 60 s). |
| `SCOPUS_COMPLETENESS_RATIO` | — | `0.9` | A reference list is SHORT below this share of the comparison count... |
| `SCOPUS_COMPLETENESS_MIN_MISSING` | — | `5` | ...and with at least this many references missing (both must hold). |
| `CACHE_TTL_SEARCH` | `cache_ttl_search` | `3600` | Search cache lifetime, seconds. |
| `CACHE_TTL_ABSTRACT` | `cache_ttl_abstract` | `2592000` | Abstract and reference cache lifetime. |
| `CACHE_TTL_AUTHOR` | `cache_ttl_author` | `604800` | Author cache lifetime. |
| `CACHE_TTL_DEFAULT` | `cache_ttl_default` | `86400` | Everything else. |
| `SCOPUS_DISABLE_SECRET_STORE` | — | — | Any value skips the OS secret-store lookup. |

`SCOPUS_PAGE_SIZE` above 25 works only for subscriber keys; others get
`400 INVALID_INPUT`. At 200, large searches need eight times fewer requests.

## Keep secrets out of config files

MCP client config files are plain text, and Elsevier requires institutional
tokens to be kept in a password-protected store. The server reads `api_key`,
`insttoken` and `openalex_api_key` from the OS secret store under the service
name `scopus-mcp` (the project's former name, kept so existing entries keep
working). Each command below prompts for the value, so it never lands in
your shell history.

**macOS** (Keychain):

```bash
security add-generic-password -U -s scopus-mcp -a insttoken -w
```

**Windows** (Credential Manager; `keyring` is installed with the server):

```powershell
uvx --from keyring keyring set scopus-mcp insttoken
```

**Linux** (Secret Service): needs a desktop session with GNOME Keyring or
KWallet, and the server installed with the `keyring` extra; then the same
`keyring set` command.

Use the account names `api_key`, `insttoken` or `openalex_api_key`. Restart
your MCP client afterwards, since secrets are read once at startup.
`diagnose_connection` reports where each secret came from, never its value.
