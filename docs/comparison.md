# How this compares

Two comparisons: with the other Scopus MCP servers (the same kind of
software), and with the established bibliometrics packages (the same kind of
analysis).

## Scopus MCP servers

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
| Citation network of any paper set: SPC/SPLC/SPNP main paths, key routes | ✓ | — | — | — | — | — |
| RPYS and historiograph | ✓ | — | — | — | — | — |
| Citer sets verified across search strategies and indexes | ✓ | — | — | — | — | — |
| Reference-list completeness checks | ✓ | — | — | — | — | — |
| Citation contexts, intent, and transmission audit of a main path | ✓ | — | — | — | — | — |
| Forward citations (citing papers) | ✓ | ✓ | ✓ | — | ✓ | — |
| Reference lists | ✓ | — | ✓ | — | — | — |
| Works without a Scopus subscription (OpenAlex) | ✓ | — | — | — | — | — |
| **Bibliometrics and records** | | | | | | |
| Publications per year | ✓ | — | — | — | ✓ | ✓ |
| Journal metrics (SJR, SNIP, CiteScore) | ✓ | — | ✓ | ✓ | — | — |
| Journal percentile and quartile per subject category | ✓ | — | — | — | — | — |
| Journals above a percentile cut-off in chosen categories | ✓ | — | — | — | — | — |
| Topic landscape: fields and journal quartiles for a query | ✓ | — | — | — | — | — |
| BibTeX export | ✓ | — | ✓ | — | — | — |
| Full text (ScienceDirect, open access) | ✓ | — | ✓ | ✓ | — | — |
| Open-access copies from arXiv, Semantic Scholar, Europe PMC, Unpaywall, CORE | ✓ | — | — | — | — | — |
| Full-text search (ScienceDirect) | ✓ | — | ✓ | — | — | — |
| Author profile by ID | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Author search by name | ✓ | — | ✓ | ✓ | ✓ | — |
| Altmetrics, Embase, affiliation search | — | — | ✓ | partly | — | — |
| **Reliability** | | | | | | |
| Institutional token | ✓ | — | ✓ | ✓ | — | — |
| Access diagnostics | ✓ | — | ✓ | — | — | — |
| Response cache and retries | ✓ | ✓ | ✓ | — | — | — |
| Test functions | 487 | 5 | 10 | 0 | 156 | 6 |
| CI on Linux, macOS and Windows | ✓ | — | — | — | — | — |
| **Project** | | | | | | |
| Tools | 30 | 5 | 25 | 13 | 12 | 6 |
| Commits | 120 | 45 | 5 | 7 | 12 | 6 |
| Last commit | 2026-09 | 2026-05 | 2026-05 | 2026-06 | 2026-04 | 2026-05 |

[up]: https://github.com/qwe4559999/scopus-mcp
[ext]: https://github.com/oliviercaron/scopus-mcp-extended
[els]: https://github.com/kemalabuteliyte/elsevier-mcp
[str]: https://github.com/stratosphereips/strato-mcp-scopus
[yas]: https://github.com/yasufumi-nakata/Elsevier_MCP

### Where this project leads

- **Citation networks.** It is the only one that builds bibliographic
  coupling and co-citation networks, and the only one that walks a
  multi-generation citation lineage and extracts its main path
  (search-path-count, Batagelj 2003) and key routes, for a lineage or for
  any set of papers. Networks come out as GraphML and Pajek `.net` for
  VOSviewer, Gephi or Pajek.
- **Auditing a citation network.** It is the only one that verifies a
  citer set across search strategies, flags reference lists that are short
  against Crossref (papers that would silently lose edges), and returns the
  citing sentences and citation intent behind an edge (Semantic Scholar).
- **Review scoping by field and prestige.** It is the only one that reports
  journal percentiles per subject category, lists the journals above a
  percentile cut-off in chosen categories as a ready Scopus query, and maps
  where a topic is published and in which quartile per field.
- **Full-text engagement.** Besides searching full text, it counts how often
  and where each article uses a term, separating use from citation.
- **Two data sources.** Everything from search to lineage also runs on
  OpenAlex, so it works without a Scopus subscription, with the coverage
  differences [measured and documented](data-sources.md).
- **Correctness under the live API.** Reference lists are paged to the end
  (the Scopus REF view serves 40 per request, which silently truncates naive
  clients), journal lookups avoid the Serial Title API's source-ID trap, and
  abstract-length text is never labelled full text.
- **Testing.** The largest test suite of the group, CI on three operating
  systems, and a live smoke test for every tool.

### Where others lead

- **Breadth of Elsevier APIs.** scopus-mcp-extended covers 25 endpoints,
  including PlumX altmetrics, Embase and affiliation search.
- **Docker.** strato ships a Docker image; this project installs from PyPI
  (`uvx scopus-plus-mcp`), as a Claude Desktop extension or as a Claude
  Code plugin, and is listed in the official MCP registry.
- **Workflow-shaped tools.** strato wraps common questions ("find experts",
  "compare documents") in single tools.

## Bibliometrics packages

The established tools for the same analyses, compared on **2026-09-30** from
their documentation. They are libraries and desktop programs: they analyse
exported records or call APIs from code. This project answers in a
conversation and fetches its data live. ✓ supported; partly: a narrower
version; — not supported. Corrections are welcome.

| | **This project** | [bibliometrix][bx] | [pybliometrics][pyb] | [metaknowledge][mk] | [litstudy][ls] | [VOSviewer][vos] / [CitNetExplorer][cne] | [Pajek][pj] / [MainPath][mp] |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Form | MCP server | R package, Shiny app | Python library | Python library | Python library | Desktop apps | Desktop apps |
| **Data** | | | | | | | |
| Live Scopus APIs | ✓ | — | ✓ | — | ✓ | — | — |
| OpenAlex, Semantic Scholar, Crossref | ✓ | partly | — | — | ✓ | ✓ | — |
| Export files (Web of Science, Scopus, ...) | — (planned) | ✓ | — | ✓ | ✓ | ✓ | network files |
| **Citation analysis** | | | | | | | |
| Coupling and co-citation networks | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ |
| Direct-citation network of a paper set | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ |
| Main path: SPC/SPLC/SPNP, local and global | ✓ | — | — | — | — | — | ✓ |
| Key-route main paths | ✓ | — | — | — | — | — | ✓ |
| Historiograph | ✓ | ✓ | — | — | — | partly | — |
| RPYS | ✓ | ✓ | — | ✓ | — | — | — |
| Clustering into research fronts | — (planned) | ✓ | — | — | — | ✓ | ✓ |
| **Content and science mapping** | | | | | | | |
| Co-word, thematic maps, thematic evolution | — (planned) | ✓ | — | — | partly | partly | — |
| Topic models | — | ✓ | — | — | ✓ | — | — |
| Co-authorship, institution, country networks | — | ✓ | — | ✓ | ✓ | ✓ | ✓ |
| Lotka, Bradford, descriptive statistics | partly | ✓ | — | — | partly | — | — |
| **Audit and quality** | | | | | | | |
| Reference-list completeness against Crossref, OpenAlex, S2 | ✓ | — | — | — | — | — | — |
| Citer sets verified across strategies and indexes | ✓ | — | — | — | — | — | — |
| Citation contexts and intent; transmission audit | ✓ | — | — | — | — | — | — |
| Journal percentiles and quartiles per category | ✓ | — | ✓ | — | — | — | — |
| Full text and full-text search | ✓ | — | ✓ | — | — | — | — |
| **Maturity** | | | | | | | |
| First release | 2026 | 2016 | 2017 | 2015 | 2021 | 2010 / 2014 | 1996 / 2010s |
| Peer-reviewed software paper | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

[bx]: https://www.bibliometrix.org
[pyb]: https://github.com/pybliometrics-dev/pybliometrics
[mk]: https://github.com/UWNETLAB/metaknowledge
[ls]: https://github.com/NLeSC/litstudy
[vos]: https://www.vosviewer.com
[cne]: https://www.citnetexplorer.nl
[pj]: http://mrvar.fdv.uni-lj.si/pajek/
[mp]: https://sites.google.com/site/mainpathanalysis/

### Where this project leads

- **Auditing a network, not only building one.** None of the others checks
  reference lists for gaps, verifies a citer set across strategies and
  indexes, or reads what a citation carries. These are the checks that
  decide whether a main path is a finding or an artefact.
- **Main-path analysis without leaving the conversation.** SPC, SPLC and
  SPNP weights, local, global and key-route searches and a robustness check
  are otherwise found only in Pajek and MainPath, as separate desktop steps.
- **Live data from several indexes**, with the index recorded for every
  count.

### Where others lead

- **Science mapping.** bibliometrix and VOSviewer cover co-word and thematic
  maps, thematic evolution, clustering and collaboration networks; this
  project has none of these yet (thematic evolution and research fronts are
  on the roadmap).
- **Files and reproducibility.** The packages analyse saved exports, so an
  analysis can be rerun years later on the same records. This project works
  from live APIs; its cached responses and written corpus files are the
  record (export-file import is on the roadmap).
- **Track record.** Every one of the others has a peer-reviewed software
  paper and years of users. This project is new, and its
  transmission-audit labels are unvalidated heuristics.
