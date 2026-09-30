# Development

```bash
uv run --extra dev pytest
```

The offline suite mocks every network call, and CI runs it on Linux, macOS
and Windows with Python 3.10 and 3.12. Tests never read your real secret
store. Tests marked `integration` call the live APIs and run only with
`pytest -m integration`.

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
`plugins/scopus-mcp/.claude-plugin/plugin.json` (Claude Code plugin) take
their version and tool list from the code:

```bash
uv run python scripts/build_extension.py
```

This also validates and packs `dist/scopus-mcp-<version>.mcpb` (needs
Node.js for `npx`). `tests/test_extension.py` fails when either manifest is
stale, and CI builds the bundle on every push.

## Releases

1. Change `__version__` in `src/scopus_mcp/__init__.py`, the only place the
   version lives.
2. Run `scripts/build_extension.py` and move the CHANGELOG's Unreleased
   entries under the new version.
3. Merge to `main`, then push the tag `vX.Y.Z`. `release.yml` checks the tag
   matches the version and publishes a GitHub Release with the `.mcpb`.

PyPI publishing (`publish.yml`) is manual only until the project has its own
package name; `scopus-mcp` on PyPI belongs to upstream.
