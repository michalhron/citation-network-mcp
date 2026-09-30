"""Generate docs/tools.md from the server's own tool definitions.

    uv run python scripts/gen_tools_doc.py

tests/test_docs.py fails when docs/tools.md is out of date, so the reference
cannot drift from the code.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('SCOPUS_API_KEY', 'docs-generation')
os.environ.setdefault('SCOPUS_DISABLE_SECRET_STORE', '1')

GROUPS = [
    ('Search and records', ['search_scopus', 'search_all', 'get_abstract_details',
                            'resolve_identifier', 'search_authors', 'get_author_profile',
                            'get_fulltext']),
    ('Citations', ['get_references', 'get_citing_papers']),
    ('Networks and lineage', ['bibliographic_coupling', 'co_citation', 'citation_lineage']),
    ('Bibliometrics and bibliography', ['publication_counts', 'get_journal_metrics',
                                        'get_bibtex']),
    ('Diagnostics', ['diagnose_connection', 'get_quota_status', 'get_server_info']),
]

HEADER = """# Tool reference

Generated from the server's tool definitions by `scripts/gen_tools_doc.py`;
do not edit by hand. Tools marked **OpenAlex** accept `source="openalex"`
and then need no Scopus subscription (see [data sources](data-sources.md)).
"""


def _type(schema: dict) -> str:
    if 'enum' in schema:
        return ' \\| '.join(f'`{v}`' for v in schema['enum'])
    if schema.get('type') == 'array':
        return f"list of {schema.get('items', {}).get('type', 'value')}"
    return schema.get('type', '')


def _cell(text) -> str:
    return ' '.join(str(text).split()).replace('|', '\\|')


def render(tools) -> str:
    by_name = {t.name: t for t in tools}
    grouped = [n for _, names in GROUPS for n in names]
    missing = sorted(set(by_name) - set(grouped))
    if missing:
        raise SystemExit(f"Tools missing from GROUPS: {missing}")
    out = [HEADER]
    for title, names in GROUPS:
        out.append(f"\n## {title}\n")
        for name in names:
            tool = by_name[name]
            props = tool.inputSchema.get('properties', {})
            required = set(tool.inputSchema.get('required', []))
            tag = ' · **OpenAlex**' if 'source' in props else ''
            out.append(f"\n### `{name}`{tag}\n\n{_cell(tool.description)}\n")
            params = [(k, v) for k, v in props.items() if k != 'source']
            if params:
                out.append("\n| Parameter | Type | Default | Description |\n| --- | --- | --- | --- |")
                for key, schema in params:
                    default = schema.get('default', 'required' if key in required else '')
                    out.append(f"| `{key}` | {_type(schema)} | {_cell(default)} | "
                               f"{_cell(schema.get('description', ''))} |")
                out.append('')
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).rstrip() + '\n'


async def _tools():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
    from scopus_mcp import server
    try:
        return await server.handle_list_tools()
    finally:
        await server.client.close()
        await server.openalex.close()


def main():
    text = render(asyncio.run(_tools()))
    path = Path(__file__).resolve().parent.parent / 'docs' / 'tools.md'
    path.write_text(text, encoding='utf-8')
    print(f"wrote {path}")


if __name__ == '__main__':
    main()
