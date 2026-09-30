"""Import saved Scopus / Web of Science exports as a reusable corpus file."""
import json
import logging
from datetime import datetime
from pathlib import Path

import mcp.types as types

from .. import jobs
from ..importers import CORPUS_KIND, deduplicate, read_export
from ..openalex import normalize_title
from ..output import _output_dir
from .common import server_module
from .corpus import _base_name, _scopus_records_by_title, _scopus_records_for

logger = logging.getLogger("scopus-plus-mcp")

TOOLS = [
    types.Tool(
        name="import_records",
        description=(
            "Read saved bibliographic exports into one deduplicated corpus file: Scopus "
            "CSV, RIS or BibTeX exports and Web of Science plain-text or tab-delimited "
            "exports (format detected). Records are merged across files by Scopus ID, WoS "
            "ID, DOI, or title and year. With resolve (default true), records without a "
            "Scopus ID (e.g. from Web of Science) are matched in Scopus by DOI, then by "
            "exact title and year. Returns the corpus file path and the Scopus IDs. Pass the "
            "file as corpus_file to citation_network, rpys, historiograph, research_fronts "
            "or thematic_evolution to analyse exactly these records again later "
            "(thematic_evolution then reads keywords from the file, with no API calls)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "Export files on this computer (.csv, .txt, .ris, .bib, .tsv)."},
                "resolve": {"type": "boolean", "default": True,
                            "description": "Look up Scopus IDs for records without one (default true)."},
            },
            "required": ["paths"],
        },
    ),
]


async def _import_records(arguments: dict) -> list:
    srv = server_module()
    paths = arguments.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        raise ValueError("paths is required.")
    records, per_file = [], []
    for path in paths:
        recs = read_export(path)
        per_file.append((Path(path).name, recs[0]['format'] if recs else '?', len(recs)))
        records.extend(recs)
    unique, dups = deduplicate(records)

    resolved_doi = resolved_title = 0
    if arguments.get("resolve", True):
        client = srv.client
        need = [r for r in unique if not r.get('scopus_id') and r.get('doi')]
        if need:
            jobs.progress(f"import_records: {len(need)} DOIs to Scopus IDs")
            found = await _scopus_records_for(client, 'DOI', [r['doi'] for r in need])
            for r in need:
                if r['doi'] in found:
                    r['scopus_id'] = found[r['doi']]['scopus_id']
                    resolved_doi += 1
        need = [r for r in unique if not r.get('scopus_id') and r.get('title')]
        if need:
            jobs.progress(f"import_records: {len(need)} titles to Scopus IDs")
            found = await _scopus_records_by_title(client, need)
            for r in need:
                rec = found.get(normalize_title(r['title']))
                if rec and str(rec.get('cover_date') or '')[:4] == str(r.get('year') or ''):
                    r['scopus_id'] = rec['scopus_id']
                    resolved_title += 1

    scopus_ids = [r['scopus_id'] for r in unique if r.get('scopus_id')]
    dois = [r['doi'] for r in unique if r.get('doi')]
    base = _base_name(f"corpus-{len(unique)}-records")
    path = _output_dir() / f'{base}.json'
    path.write_text(json.dumps({
        'kind': CORPUS_KIND, 'version': srv.SERVER_VERSION,
        'created': datetime.now().isoformat(timespec='seconds'),
        'files': [f for f, _, _ in per_file], 'records': unique,
        'scopus_ids': scopus_ids, 'dois': dois}, ensure_ascii=False, indent=2), encoding='utf-8')

    missing = [r for r in unique if not r.get('scopus_id')]
    lines = [f"Imported {len(records)} records from {len(paths)} file(s): {len(unique)} unique "
             f"({dups} duplicates merged).",
             'Files: ' + '; '.join(f"{name} ({fmt}, {n})" for name, fmt, n in per_file),
             f"Scopus IDs: {len(scopus_ids)} of {len(unique)} (from the export "
             f"{len(scopus_ids) - resolved_doi - resolved_title}, by DOI {resolved_doi}, "
             f"by title {resolved_title}); DOIs: {len(dois)}.",
             f"Corpus file: {path}",
             "Pass it as corpus_file to citation_network, rpys, historiograph, research_fronts or "
             "thematic_evolution."]
    if missing:
        lines.append(f"Without a Scopus ID ({len(missing)}; left out of Scopus analyses): " + '; '.join(
            f"{r.get('year') or '?'} {(r.get('title') or '')[:60]}" for r in missing[:10])
            + (' ...' if len(missing) > 10 else ''))
    lines.append("Scopus IDs: " + ', '.join(scopus_ids))
    return [types.TextContent(type="text", text='\n'.join(lines))]


HANDLERS = {'import_records': _import_records}
