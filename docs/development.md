# Development

```bash
uv run --extra dev pytest
```

The offline suite mocks every network call, and CI runs it on Linux, macOS
and Windows with Python 3.10 and 3.12. Tests never read your real secret
store. Tests marked `integration` call the live APIs and run only with
`pytest -m integration`.

## Layout

- `src/scopus_mcp/server.py`: the MCP server, the Scopus and OpenAlex
  clients, prompts and a dispatcher.
- `src/scopus_mcp/tools/`: tool schemas and handlers, one module per group
  (`search`, `citations`, `networks`, `bibliometrics`, `diagnostics`) plus
  shared helpers in `common.py`. Each module exports `TOOLS` and `HANDLERS`.
  Handlers read the clients from the server module at call time, which is
  also where tests replace them.
- API clients and parsing: `client.py` (Elsevier), `openalex.py`,
  `fulltext_search.py`, `journals.py`, `authors.py`, `bibtex.py`, `utils.py`.

To add a tool, add its schema to `TOOLS` and a handler to `HANDLERS` in the
right group module, then regenerate `docs/tools.md` and the manifests.

## Smoke-test against the live APIs

Mocked tests prove logic, not the live API's behaviour. Each of these passed
its mocks and failed live:

- search paging past 5,000 results;
- the size limit on tool responses;
- the REF-view reference parser;
- reference-list paging, where the REF view serves 40 references per page and
  can report one more than it serves;
- journal lookup by Scopus source ID, which the Serial Title API ignores,
  answering with an unrelated list.

So every new tool gets a live smoke test before it is trusted.

## Tool reference

`docs/tools.md` is generated from the server's tool definitions:

```bash
uv run python scripts/gen_tools_doc.py
```

`tests/test_docs.py` fails when it is out of date, and when a relative link
in the README or `docs/` points nowhere.

## Extension and plugin

`manifest.json` (Claude Desktop extension) and
`plugins/citation-network-mcp/.claude-plugin/plugin.json` (Claude Code plugin)
take their version and tool list from the code, and `server.json` (MCP
registry) its version:

```bash
uv run python scripts/build_extension.py
```

This also validates and packs `dist/citation-network-mcp-<version>.mcpb`
(needs Node.js for `npx`). `tests/test_extension.py` fails when any of these
is stale, and CI builds the bundle on every push.

## Releases

1. Change `__version__` in `src/scopus_mcp/__init__.py`, the only place the
   version lives.
2. Run `scripts/build_extension.py` and move the CHANGELOG's Unreleased
   entries under the new version.
3. Merge to `main`, then push the tag `vX.Y.Z`. Two workflows run:
   `release.yml` publishes a GitHub Release with the `.mcpb`, and
   `publish.yml` uploads `citation-network-mcp` to PyPI and then lists it in
   the MCP registry. Both check that the tag matches the version.

Publishing uses GitHub OIDC, with no stored tokens: PyPI through a trusted
publisher for this repository (workflow `publish.yml`, environment `pypi`),
the registry through the `io.github.michalhron` namespace. The `mcp-name`
line at the top of the README is how the registry verifies the PyPI package.

The Python import package keeps its original name, `scopus_mcp`, and the
`scopus-mcp` command remains as an alias, so older configurations work.
