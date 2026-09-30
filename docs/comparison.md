# How this compares

There is no official Elsevier MCP server. These are the Scopus MCP servers on
GitHub with more than a README behind them, compared on **2026-09-30** by
reading each repository's source code: its tool definitions, tests and CI
configuration. ✓ means a dedicated tool exists; — means none was found.
Corrections are welcome as issues.

| | **This project** | [upstream][up] | [scopus-mcp-extended][ext] | [elsevier-mcp][els] | [strato-mcp-scopus][str] | [Elsevier_MCP][yas] |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| **Citation analysis** | | | | | | |
| Bibliographic coupling network | ✓ | — | — | — | — | — |
| Co-citation network | ✓ | — | — | — | — | — |
| Citation lineage with main-path analysis | ✓ | — | — | — | — | — |
| Forward citations (citing papers) | ✓ | ✓ | ✓ | — | ✓ | — |
| Reference lists | ✓ | — | ✓ | — | — | — |
| Works without a Scopus subscription (OpenAlex) | ✓ | — | — | — | — | — |
| **Bibliometrics and records** | | | | | | |
| Publications per year | ✓ | — | — | — | ✓ | ✓ |
| Journal metrics (SJR, SNIP, CiteScore) | ✓ | — | ✓ | ✓ | — | — |
| BibTeX export | ✓ | — | ✓ | — | — | — |
| Full text (ScienceDirect, open access) | ✓ | — | ✓ | ✓ | — | — |
| Full-text search (ScienceDirect) | ✓ | — | ✓ | — | — | — |
| Author profile by ID | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Author search by name | ✓ | — | ✓ | ✓ | ✓ | — |
| Altmetrics, Embase, affiliation search | — | — | ✓ | partly | — | — |
| **Reliability** | | | | | | |
| Institutional token | ✓ | — | ✓ | ✓ | — | — |
| Access diagnostics | ✓ | — | ✓ | — | — | — |
| Response cache and retries | ✓ | ✓ | ✓ | — | — | — |
| Test functions | 167 | 5 | 10 | 0 | 156 | 6 |
| CI on Linux, macOS and Windows | ✓ | — | — | — | — | — |
| **Project** | | | | | | |
| Tools | 19 | 5 | 25 | 13 | 12 | 6 |
| Commits | 85 | 45 | 5 | 7 | 12 | 6 |
| Last commit | 2026-09 | 2026-05 | 2026-05 | 2026-06 | 2026-04 | 2026-05 |

[up]: https://github.com/qwe4559999/scopus-mcp
[ext]: https://github.com/oliviercaron/scopus-mcp-extended
[els]: https://github.com/kemalabuteliyte/elsevier-mcp
[str]: https://github.com/stratosphereips/strato-mcp-scopus
[yas]: https://github.com/yasufumi-nakata/Elsevier_MCP

## Where this project leads

- **Citation networks.** It is the only one that builds bibliographic
  coupling and co-citation networks, and the only one that walks a
  multi-generation citation lineage and extracts its main path
  (search-path-count, Batagelj 2003). Networks come out as GraphML for
  VOSviewer, Gephi or Pajek.
- **Two data sources.** Everything from search to lineage also runs on
  OpenAlex, so it works without a Scopus subscription, with the coverage
  differences [measured and documented](data-sources.md).
- **Correctness under the live API.** Reference lists are paged to the end
  (the Scopus REF view serves 40 per request, which silently truncates naive
  clients), journal lookups avoid the Serial Title API's source-ID trap, and
  abstract-length text is never labelled full text.
- **Testing.** The largest test suite of the group, CI on three operating
  systems, and a live smoke test for every tool.

## Where others lead

- **Breadth of Elsevier APIs.** scopus-mcp-extended covers 25 endpoints,
  including PlumX altmetrics, Embase and affiliation search.
- **Docker.** strato ships a Docker image; this project installs from PyPI
  (`uvx citation-network-mcp`), as a Claude Desktop extension or as a Claude
  Code plugin, and is listed in the official MCP registry.
- **Workflow-shaped tools.** strato wraps common questions ("find experts",
  "compare documents") in single tools.
