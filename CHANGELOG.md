# Changelog

All notable changes to this fork. Versions follow `pyproject.toml`; no tags
are pushed yet (see "Release process" in ROADMAP.md).

## [Unreleased]

## [0.23.0] - 2026-09-30

### Added
- `import_records`: reads saved exports (Scopus CSV, RIS, BibTeX; Web of
  Science plain text and tab-delimited; format detected), merges records
  across files by Scopus ID, WoS ID, DOI or title and year, and looks up
  Scopus IDs for the rest by DOI, then exact title and year. Writes a
  corpus file and returns its Scopus IDs. Only export file types are read
  (.csv, .txt, .ris, .bib, .tsv; up to 50 MB).
- `corpus_file` on `citation_network`, `rpys`, `historiograph`,
  `research_fronts` and `thematic_evolution`: analyse exactly the imported
  records again later. `thematic_evolution` takes keywords and abstracts
  from the file itself, with no API calls.

## [0.22.0] - 2026-09-30

### Added
- `research_fronts`: Louvain communities of a paper set's direct-citation
  network (as in CitNetExplorer), each with its years, density, core papers
  (most cited within the set) and distinguishing keywords (count x log
  lift against the whole set). Reports the front of every global
  main-path paper, the hops between fronts, and for each hop at how many of
  the resolutions 0.5, 1.0 and 1.5 it persists. JSON, and Pajek .net with
  a .clu partition (VOSviewer and Pajek colour by it).

## [0.21.0] - 2026-09-30

### Added
- `thematic_evolution`: themes of a corpus per period and how they change
  (Cobo et al. 2011; bibliometrix's thematic map and evolution). Keyword
  co-occurrence normalised by the equivalence index and clustered with
  Louvain; each theme placed by Callon centrality and density (motor,
  basic, niche, emerging or declining); themes of consecutive periods
  linked by the inclusion index (continues, splits, merges, new,
  vanishes). `construct_terms` follows a construct: its theme, that
  theme's quadrant and the keywords it co-occurs with, per period. Terms:
  author keywords (index terms where a paper has none), all keywords, or
  two- and three-word phrases from title and abstract (copyright lines and
  academic boilerplate removed; "IT" kept as an acronym). Periods by cut
  years or by size. JSON, CSV and a PNG of the strategic diagrams, with the
  construct's themes starred.

## [0.20.0] - 2026-09-30

### Added
- `rpys`: Reference Publication Year Spectroscopy (Marx et al. 2014) of a
  paper set: cited references per year, deviation from the five-year
  median, the peak years and the works cited most in each; CSV, JSON, PNG.
  Years come from the FULL view's bibliography, because the REF view leaves
  most older references undated (63 of 73 for Swanson & Ramiller 1997);
  on the 113 organizing-vision papers 39 of 10,290 references stay undated.
- `historiograph`: Garfield's historiograph: the papers most cited within
  the set on a time axis, citations among them, the global main path
  highlighted; PNG, Pajek, JSON, and a list with local and global citation
  counts.
- `docs/comparison.md`: a second table, against bibliometrics packages
  (bibliometrix, pybliometrics, metaknowledge, litstudy, VOSviewer /
  CitNetExplorer, Pajek / MainPath), with where each side leads. The Scopus
  MCP table gains rows for the audit tools and current counts.

## [0.19.0] - 2026-09-30

### Added
- Main-path variants in `citation_network` (roadmap "From established
  bibliometrics tools", item 1). `weight`: SPC (search path count, the
  default), SPLC (search path link count: paths may start at any paper) or
  SPNP (search path node pair: paths between any two papers), after
  Batagelj 2003 and Liu & Lu 2012. Every run now reports the local
  forward, local backward and global main paths.
- `key_route_search`: 'local' (heaviest adjoining edge, as before) or
  'global' (heaviest whole path to and from each key edge).
- `robustness` (default on): the global main path under all three weights,
  the papers they share and their pairwise overlap. On the
  organizing-vision network (113 papers) the global path is identical
  under SPC, SPLC and SPNP; the local backward path agrees up to Wang 2010
  and then runs through Nielsen 2014 and Gal 2022.
- ROADMAP: six features from bibliometrix, metaknowledge, Pajek/MainPath
  and CitNetExplorer, with the ones deliberately left out.

## [0.18.0] - 2026-09-30

Roadmap items A to D from the 0.17.0 field test: the main-path transmission
audit, done by hand in the field test, as tools.

### Added
- **A.** `citation_context` falls back to the citing paper's full text when
  Semantic Scholar has no usable sentences: ScienceDirect when entitled,
  else the open-access waterfall. The cited work is found in the reference
  list (surname, year, title words) and the sentences carrying an
  author-year or numeric marker are returned; running headings and leaked
  bibliography text are filtered out. Every result reports
  `context_source` (`semantic_scholar`, `fulltext_sciencedirect`,
  `fulltext_oa`, `none`). Live: Swanson 2010 (JSIS) citing Swanson &
  Ramiller 1997, which Semantic Scholar does not record, now returns two
  sentences from ScienceDirect. AIS eLibrary refuses automated downloads
  (HTTP 403), so JAIS full text is usually unavailable.
- **B.** `path_transmission(path_ids, construct_terms)`: per main-path edge,
  the contexts (S2, then full text), intent and influential flags, how many
  contexts name the construct in the cited work's own clause (so "fashions
  (Wang, 2010), and organizing visions (Ramiller & Swanson, 2003)" does not
  credit Wang), and list citations (three or more works in one parenthesis,
  or enumerated in one sentence). A draft label per edge (substantive,
  construct-shifted, hollow, unresolved) with its evidence, stated to be a
  heuristic for a human coder. On the organizing-vision main path: 1
  substantive, 2 construct-shifted, 2 hollow, 4 unresolved.
- **C.** `index_coverage(seed_ids, scope)`: citers of the seeds in Scopus,
  OpenAlex and Semantic Scholar under one scope, matched by DOI, else title
  and year, with the seven-region overlap.
- **D.** `path_transmission` writes a CSV coding sheet (UTF-8 with BOM, so
  Excel opens it): edge, citing, cited, SPC (from a `citation_network`
  corpus file), source, intents, influential, contexts, draft label,
  evidence, and blank columns for two coders.

## [0.17.1] - 2026-09-30

Fixes from the 0.17.0 field test (organizing-vision network, Basket of
Eight). Numbers refer to the field-test report. The full run now completes
without workarounds: `resolve_citers` (2 seeds, phrase query, scope, verify)
in about 50 s, `citation_network` on the 113 confirmed papers in about 30 s,
`citation_context` on the 9 main-path edges in about 30 s. The global main
path is unchanged: Swanson 1997, Ramiller 2003, Swanson 2004, Ramiller 2008,
Baskerville 2009, Wang 2010, Kohli 2019, Wang 2021, Miranda 2022, Swanson 2025.

### Fixed
- **1.** `'list' object has no attribute 'lower'` crashed `resolve_citers`
  (verify) and `citation_network` on Flynn 2012, Hoefnagel 2014, Lyytinen
  2015 and Mamonov 2021. Where Scopus merges duplicate references it sends
  `ce:doi` as a list of `{'$': doi}` nodes. Every reference field now goes
  through one normalizer (`records.scalar`); the four REF-view responses are
  test fixtures.
- **2.** One bad paper no longer aborts a batch. Load failures go to
  `reference_fetch_errors`, parse failures to `paper_errors` (ID, exception
  type, message), both listed at the top of the reply; `resolve_citers`
  reports `verification_failed` hits separately, never as confirmed or
  unconfirmed.
- **3.** 429s have their own retry budget (`SCOPUS_RATE_LIMIT_RETRIES`,
  default 5, about 30 to 60 s) honouring `Retry-After` and
  `X-RateLimit-Reset`; a spent quota fails at once. Papers still
  rate-limited get a final sequential pass; if any list is still missing the
  reply says `PROVISIONAL: main path computed with N of M reference lists
  missing`. Empty quota headers read "quota headers not returned".
- **4.** `refs_retrieved` was one less than `refs_reported` for nearly every
  paper. Not our paging: the Scopus REF view never serves the last
  reference (startref is 1-based; asking for entry N of N is a 400). The
  missing tail now comes from the FULL view's bibliography (108 references
  recovered in the 113-paper network), marked `recovered_from: FULL`;
  anything still unavailable is counted as `refs_unparseable`. This also
  confirms more citers: "Swanson" sorts late, so the dropped last reference
  was often the seed (REF(1997): 104 of 105 confirmed, was 101 of 104).
- **5.** Completeness falls back from Crossref to OpenAlex
  `referenced_works_count` and Semantic Scholar `referenceCount`, with
  `completeness_source` per paper (unknown: 4 of 113, was 51 of 111). The
  SHORT rule is explicit and configurable (below 90% of the comparison count
  and at least 5 missing; `SCOPUS_COMPLETENESS_RATIO`,
  `SCOPUS_COMPLETENESS_MIN_MISSING`). Main-path papers get their own line.
- **6.** `resolve_citers` `cross_check` (on when scoped) asks OpenAlex and
  Semantic Scholar for in-scope citers and lists those Scopus misses, with
  Scopus's reference count against the external one, grouped by cause: in
  Scopus but missed by the searches (Miranda 2015: Scopus holds 32 of ~86
  references), not in Scopus, and OpenAlex-only records without DOI (often
  conference papers OpenAlex files under the journal). Never merged into the
  Scopus result.
- **7.** `citation_context` resolves papers by DOI, MAG ID, then title and
  year, and reports the route. Swanson & Ramiller 2004 (JSTOR DOI) sits in
  Semantic Scholar under an ACM DOI and is now found by title. `not_found` is
  split into `citing_paper_unresolved`, `cited_paper_unresolved`,
  `edge_absent_in_s2` and `contexts_withheld`; DOI and Scopus-ID inputs give
  the same answer.
- Basket of Eight: JAIS records in Scopus carry either 1536-9323 or
  1558-3457; the second was missing from the basket.

### Added
- **8.** Long calls (`citation_network`, `resolve_citers`, lineage,
  coupling, co-citation) return a job ID after `SCOPUS_SYNC_BUDGET` seconds
  (default 45) and keep running; new tools `job_status` and `job_result`.
  Responses are cached as they arrive, so a timed-out call resumes.
- **9.** `citation_network` `inline='nodes'`: one compact line per paper
  plus the edges (under 25k characters for 150 papers); `inline='full'` is
  paged (`page`, `page_size`). `resolve_citers` compact lines are shorter
  and include the seeds found in each hit's references.
- **10.** Key-route line: "top 10 SPC edges extend into 5 distinct routes
  (15 papers, 18 edges)".
- **11.** `citation_context` `max_contexts` (default 3) and
  `construct_terms`; contexts are cleaned of running headers and
  citation-free text and ranked (naming the cited authors or the construct
  first).
- **12.** `resolve_citers` per-strategy table: hits, confirmed, unconfirmed,
  verification failed, confirmed citers missed.
- **13.** `citation_network` lists `possible_duplicates` (same DOI, or same
  normalized title and year).

### P3, carried over from the pre-0.17 test
- Already fixed in 0.17.0: `get_references` truncation flag and totals;
  `search_all` `inline='compact'|'full'`; "Error translating query" told
  apart from missing entitlement.
- Fixed now: an unauthorized REF view says you are probably off the
  institutional network and names `SCOPUS_INSTTOKEN`; `diagnose_connection`
  recommends the token when reference lists are unavailable.

## [0.17.0] - 2026-09-30

### Added
- `citation_network`: the direct-citation network within a set of papers
  (IDs or a query) in one call, with SPC main path (local and global),
  key-route main paths (Liu & Lu 2012) and a completeness flag per paper.
  Writes the corpus JSON, a Pajek `.net` (Pajek, VOSviewer, Gephi) and an
  edge CSV; edges come back inline too. Replaces one `get_references` call
  per paper.
- `resolve_citers`: citers of one or more seeds by several strategies at
  once (REF() per seed plus any extra queries), merged and verified against
  each hit's own reference list; reports what each strategy found and
  missed, and the hits that cite no seed.
- `citation_context`: the sentences in which one paper cites another, with
  Semantic Scholar's intent labels and influential-citation flag. Falls back
  to the cited paper's citation list when the publisher has Semantic Scholar
  withhold the citing paper's references (Wiley, Taylor & Francis).
- `get_references`: `filter_ids` returns only the references to papers in a
  given list; `check_completeness` compares the list with the reference
  count the publisher deposited at Crossref.
- `scope` on `search_all`, `citation_lineage` (forward), `citation_network`
  and `resolve_citers`: an ISSN list or `basket_of_eight` / `ais8`. ISSNs
  rather than journal names, which Scopus spells inconsistently.
- `search_all` `inline='compact'` returns every record as one JSON line of
  key fields, for callers that cannot read the server's files (cloud
  sessions); `inline='full'` returns them in full. Records carry `issn`.
- `citation_lineage` also writes a Pajek `.net` with SPC-weighted arcs.

### Fixed
- `get_references` no longer truncates silently: every reply states how many
  references were returned of how many, whether the list was cut, and when
  Scopus reports more references than it serves.
- "Error translating query" no longer always blames entitlement. One
  known-good query decides: if it works, the note points at the query
  (`REFPUBYEAR(1997)` must be `REFPUBYEAR IS 1997`); if not, the VPN,
  proxy and insttoken advice follows. "Field restrictions not allowed" gets
  its own note (use `REF(2-s2.0-<id>)` instead of `REFEID`).
- Main-path analysis dropped every paper on a citation cycle. Cycles are now
  broken by removing the edge that runs most against publication order,
  using each paper's earliest known date (online-first before issue date),
  and the removed edges are reported.

## [0.16.0] - 2026-09-30

### Changed
- Renamed to **scopus-plus-mcp** (repository `michalhron/scopus-plus-mcp`;
  both earlier URLs redirect). Network analysis is now one feature among
  many; the name says what the project is: a Scopus MCP server that goes
  further. The PyPI package `citation-network-mcp` stays at 0.15.0 and is
  no longer updated; install `scopus-plus-mcp` instead. The
  `citation-network-mcp` and `scopus-mcp` commands remain as aliases.
- Claude Desktop extension and Claude Code plugin renamed to match
  (`scopus-plus-mcp@michalhron`); reinstall them under the new name.
- MCP registry entry: `io.github.michalhron/scopus-plus-mcp`.
- README states that Scopus and ScienceDirect are Elsevier trademarks and
  that the project is not affiliated with Elsevier.

## [0.15.0] - 2026-09-30

### Added
- `get_fulltext` searches more open sources: every open location in
  OpenAlex (not only its best one), Semantic Scholar's open PDF and arXiv
  ID, arXiv by exact title, Europe PMC full-text XML, and Unpaywall
  (needs `CONTACT_EMAIL`) and CORE (needs `CORE_API_KEY`) when configured.
  Published versions are tried first, then accepted manuscripts, then
  preprints; the result names the source, the version and every attempt.
  ResearchGate and similar sites without an API are not used: their terms
  forbid automated downloading.
- Open-access text must open with the paper's title. Live, the repository
  copy linked to He et al. (2016) was a PhD thesis citing it; it is now
  rejected and the arXiv preprint used.
- Settings `CONTACT_EMAIL`, `CORE_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`.

### Changed
- Open-access text must be at least 5,000 characters, the bar the
  ScienceDirect tier already used; shorter pages are landing pages.

### Fixed
- The package sent a hard-coded contact email (the maintainer's) with every
  OpenAlex request, and a placeholder one elsewhere. No email is sent now
  unless `CONTACT_EMAIL` is set.

### Changed
- `utils.py` (1,389 lines) split into `records.py`, `graphs.py`,
  `lineage.py`, `output.py` and `oa_fulltext.py`; `utils.py` re-exports
  every name, so existing imports keep working. No behaviour change.
- Removed two unused imports (`re` in `client.py`, `matplotlib.cm` in the
  lineage PNG renderer).

## [0.14.0] - 2026-09-30

### Changed
- `server.py` (2,248 lines, 21 tools in one if/elif chain) is split into
  `scopus_mcp/tools/` modules by group; `server.py` keeps the server, the
  clients and a dispatcher (154 lines). No behaviour change: the test suite
  passes unmodified.

### Added
- `topic_landscape` `sample` option for topics larger than `max_papers`:
  `recent` (default), `cited` (most-cited first, where influential work
  appears) or `relevance`; the coverage line names the sample.

## [0.13.0] - 2026-09-30

### Changed
- `topic_landscape` counts only journal papers in its Q1-Q4 figures by
  default (`journals_only=true`). Proceedings series such as
  IFAC-PapersOnLine and Procedia CIRP, and book series such as IFIP AICT,
  also carry CiteScore ranks and had been counted as if they were journals.
  They are now reported per category as `ranked_non_journal` with their
  main venues. `journals_only=false` restores the old counting.

### Added
- `topic_landscape` reports the venue mix of the analysed papers (journal,
  conference proceedings, book series, book, unranked journal) and labels
  every venue with its type.
- `get_journal_metrics` reports each title's venue type.

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
