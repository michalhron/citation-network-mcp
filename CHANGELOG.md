# Changelog

All notable changes to this fork. Versions follow `pyproject.toml`; no tags
are pushed yet (see "Release process" in ROADMAP.md).

## [Unreleased]

## [0.12.0] - 2026-09-30

### Added
- `get_journal_metrics` reports each journal's CiteScore percentile, rank and
  quartile in every subject category it belongs to, from the latest complete
  CiteScore year, and its best quartile.
- `find_journals`: journals in chosen ASJC categories (names or codes) at or
  above a percentile in that category (Q1 = 75, top 10% = 90), with CSV and
  ready `SRCID(...)` query fragments for scoping a review. Ambiguous category
  names return the candidates.
- `topic_landscape`: for a Scopus query, papers per broad subject area over
  all results, and per subject category the papers in Q1, Q2, Q3 and Q4
  journals of that category, the main journals, and the share in unranked
  venues such as conference proceedings.
- `search_fulltext`: full-text search of Elsevier (ScienceDirect) articles,
  paged up to 1,000 results. With `context=true` it retrieves the top
  results' full texts and reports body mentions separately from the
  reference list, their positions through the article, and example
  sentences, so citing without using a term is visible.

### Changed
- ScienceDirect searches (PUT with a JSON body) are cached and retried like
  GET requests.
- Installs from PyPI: `uvx citation-network-mcp` in the README and the
  Claude Code plugin, now that 0.11.0 is on PyPI and in the MCP registry.

## [0.11.0] - 2026-09-30

### Changed
- Renamed to **citation-network-mcp** (repository
  `michalhron/citation-network-mcp`; the old URL redirects). The new name
  describes what sets the project apart and no longer implies Scopus only.
  Kept for compatibility: the `scopus-mcp` command (alias), the `scopus_mcp`
  import package, `SCOPUS_*` settings, the `scopus-mcp` secret-store service,
  and the cache and output folders.
- Claude Desktop extension and Claude Code plugin renamed to match
  (`citation-network-mcp@michalhron`); reinstall them under the new name.
- Publishing: on a version tag, `publish.yml` uploads to PyPI with trusted
  publishing and lists the server in the MCP registry
  (`io.github.michalhron/citation-network-mcp`).

### Removed
- Upstream's Chinese README, its Chinese prompt guide (replaced by an English
  `docs/examples.md`) and its outdated `MCP_tool_config.json`.

## [0.10.0] - 2026-09-30

### Changed
- PyPI publishing is manual only: the tag trigger would have published
  under upstream's package name.

### Added
- Claude Desktop extension (`.mcpb`): one-click install that asks for the
  API key and stores it securely. Built by `scripts/build_extension.py`
  and attached to GitHub Releases by `release.yml` on version tags.
- Claude Code plugin: `claude plugin marketplace add michalhron/scopus-mcp`,
  then `claude plugin install scopus-mcp@michalhron`.
- The server reports its own version to clients and sends usage
  instructions (diagnose first, OpenAlex fallback, one source per analysis).
- `search_authors`: find authors by name, optionally narrowed by
  affiliation. Scopus returns author IDs for `get_author_profile`, document
  counts, affiliation, subject areas and name variants; OpenAlex returns
  ORCID, h-index, institution and topics.

## [0.9.0] - 2026-09-30

### Changed
- One version source: `scopus_mcp.__version__`. `pyproject.toml` reads it
  (hatch dynamic version); the server and all User-Agent headers import it.
- README shortened to an overview; details moved to `docs/` (tools, data
  sources, configuration, access, comparison, development).
  `docs/tools.md` is generated from the tool definitions, and a test
  fails when it is stale or a relative link breaks.

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
- `get_journal_metrics`: SJR, SNIP, CiteScore and CiteScore Tracker (with
  years) plus subject areas for up to 200 journals, by ISSN or Scopus source
  ID, written to CSV. Source IDs are mapped to ISSNs through Scopus search,
  because the Serial Title API silently ignores them. Unmatched journals
  are listed, not dropped. `source="openalex"` returns OpenAlex's own
  measures (2-year mean citedness, h-index, i10-index).
- `get_bibtex`: BibTeX for up to 200 DOIs, Scopus IDs or OpenAlex IDs via DOI
  content negotiation, written to a `.bib` file. Page ranges use `--`,
  repeated keys get a/b suffixes, and one paper given twice appears once.
  Papers without a DOI get an entry generated from Scopus or OpenAlex
  metadata, marked in its `note` field.
- `citation_lineage` accepts `source="openalex"`: forward walks OpenAlex's
  citing works, backward walks its reference lists, with OpenAlex work IDs
  as node keys. `sort="relevancy"` stays Scopus-only.
- `publication_counts`: publications per year for a query. Scopus runs one
  small search per year (range required, at most 60 years); OpenAlex one
  grouped request. Missing years show as zero; the current year is
  flagged as incomplete.
- Results CSV gains `openalex_id` and `source` columns.
- README rewritten for this project as an independent fork, with upstream
  credited under "Origins and credits".

### Fixed
- `get_fulltext` labelled any ScienceDirect body over 500 characters as
  full text, so an abstract with metadata could pass. It now uses the same
  5,000-character bar as `diagnose_connection`; shorter bodies fall through
  to open access and then the abstract.
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
