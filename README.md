# Scopus MCP

An MCP server for literature and citation-network analysis. It gives Claude and
other MCP clients search, citation and reference retrieval, full text, and
bibliometric networks (bibliographic coupling, co-citation, citation lineage
with main-path analysis), from two data sources:

- **Scopus** (Elsevier API): the most complete source, but search, citations
  and references need your institution's Scopus subscription.
- **OpenAlex**: free and open, no subscription needed. Pass `source="openalex"`
  to the search, citation, reference and network tools.

Started as a fork of [qwe4559999/scopus-mcp](https://github.com/qwe4559999/scopus-mcp)
and now developed independently; see [Origins](#origins-and-credits).

[中文 (older upstream version)](README_CN.md) · [Prompt examples](USAGE_EXAMPLES.md) ·
[Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md)

## What it does

| Tool | What it returns | OpenAlex |
| --- | --- | --- |
| `search_scopus` | First page of a Scopus search | — |
| `search_all` | Every page of a search, up to `max_results`; large sets are written to JSON and CSV | ✓ |
| `get_abstract_details` | Full record for one document, abstract backfilled from OpenAlex or Crossref when Scopus withholds it | — |
| `resolve_identifier` | Scopus ID, EID, DOI and PII for any one of them | — |
| `get_author_profile` | Scopus author profile | — |
| `get_references` | Backward citations: the document's reference list | ✓ |
| `get_citing_papers` | Forward citations: documents citing it | ✓ |
| `get_fulltext` | Full text via ScienceDirect, then open access, then abstract, with provenance | — |
| `bibliographic_coupling` | Seed papers linked by shared references (the research front); GraphML, CSV, PNG | ✓ |
| `co_citation` | Seed papers linked by being cited together (the intellectual base); GraphML, CSV, PNG | ✓ |
| `publication_counts` | Publications per year for a query, e.g. to chart a topic's rise and fall | ✓ |
| `citation_lineage` | Multi-generation forward or backward walk with search-path-count main path; JSON, interactive HTML, PNG | ✓ |
| `diagnose_connection` | Which APIs your current access entitles, and which tools will fail | — |
| `get_quota_status`, `get_server_info` | Scopus quota; server version | — |

Graph files open in [VOSviewer](https://www.vosviewer.com/), Gephi or Pajek.
Output goes to `SCOPUS_MCP_OUTPUT_DIR` (default `~/scopus-mcp-output`).

### Choosing a source

Scopus and OpenAlex IDs differ, so never mix sources within one analysis.
With `source="openalex"`, IDs may be DOIs, OpenAlex work IDs (`W…`) or Scopus
IDs. Scopus IDs are resolved to a DOI through Scopus metadata, which needs no
subscription; records without a DOI are matched by exact title within one
year.

OpenAlex coverage is thinner. In a September 2026 check, a 2025 journal
article had 171 references in Scopus and 132 in OpenAlex, and AIS eLibrary
conference papers (ICIS, AMCIS) had no reference lists in OpenAlex at all, so
coupling on conference papers needs Scopus. OpenAlex search matches title and
abstract only; quote phrases (`"organizing vision"`).

## Install

Requires [uv](https://docs.astral.sh/uv/). Not yet on PyPI under its own
name: `uvx scopus-mcp` installs the older upstream package, without the
features above. Install from this repository instead, pinned to a commit so
upgrades are deliberate:

```json
{
  "mcpServers": {
    "scopus-assistant": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/michalhron/scopus-mcp.git@<commit-sha>",
        "scopus-mcp"
      ],
      "env": {
        "SCOPUS_API_KEY": "YOUR_KEY"
      }
    }
  }
}
```

Claude Desktop reads this from
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows). Other MCP clients
(Claude Code, Cursor) take the same command and arguments. Restart the client
after editing.

Get an API key at the [Elsevier Developer Portal](https://dev.elsevier.com/).
Register with an institutional email address; public email domains may be
rejected. The server needs a key
to start, even for OpenAlex-only use; with DOIs or OpenAlex IDs as input, no
Scopus calls are made.

## Configuration

Every setting can be an environment variable (in the client config's `env`
block) or a `config.json` field; the environment variable wins. Secrets can
also live in the OS secret store, which beats `config.json`
([below](#keep-secrets-out-of-config-files)).

| Environment variable | `config.json` field | Default | Purpose |
| --- | --- | --- | --- |
| `SCOPUS_API_KEY` | `api_key` | — | Elsevier API key. Required. |
| `SCOPUS_INSTTOKEN` | `insttoken` | — | Institutional token (`X-ELS-Insttoken`): subscriber access from any network. |
| `SCOPUS_PROXY` | `proxy` | — | Proxy for Elsevier traffic only, e.g. `socks5h://127.0.0.1:1080`. |
| `OPENALEX_API_KEY` | `openalex_api_key` | — | Free OpenAlex account key; raises the daily budget from $0.10 to $1. |
| `SCOPUS_MCP_OUTPUT_DIR` | — | `~/scopus-mcp-output` | Where result, graph and full-text files go. |
| `SCOPUS_PAGE_SIZE` | `page_size` | `25` | Records per Scopus request in `search_all` (1–200). |
| `SCOPUS_MAX_RETRIES` | `max_retries` | `2` | Retries for timeouts, 429 and 5xx; `0` disables. |
| `CACHE_TTL_SEARCH` | `cache_ttl_search` | `3600` | Search cache lifetime, seconds. |
| `CACHE_TTL_ABSTRACT` | `cache_ttl_abstract` | `2592000` | Abstract and reference cache lifetime. |
| `CACHE_TTL_AUTHOR` | `cache_ttl_author` | `604800` | Author cache lifetime. |
| `CACHE_TTL_DEFAULT` | `cache_ttl_default` | `86400` | Everything else. |
| `SCOPUS_DISABLE_SECRET_STORE` | — | — | Any value skips the OS secret-store lookup. |

`SCOPUS_PAGE_SIZE` above 25 works only for subscriber keys; others get
`400 INVALID_INPUT`. 200 cuts requests, and quota use, eightfold on large
searches.

### Keep secrets out of config files

The client config file is plain text, and Elsevier requires institutional
tokens to be kept in a password-protected store. The server reads
`api_key`, `insttoken` and `openalex_api_key` from the OS secret store under
service `scopus-mcp`, between the environment and `config.json`.

macOS Keychain (`-w` given last prompts for the value, keeping it out of shell
history):

```bash
security add-generic-password -U -s scopus-mcp -a insttoken -w
```

Windows Credential Manager (`keyring` is installed with the server on
Windows; the command prompts for the value):

```powershell
uvx --from keyring keyring set scopus-mcp insttoken
```

Linux needs a desktop session with GNOME Keyring or KWallet, the server
installed with the `keyring` extra, and the same `keyring set` command.

Restart the client afterwards; secrets are read once at startup.
`diagnose_connection` shows where each came from, never the value.

## Access off campus

Your API key identifies you; your institution's subscription entitles you.
Elsevier recognizes the subscription by the institution's IP range or by an
institutional token. Off campus without either, ID-based metadata keeps
working, while search, citations, references and full text fail. Search fails
misleadingly: every query, even `ALL(gene)`, returns
`400 "Error translating query"`, which reads like a syntax error.

**Run `diagnose_connection` first when Scopus misbehaves.** It checks
reachability, metadata, search, reference-list (REF view), full-text and
journal-metrics access, and lists the tools that cannot work with your current
access.

Ways to get subscriber access off campus:

1. **Institutional VPN.** No configuration needed.
2. **Institutional token** (`SCOPUS_INSTTOKEN`), requested through your
   library or Elsevier support and linked to your API key. Works from any
   network.
3. **SOCKS tunnel to an on-campus host**, where your institution permits it.
   Only Elsevier traffic uses it:

   ```bash
   ssh -N -D 1080 you@host.your-university.edu
   ```

   then set `SCOPUS_PROXY` to `socks5h://127.0.0.1:1080`.

Without any of these, use `source="openalex"`.

## Development

```bash
uv run --extra dev pytest
```

The offline suite mocks every network call; CI runs it on Linux, macOS and
Windows. Tests never read your real secret store. Tests marked `integration`
call the live APIs and run only with `pytest -m integration`.

Mocked tests prove logic, not the live API's shape: search paging, the
tool-return size limit, the REF-view parser and reference-list paging all
passed mocks and failed live. Smoke-test every new tool against the real API before trusting it.

## Origins and credits

This project began as a fork of
[qwe4559999/scopus-mcp](https://github.com/qwe4559999/scopus-mcp) by
[thinktraveller](https://github.com/thinktraveller) (initial work) and
[qwe4559999](https://github.com/qwe4559999) (maintainer) with contributors,
which provides
Scopus search, abstracts, author profiles and citing papers. Everything from
version 0.2 on — reference retrieval, the network and lineage tools, full
text, OpenAlex, diagnostics and off-campus access — was developed here by
[Michal Hron](https://github.com/michalhron). Fixes suitable for upstream are
offered there as pull requests.

MIT licensed; see [LICENSE](LICENSE).
