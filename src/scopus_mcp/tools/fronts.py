"""Research fronts of a paper set and the main path's route across them."""
import asyncio
import json
import logging

import mcp.types as types

from .. import jobs
from ..fronts import (
    cluster_citation_network,
    describe_fronts,
    hop_robustness,
    path_route,
    write_pajek_partition,
)
from ..graphs import _make_node_label
from ..lineage import compute_main_path, write_pajek
from ..openalex import short_id
from ..output import _output_dir
from ..themes import normalize_term
from .common import _source, server_module
from .corpus import REF_CONCURRENCY, _base_name, collect_corpus
from .history import CORPUS_PROPS
from .themes import _scopus_terms

logger = logging.getLogger("scopus-plus-mcp")

TOOLS = [
    types.Tool(
        name="research_fronts",
        description=(
            "Research fronts of a paper set: Louvain communities of its direct-citation "
            "network (as in CitNetExplorer), each described by its years, density, core "
            "papers (most cited within the set) and the keywords that distinguish it. Also "
            "reports which front each paper of the global main path belongs to and where the "
            "path hops from one front to another: a main path that stays in one front traces "
            "a single conversation, one that hops stitches several together. Give ids or a "
            "query. Writes JSON and Pajek .net plus .clu (partition) files. Cost: one "
            "reference and one abstract request per paper (cached)."
        ),
        inputSchema={"type": "object", "properties": {
            **CORPUS_PROPS,
            "resolution": {"type": "number", "default": 1.0,
                           "description": "Louvain resolution: above 1 gives more, smaller fronts (default 1)."},
            "min_size": {"type": "integer", "default": 3,
                         "description": "Smallest front reported; smaller groups count as unclustered (default 3)."},
        }},
    ),
]


async def _keywords(srv, arguments, nodes):
    """{paper: [normalised keywords]} for labelling fronts."""
    if _source(arguments) == 'openalex':
        works = await srv.openalex.works_by_ids(list(nodes), select='id,keywords')
        return {short_id(w['id']): sorted({normalize_term(k['display_name'])
                                           for k in w.get('keywords') or [] if k.get('display_name')})
                for w in works}
    semaphore = asyncio.Semaphore(REF_CONCURRENCY)
    out, done = {}, 0

    async def one(sid):
        nonlocal done
        async with semaphore:
            try:
                raw = await srv.client.get_abstract(sid)
                out[sid] = _scopus_terms(raw, 'author_keywords')[0]
            except Exception as exc:
                logger.info(f"keywords failed for {sid}: {exc}")
                out[sid] = []
            done += 1
            jobs.progress(f"research_fronts: keywords {done}/{len(nodes)}")
    await asyncio.gather(*(one(k) for k in nodes))
    return out


async def _research_fronts(arguments: dict) -> list:
    srv = server_module()
    source = _source(arguments)
    nodes, fetch_errors, parse_errors, _ = await collect_corpus(srv, arguments)
    clustering = cluster_citation_network(nodes, float(arguments.get("resolution") or 1.0),
                                          int(arguments.get("min_size") or 3))
    terms_of = await _keywords(srv, arguments, nodes)
    fronts = describe_fronts(nodes, clustering, terms_of)
    records = [dict(n, scopus_id=n['id'] if source == 'scopus' else None,
                    openalex_id=n['id'] if source == 'openalex' else None) for n in nodes.values()]
    main_path = compute_main_path(records)['global_main_path']
    route = path_route(main_path, clustering['front_of'])

    label = arguments.get("query") or f"{len(nodes)} papers"
    base = _base_name(f"fronts-{label}")
    out = _output_dir()
    order = list(nodes)
    net = write_pajek([{'id': k, 'label': _make_node_label(nodes[k], k)} for k in order],
                      [{'source': p, 'target': k} for k in order for p in nodes[k].get('parents') or []
                       if p in nodes], out / f'{base}.net')
    clu = write_pajek_partition(order, clustering['front_of'], out / f'{base}.clu')
    json_path = out / f'{base}.json'
    json_path.write_text(json.dumps({'fronts': fronts, 'front_of': clustering['front_of'],
                                     'main_path': main_path, 'route': route},
                                    ensure_ascii=False, indent=2), encoding='utf-8')

    unclustered = sum(1 for f in clustering['front_of'].values() if f == 0)
    lines = [f"Research fronts of {len(nodes)} papers ({source}): {len(fronts)} fronts "
             f"(Louvain on direct citations, resolution {arguments.get('resolution') or 1.0}); "
             f"{unclustered} papers unclustered.",
             f"Server version: {srv.SERVER_VERSION}",
             f"JSON: {json_path}", f"Pajek .net: {net}", f"Pajek partition .clu (front per paper): {clu}"]
    missing = {**fetch_errors, **parse_errors}
    if missing:
        lines.append(f"Reference lists missing for {len(missing)} paper(s); their fronts are uncertain.")
    for f in fronts:
        years = f"{f['years'][0]}–{f['years'][1]}, median {f['median_year']}" if f['years'] else 'years unknown'
        core = '; '.join(f"{p['label']} ({p['lcs']})" for p in f['core_papers'])
        lines.append(f"F{f['front']}: {f['size']} papers, {years}, density {f['density']:g}; "
                     f"keywords: {', '.join(f['keywords']) or '—'}; core: {core}")
    if main_path:
        steps = ' → '.join(f"{_make_node_label(nodes[k], k)} [F{route['fronts'][i]}]"
                           for i, k in enumerate(main_path))
        lines.append(f"Main path across fronts: {steps}")
        if route['hops']:
            robust = hop_robustness(nodes, route['hops'], min_size=int(arguments.get("min_size") or 3))
            lines.append(f"The path hops {len(route['hops'])} time(s) (in brackets: at how many of the "
                         "resolutions 0.5, 1.0, 1.5 the two papers fall in different fronts): " + '; '.join(
                             f"{_make_node_label(nodes[a], a)} (F{fa}) → {_make_node_label(nodes[b], b)} "
                             f"(F{fb}) [{r}/3]" for (a, b, fa, fb), r in zip(route['hops'], robust))
                         + ". Hops are where the path joins two conversations; check them with "
                         "path_transmission.")
        else:
            lines.append("The main path stays in one front.")
    lines.append("F0 = unclustered. Fronts depend on the resolution; a hop marked 3/3 holds at all "
                 "three tested resolutions.")
    return [types.TextContent(type="text", text='\n'.join(lines))]


HANDLERS = {'research_fronts': _research_fronts}
