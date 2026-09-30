"""Historical views of a corpus: RPYS and the historiograph."""
import csv
import json
import logging
from datetime import date

import mcp.types as types

from .. import jobs
from ..baskets import SCOPE_SCHEMA
from ..history import historiograph, render_historiograph_png, render_rpys_png, spectrogram
from ..lineage import compute_main_path, write_pajek
from ..openalex import clean_openalex_work, short_id
from ..records import clean_references
from ..output import _output_dir
from .common import SOURCE_SCHEMA, _source, server_module
from .corpus import _base_name, collect_corpus

logger = logging.getLogger("scopus-plus-mcp")

CORPUS_PROPS = {
    "ids": {"type": "array", "items": {"type": "string"},
            "description": "The papers (Scopus IDs/EIDs; with source='openalex', DOIs or OpenAlex IDs). Use this or query."},
    "query": {"type": "string", "description": "Search query defining the set, instead of ids."},
    "max_results": {"type": "integer", "default": 300,
                    "description": "With query: how many papers to include (default 300)."},
    "scope": SCOPE_SCHEMA,
    "source": SOURCE_SCHEMA,
}

TOOLS = [
    types.Tool(
        name="rpys",
        description=(
            "Reference Publication Year Spectroscopy (Marx et al. 2014): counts every "
            "cited reference of a set of papers by the year the cited work appeared, "
            "subtracts the five-year median, and reports the peak years with the works "
            "cited most from each: the set's historical roots. Give ids or a query "
            "(e.g. the confirmed citers from resolve_citers). Writes a CSV of the "
            "spectrogram and a PNG. Cost: one reference request per paper (cached; "
            "shared with citation_network). With source='openalex' the cited works "
            "are fetched in batches of 50."
        ),
        inputSchema={"type": "object", "properties": {
            **CORPUS_PROPS,
            "from_year": {"type": "integer", "default": 1900,
                          "description": "Earliest cited year to count (default 1900)."},
            "to_year": {"type": "integer", "description": "Latest cited year (default: this year)."},
            "top_peaks": {"type": "integer", "default": 10, "description": "Peaks to report (default 10)."},
        }},
    ),
    types.Tool(
        name="historiograph",
        description=(
            "Garfield's historiograph: the papers most cited within the set (local "
            "citation score) on a time axis, with the citations among them and the "
            "global main path highlighted. Complements the main path with the "
            "picture readers expect beside it. Give ids or a query. Writes a PNG and a "
            "Pajek file; lists the papers with their local and global citation counts."
        ),
        inputSchema={"type": "object", "properties": {
            **CORPUS_PROPS,
            "top": {"type": "integer", "default": 30,
                    "description": "Papers to draw, by local citation score (default 30)."},
        }},
    ),
]


async def _cited_references(srv, arguments, references):
    """Every cited reference, one dict per citation instance."""
    if _source(arguments) != 'openalex':
        # Years come from the FULL view's bibliography where available.
        out, dated_from_full = [], 0
        done = 0
        for sid, refs in references.items():
            bib = None
            try:
                bib = await srv.client.get_bibliography(sid)
            except Exception as exc:
                logger.info(f"bibliography failed for {sid}: {exc}")
            done += 1
            jobs.progress(f"rpys: bibliographies {done}/{len(references)}")
            if bib:
                out.extend(clean_references({'abstracts-retrieval-response': {
                    'references': {'reference': bib}}}))
                dated_from_full += 1
            else:
                out.extend(refs)
        return out
    unique = list(dict.fromkeys(w for ids in references.values() for w in ids))
    jobs.progress(f"rpys: fetching {len(unique)} cited works from OpenAlex")
    works = {short_id(w['id']): clean_openalex_work(w) for w in await srv.openalex.works_by_ids(unique)}
    out = []
    for ids in references.values():
        for w in ids:
            rec = works.get(w)
            if rec:
                out.append({'openalex_id': w, 'year': rec['year'], 'title': rec['title'],
                            'authors': [rec['creator']] if rec['creator'] else [],
                            'source': rec['publication_name']})
            else:
                out.append({'openalex_id': w, 'year': None})
    return out


async def _rpys(arguments: dict) -> list:
    srv = server_module()
    nodes, fetch_errors, parse_errors, references = await collect_corpus(srv, arguments)
    refs = await _cited_references(srv, arguments, references)
    from_year = int(arguments.get("from_year") or 1900)
    to_year = int(arguments.get("to_year") or date.today().year)
    spec = spectrogram(refs, from_year, to_year, int(arguments.get("top_peaks") or 10))
    label = arguments.get("query") or f"{len(nodes)} papers"
    base = _base_name(f"rpys-{label}")
    out = _output_dir()
    csv_path = out / f'{base}.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['year', 'n', 'median', 'deviation'])
        writer.writeheader()
        writer.writerows({k: r[k] for k in ('year', 'n', 'median', 'deviation')} for r in spec['rows'])
    json_path = out / f'{base}.json'
    json_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    png = render_rpys_png(spec, out / f'{base}.png',
                          f"RPYS: {len(nodes)} citing papers, {spec['n_references']} cited references")
    missing = {**fetch_errors, **parse_errors}
    lines = [
        f"RPYS of {len(nodes)} papers ({_source(arguments)}): {spec['n_references']} cited "
        f"references dated {from_year}-{to_year}; {spec['undated']} without a year.",
        f"Server version: {srv.SERVER_VERSION}",
        f"Spectrogram CSV: {csv_path}", f"JSON: {json_path}",
    ]
    if png:
        lines.append(f"PNG: {png}")
    if missing:
        lines.append(f"Reference lists missing for {len(missing)} paper(s): " + ', '.join(list(missing)[:10]))
    lines.append("Peaks (year, cited references, deviation from the 5-year median; most-cited "
                 "works of that year):")
    for p in spec['peaks']:
        works = '; '.join(f"{w['label']} ({w['citations']})" for w in p['works'])
        lines.append(f"  {p['year']}: n={p['n']}, +{p['deviation']:g} — {works}")
    lines.append("A peak carried by one work (its citations are most of the year's count) marks "
                 "a single root; a broad peak marks a formative period.")
    return [types.TextContent(type="text", text='\n'.join(lines))]


async def _historiograph(arguments: dict) -> list:
    srv = server_module()
    nodes, fetch_errors, parse_errors, _ = await collect_corpus(srv, arguments)
    source = _source(arguments)
    records = [dict(n, scopus_id=n['id'] if source == 'scopus' else None,
                    openalex_id=n['id'] if source == 'openalex' else None) for n in nodes.values()]
    main_path = compute_main_path(records)['global_main_path']
    hist = historiograph(nodes, int(arguments.get("top") or 30))
    label = arguments.get("query") or f"{len(nodes)} papers"
    base = _base_name(f"historiograph-{label}")
    out = _output_dir()
    png = render_historiograph_png(hist, main_path, out / f'{base}.png',
                                   f"Historiograph: top {len(hist['nodes'])} of {len(nodes)} papers by "
                                   "local citations (orange: global main path)")
    net = write_pajek([{'id': n['id'], 'label': n['label']} for n in hist['nodes']],
                      [{'source': a, 'target': b} for a, b in hist['edges']], out / f'{base}.net')
    json_path = out / f'{base}.json'
    json_path.write_text(json.dumps({**hist, 'main_path': main_path}, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    on_path = [n for n in hist['nodes'] if n['id'] in set(main_path)]
    lines = [
        f"Historiograph of {len(nodes)} papers ({source}): the {len(hist['nodes'])} most cited within "
        f"the set, {len(hist['edges'])} citations among them; {len(on_path)} of the "
        f"{len(main_path)} global main-path papers are among them.",
        f"Server version: {srv.SERVER_VERSION}",
        f"PNG: {png}" if png else "PNG: not rendered (matplotlib unavailable)",
        f"Pajek .net: {net}", f"JSON: {json_path}",
    ]
    missing = {**fetch_errors, **parse_errors}
    if missing:
        lines.append(f"Reference lists missing for {len(missing)} paper(s); local citation "
                     "counts may be low.")
    lines.append("Papers (year, label, local citations LCS / global citations GCS; * = on the main path):")
    path_set = set(main_path)
    for n in hist['nodes']:
        star = '*' if n['id'] in path_set else ' '
        lines.append(f" {star}{n['year'] or '?'} {n['label']}: LCS {n['lcs']}, GCS {n['gcs']} — "
                     f"{(n['title'] or '')[:70]}")
    return [types.TextContent(type="text", text='\n'.join(lines))]


HANDLERS = {'rpys': _rpys, 'historiograph': _historiograph}
