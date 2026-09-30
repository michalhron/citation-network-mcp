# Changelog

All notable changes to this fork. Versions follow `pyproject.toml`; no tags
are pushed yet (see "Release process" in ROADMAP.md).

## [Unreleased]

### Added
- `diagnose_connection` probes per-API capabilities: REF-view references,
  ScienceDirect full text (a subscription canary, so open access cannot pass
  for entitlement) and Serial Title. Reports `unavailable_tools`, the tools
  that cannot work with the current access.
- OpenAlex backend: `source="openalex"` on `search_all`, `get_citing_papers`,
  `get_references`, `bibliographic_coupling` and `co_citation`. Needs no
  Scopus entitlement. Accepts DOIs, OpenAlex work IDs, or Scopus IDs
  (resolved to a DOI through Scopus metadata, or by exact title within one
  year when the record has no DOI). Search matches title and abstract.
- Optional `OPENALEX_API_KEY` (environment, OS secret store account
  `openalex_api_key`, or `config.json`), sent as a Bearer header. A free key
  raises OpenAlex's daily budget from $0.10 to $1; errors report the
  remaining budget.
- Results CSV gains `openalex_id` and `source` columns.
- README rewritten for this project as an independent fork, with upstream
  credited under "Origins and credits".

### Fixed
- `get_references` returned at most 40 references (the REF view's page
  size), which also truncated `bibliographic_coupling` and backward
  `citation_lineage`. It now pages through the full list. Re-run coupling
  networks built before this fix.
- Scopus `bibliographic_coupling` labelled seeds by ID when the REF view
  carried no title; it now falls back to the abstract.

## [0.8.1] - 2026-09-30

### Added
- Secrets (`SCOPUS_INSTTOKEN`, `SCOPUS_API_KEY`) read from the OS secret store:
  macOS Keychain via `security`, Windows Credential Manager and Linux Secret
  Service via `keyring`. Order: environment, secret store, `config.json`.
- `SCOPUS_PROXY`: routes `api.elsevier.com` traffic only through an
  http/https/socks5/socks5h proxy, e.g. an `ssh -D` tunnel to campus.
- `diagnose_connection` reports credential sources, proxy scheme and
  `entitlement_via`, with verdicts for an unlinked token and an unentitled
  proxy exit IP.
- CI matrix on Ubuntu, macOS and Windows, Python 3.10 and 3.12.

### Changed
- A 401 with an insttoken configured says the token may be the cause.

## [0.8.0] - 2026-08-09

### Added
- `diagnose_connection`: config, reachability, metadata and search
  entitlement checks with a one-line verdict.
- Bounded retries with jittered backoff for timeouts, 429 and 5xx.
- `SCOPUS_PAGE_SIZE` for `search_all`.

### Fixed
- `search_all` paging always terminates.
- "Error translating query" 400s flagged as a likely entitlement problem.
- Test event-loop leak; live-network tests deselected by default.

## [0.7.6] - 2026-07-28

### Fixed
- Pin the `mcp` SDK below 2.0 to restore installability.
- Abstract fallback (OpenAlex, Crossref) for keys without abstract
  entitlement.

## [0.7.5] - 2026-06-25

### Added
- `citation_lineage`: `sort` parameter, degeneracy guard, inline corpus.
- `get_server_info` and server version in responses.

### Fixed
- Lineage DAG edges restricted to adjacent generations.
- Canonical Batagelj search-path-count (SPC) main path.

## [0.7.1] - 2026-06-24

### Added
- `citation_lineage` forward and backward walker with SPC main path, D3 HTML
  and layered PNG renderings.
- `bibliographic_coupling` and `co_citation` network builders (GraphML, CSV).
- `search_all` multi-page aggregation with file output for large results.
- `resolve_identifier` and `get_references` (REF view).
- `get_fulltext`: ScienceDirect, open access, then abstract.

### Fixed
- `get_citing_papers` uses `REF(EID)`, so forward citations return results.
- REF-view reference parser matches the real response structure.

## [0.1.8] and earlier - 2026-01-13

Upstream releases by qwe4559999 and thinktraveller: search, abstracts, author
profiles, `get_citing_papers`, MCP prompts, PyPI publishing.
