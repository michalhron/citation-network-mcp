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

## Releases

The version lives only in `src/scopus_mcp/__init__.py`. Change it, move the
CHANGELOG's Unreleased entries under the new version, and merge. Do not push
`v*` tags yet: the publish workflow would upload to PyPI under the upstream
package name. See [the roadmap](../ROADMAP.md).
