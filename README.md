# Scopus MCP

**Citation-network analysis for Claude and other AI assistants, on Scopus or OpenAlex.**

[![Tests](https://github.com/michalhron/scopus-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/michalhron/scopus-mcp/actions/workflows/test.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Search the literature, trace who cites whom across generations, map research
fronts and intellectual bases, and pull the counts, metrics and bibliography
you need, from a conversation. It is an [MCP](https://modelcontextprotocol.io)
server, so any MCP client can use it: Claude Desktop, Claude Code, Cursor.

- 🕸️ **Citation networks.** Bibliographic coupling, co-citation, and
  multi-generation citation lineages with main-path analysis, exported as
  GraphML for VOSviewer or Gephi.
- 🔀 **Two data sources.** Scopus by default; add `source="openalex"` to run
  the same analyses without a Scopus subscription.
- 📊 **Bibliometrics.** Publications per year, journal SJR/SNIP/CiteScore,
  BibTeX for any list of papers.
- 🩺 **Honest about access.** One call tells you which tools your current
  Scopus access supports, and why the rest fail.
- ✅ **Tested.** 167 test functions, CI on Linux, macOS and Windows, and a
  live check of every tool against the real APIs.

How it compares with the other Scopus MCP servers: **[comparison](docs/comparison.md)**.

## Ask things like

> *Map the research front around these six papers on organizing visions.*
>
> *Trace two generations of work citing Swanson & Ramiller (1997) and show me the main path.*
>
> *How has publishing on "digital transformation" grown since 2010?*
>
> *Get SJR and CiteScore for the Basket of Eight, and BibTeX for the papers we just found.*

## Tools

| | |
| --- | --- |
| **Search and records** | `search_scopus` · `search_all` · `get_abstract_details` · `resolve_identifier` · `search_authors` · `get_author_profile` · `get_fulltext` |
| **Citations** | `get_references` · `get_citing_papers` |
| **Networks** | `bibliographic_coupling` · `co_citation` · `citation_lineage` |
| **Bibliometrics** | `publication_counts` · `get_journal_metrics` · `get_bibtex` |
| **Diagnostics** | `diagnose_connection` · `get_quota_status` · `get_server_info` |

Parameters and details for each: [tool reference](docs/tools.md).

## Quick start

You need [uv](https://docs.astral.sh/uv/) and an API key from the
[Elsevier Developer Portal](https://dev.elsevier.com/) (register with your
institutional email). Add this to your MCP client's config, for Claude
Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "scopus": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/michalhron/scopus-mcp.git@main", "scopus-mcp"],
      "env": { "SCOPUS_API_KEY": "YOUR_KEY" }
    }
  }
}
```

Restart the client, then ask it to run `diagnose_connection`. Pin a commit
instead of `@main` if you want upgrades to be deliberate. (`uvx scopus-mcp`
without `--from` installs the older upstream package.)

## Documentation

| | |
| --- | --- |
| [Tool reference](docs/tools.md) | Every tool and parameter, generated from the code |
| [Data sources](docs/data-sources.md) | Scopus vs OpenAlex: when to use which, measured coverage |
| [Configuration](docs/configuration.md) | All settings; keeping keys in the OS secret store |
| [Access and troubleshooting](docs/access.md) | Off-campus access, tokens, proxies, reading `diagnose_connection` |
| [Comparison](docs/comparison.md) | This project vs the other Scopus MCP servers |
| [Development](docs/development.md) | Tests, live smoke tests, releases |
| [Prompt examples](USAGE_EXAMPLES.md) · [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md) | |

## Origins

This project began as a fork of
[qwe4559999/scopus-mcp](https://github.com/qwe4559999/scopus-mcp) by
[thinktraveller](https://github.com/thinktraveller) and
[qwe4559999](https://github.com/qwe4559999), which provides Scopus search,
abstracts, author profiles and citing papers. Everything since version 0.2
was developed here by [Michal Hron](https://github.com/michalhron). MIT
licensed; see [LICENSE](LICENSE).
