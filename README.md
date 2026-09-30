# Scopus Plus MCP

<!-- mcp-name: io.github.michalhron/scopus-plus-mcp -->

**The Scopus MCP server that goes further: search, full text, citation networks, journal quality and bibliometrics, for Claude and other AI assistants.**

[![Tests](https://github.com/michalhron/scopus-plus-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/michalhron/scopus-plus-mcp/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/scopus-plus-mcp)](https://pypi.org/project/scopus-plus-mcp/)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Search the literature, trace who cites whom across generations, map research
fronts and intellectual bases, and pull the counts, metrics and bibliography
you need, from a conversation. It is an [MCP](https://modelcontextprotocol.io)
server, so any MCP client can use it: Claude Desktop, Claude Code, Cursor.

- 🕸️ **Citation networks.** Bibliographic coupling, co-citation,
  multi-generation citation lineages, and the citation network within any
  set of papers, with main path and key routes, exported for Pajek,
  VOSviewer or Gephi.
- 🧾 **Audited, not just built.** Citer sets verified across search
  strategies, reference lists checked against Crossref for gaps, and the
  sentences behind each citation, with their intent, from Semantic Scholar.
- 🔀 **Two data sources.** Scopus by default; add `source="openalex"` to run
  the same analyses without a Scopus subscription.
- 📊 **Bibliometrics.** Where a topic is published and in which quartile per
  field; journals above a percentile cut-off in chosen categories; publications
  per year; journal metrics; BibTeX for any list of papers.
- 🔎 **Full-text search.** Find Elsevier papers that use a term in their body,
  and see how often and where each one uses it.
- 🩺 **Honest about access.** One call tells you which tools your current
  Scopus access supports, and why the rest fail.
- ✅ **Tested.** 443 test functions, CI on Linux, macOS and Windows, and a
  live check of every tool against the real APIs.

How it compares with the other Scopus MCP servers: **[comparison](docs/comparison.md)**.

## Ask things like

> *Map the research front around these six papers on organizing visions.*
>
> *Trace two generations of work citing Swanson & Ramiller (1997) and show me the main path.*
>
> *Build the citation network of every Basket of Eight paper citing the organizing-vision papers, and show how the main path cites its predecessors.*
>
> *How has publishing on "digital transformation" grown since 2010?*
>
> *Get SJR and CiteScore for the Basket of Eight, and BibTeX for the papers we just found.*

## Tools

| | |
| --- | --- |
| **Search and records** | `search_scopus` · `search_all` · `search_fulltext` · `get_abstract_details` · `resolve_identifier` · `search_authors` · `get_author_profile` · `get_fulltext` |
| **Citations** | `get_references` · `get_citing_papers` |
| **Networks** | `bibliographic_coupling` · `co_citation` · `citation_lineage` · `citation_network` |
| **Audit** | `resolve_citers` · `citation_context` |
| **Bibliometrics** | `publication_counts` · `topic_landscape` · `get_journal_metrics` · `find_journals` · `get_bibtex` |
| **Diagnostics** | `diagnose_connection` · `get_quota_status` · `get_server_info` |

Parameters and details for each: [tool reference](docs/tools.md).

## Install

You need an API key from the [Elsevier Developer Portal](https://dev.elsevier.com/)
(register with your institutional email). OpenAlex needs no key.

**Claude Desktop** — one click:
1. Download `scopus-plus-mcp-<version>.mcpb` from the
   [latest release](https://github.com/michalhron/scopus-plus-mcp/releases/latest).
2. Open it (or drag it into *Settings → Extensions*), click **Install**, and
   paste your API key when asked. Claude stores it securely.

**Claude Code** — two commands:

```bash
claude plugin marketplace add michalhron/scopus-plus-mcp
claude plugin install scopus-plus-mcp@michalhron
```

Then make your key available, either in your shell
(`export SCOPUS_API_KEY=...`) or, better, in the
[OS secret store](docs/configuration.md#keep-secrets-out-of-config-files).

**Any other MCP client** (needs [uv](https://docs.astral.sh/uv/)):

```json
{
  "mcpServers": {
    "scopus-plus": {
      "command": "uvx",
      "args": ["scopus-plus-mcp"],
      "env": { "SCOPUS_API_KEY": "YOUR_KEY" }
    }
  }
}
```

Then ask your assistant to run `diagnose_connection`: it checks your key and
tells you which tools your Scopus access supports.

## Documentation

| | |
| --- | --- |
| [Tool reference](docs/tools.md) | Every tool and parameter, generated from the code |
| [Data sources](docs/data-sources.md) | Scopus vs OpenAlex: when to use which, measured coverage |
| [Configuration](docs/configuration.md) | All settings; keeping keys in the OS secret store |
| [Access and troubleshooting](docs/access.md) | Off-campus access, tokens, proxies, reading `diagnose_connection` |
| [Comparison](docs/comparison.md) | This project vs the other Scopus MCP servers |
| [Development](docs/development.md) | Tests, live smoke tests, releases |
| [Prompt examples](docs/examples.md) · [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md) | |

## Origins

Formerly `citation-network-mcp` (and before that `michalhron/scopus-mcp`). This project began as a fork of
[qwe4559999/scopus-mcp](https://github.com/qwe4559999/scopus-mcp) by
[thinktraveller](https://github.com/thinktraveller) and
[qwe4559999](https://github.com/qwe4559999), which provides Scopus search,
abstracts, author profiles and citing papers. Everything since version 0.2
was developed here by [Michal Hron](https://github.com/michalhron). MIT
licensed; see [LICENSE](LICENSE).

Scopus and ScienceDirect are trademarks of Elsevier B.V. This project is
independent and not affiliated with or endorsed by Elsevier; it uses their
public APIs with your own key and subscription.
