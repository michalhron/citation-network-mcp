"""Corpus-level tools: within-set citation network with main path and key
routes, verified citer sets, and citation contexts."""
import asyncio
import json
import logging
from datetime import datetime

import mcp.types as types

from ..baskets import SCOPE_SCHEMA, openalex_issn_filter, resolve_scope, scope_scopus_query
from ..completeness import assess, crossref_reference_counts
from ..graphs import _make_node_label
from ..lineage import compute_main_path, key_route_paths, write_pajek
from ..openalex import bare_doi, clean_openalex_work, openalex_work_key, short_id
from ..output import _output_dir, _query_slug
from ..records import (
    clean_abstract_details,
    clean_references,
    clean_search_results,
    reported_reference_total,
    to_eid,
    to_scopus_id,
)
from ..semantic_scholar import citation_contexts
from .common import SOURCE_SCHEMA, _resolve_openalex_work, _source, server_module

logger = logging.getLogger("scopus-plus-mcp")

MAX_CORPUS = 1000
REF_CONCURRENCY = 4
EID_BATCH = 25          # EIDs per metadata search (one Scopus page)
INLINE_EDGE_LIMIT = 2000
MAX_CONTEXT_PAIRS = 50

INLINE_SCHEMA = {
    "type": "string",
    "enum": ["summary", "edges", "full"],
    "default": "edges",
    "description": (
        "What the reply carries besides the file paths. 'summary': counts, "
        "flags and paths. 'edges' (default): also every edge as a compact "
        "line (up to 2,000). 'full': the whole corpus as JSON, for callers "
        "that cannot read the server's files (cloud sessions)."
    ),
}


TOOLS = [
    types.Tool(
        name="citation_network",
        description=(
            "Direct-citation network within a set of papers, in one call: fetches "
            "every paper's reference list, keeps only the references to other "
            "papers in the set, and runs main-path analysis (SPC weights, local "
            "and global main path, key routes). Flags papers whose reference list "
            "looks short against the Crossref count, since they silently lose "
            "edges. Give ids (Scopus IDs/EIDs; with source='openalex', DOIs or "
            "OpenAlex IDs) or a query. Writes the corpus as JSON, the network as "
            "Pajek .net (arcs run from cited to citing paper, weighted by SPC; "
            "opens in Pajek, VOSviewer and Gephi) and an edge CSV. Cost: one "
            "reference request per paper (cached)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"The papers (up to {MAX_CORPUS}). Use this or query.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query defining the set, instead of ids.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "With query: how many papers to include (default 300).",
                    "default": 300,
                },
                "scope": SCOPE_SCHEMA,
                "source": SOURCE_SCHEMA,
                "key_routes": {
                    "type": "integer",
                    "description": (
                        "Number of top-SPC key edges to extend into key-route main "
                        "paths (Liu & Lu 2012; default 10, 0 = none)."
                    ),
                    "default": 10,
                },
                "check_completeness": {
                    "type": "boolean",
                    "description": (
                        "Compare each reference list with the Crossref reference "
                        "count (default true; one Crossref request per DOI)."
                    ),
                    "default": True,
                },
                "inline": INLINE_SCHEMA,
            },
        },
    ),
    types.Tool(
        name="resolve_citers",
        description=(
            "All papers citing one or more seed papers, found by several search "
            "strategies at once and verified. Runs REF() on each seed plus any "
            "extra queries (for example a title-phrase query), merges the hits, "
            "then checks each hit's own reference list for the seeds. Reports "
            "what each strategy found and missed, hits that cite no seed (false "
            "positives, or a truncated reference list), and the confirmed set. "
            "Scopus cost: one search page per 25 hits per strategy, plus one "
            "reference request per hit when verify is on (cached, and reused by "
            "citation_network)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "seed_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Seed papers (Scopus IDs/EIDs; with source='openalex', DOIs "
                        "or OpenAlex IDs), e.g. both papers of a construct's origin."
                    ),
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Extra search strategies, e.g. REF(\"organizing vision\") "
                        "or REFAUTH(swanson) AND REFTITLE(\"organizing vision\")."
                    ),
                },
                "scope": SCOPE_SCHEMA,
                "source": SOURCE_SCHEMA,
                "verify": {
                    "type": "boolean",
                    "description": "Check each hit's reference list for the seeds (default true).",
                    "default": True,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Cap on hits per strategy (default 1000).",
                    "default": 1000,
                },
                "inline": {
                    "type": "string",
                    "enum": ["summary", "compact"],
                    "default": "compact",
                    "description": (
                        "'compact' (default): every hit as one JSON line (ID, DOI, "
                        "year, title, seeds cited, strategies). 'summary': counts only."
                    ),
                },
            },
            "required": ["seed_ids"],
        },
    ),
    types.Tool(
        name="citation_context",
        description=(
            "How one paper cites another: the citing sentences, the citation "
            "intent (background, methodology, result) and whether Semantic "
            "Scholar classes the citation as influential. Evidence for whether "
            "a citation edge carries the cited idea or is a passing mention. "
            f"Up to {MAX_CONTEXT_PAIRS} pairs per call; IDs are DOIs, Scopus IDs "
            "or OpenAlex IDs. Contexts are missing for some publishers, and for "
            "papers Semantic Scholar does not hold."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "citing": {"type": "string", "description": "The citing paper (single pair)."},
                "cited": {"type": "string", "description": "The cited paper (single pair)."},
                "pairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citing": {"type": "string"},
                            "cited": {"type": "string"},
                        },
                        "required": ["citing", "cited"],
                    },
                    "description": "Several [citing, cited] pairs, instead of citing/cited.",
                },
            },
        },
    ),
]


# ── shared helpers ───────────────────────────────────────────────────────


def _base_name(slug: str) -> str:
    ts = datetime.now().strftime('%Y%m%dT%H%M%S')
    return f'scopus-{_query_slug(slug)}-{ts}'


async def _scopus_records_for_ids(client, ids):
    """Search-result metadata for Scopus IDs, 25 per EID() search."""
    ids = [to_scopus_id(i) for i in ids]
    found = {}
    for i in range(0, len(ids), EID_BATCH):
        chunk = ids[i:i + EID_BATCH]
        query = ' OR '.join(f'EID({to_eid(x)})' for x in chunk)
        raw = await client.search_all(query, max_results=len(chunk), sort='coverDate')
        for rec in clean_search_results(raw):
            found[rec['scopus_id']] = rec
    return [found.get(x) or {'scopus_id': x, 'metadata_missing': True} for x in ids]


async def _gather_limited(items, fn, limit=REF_CONCURRENCY):
    semaphore = asyncio.Semaphore(limit)

    async def run(item):
        async with semaphore:
            try:
                return item, await fn(item), None
            except Exception as exc:
                return item, None, str(exc)[:300]
    return await asyncio.gather(*(run(i) for i in items))


def _year(rec):
    return rec.get('year') or (str(rec.get('cover_date') or '')[:4] or None)


def _node(rec, key):
    return {
        'id': key,
        'scopus_id': rec.get('scopus_id'),
        'openalex_id': rec.get('openalex_id'),
        'doi': rec.get('doi'),
        'title': rec.get('title'),
        'creator': rec.get('creator'),
        'year': _year(rec),
        'cover_date': rec.get('cover_date'),
        'venue': rec.get('publication_name'),
        'issn': rec.get('issn'),
        'cited_by_count': rec.get('cited_by_count'),
    }


# ── citation_network ─────────────────────────────────────────────────────


async def _collect_scopus(client, arguments):
    ids, query = arguments.get("ids"), arguments.get("query")
    issns = resolve_scope(arguments.get("scope"))
    if ids and issns:
        raise ValueError("scope filters a query; with ids, pass only in-scope papers.")
    if ids:
        recs = await _scopus_records_for_ids(client, ids)
    else:
        raw = await client.search_all(scope_scopus_query(query, issns),
                                      max_results=int(arguments.get("max_results", 300)),
                                      sort='citedby-count')
        recs = clean_search_results(raw)
    nodes = {r['scopus_id']: _node(r, r['scopus_id']) for r in recs if r.get('scopus_id')}

    async def refs_of(sid):
        return await client.get_references(sid)
    results = await _gather_limited(list(nodes), refs_of)
    refs, errors = {}, {}
    for sid, raw, err in results:
        if err:
            errors[sid] = err
            continue
        cleaned = clean_references(raw)
        refs[sid] = cleaned
        nodes[sid]['refs_retrieved'] = len(cleaned)
        nodes[sid]['refs_reported'] = reported_reference_total(raw)
    by_doi = {n['doi'].lower(): k for k, n in nodes.items() if n.get('doi')}
    for sid, cleaned in refs.items():
        parents = []
        for r in cleaned:
            target = r.get('scopus_id') if r.get('scopus_id') in nodes else \
                by_doi.get((r.get('doi') or '').lower())
            if target and target != sid and target not in parents:
                parents.append(target)
        nodes[sid]['parents'] = parents
    return nodes, errors


async def _collect_openalex(openalex, arguments):
    ids, query = arguments.get("ids"), arguments.get("query")
    issns = resolve_scope(arguments.get("scope"))
    works, errors = [], {}
    if ids:
        for i in ids:
            try:
                works.append(await _resolve_openalex_work(i))
            except Exception as exc:
                errors[str(i)] = str(exc)[:300]
    else:
        kwargs = {'extra_filter': openalex_issn_filter(issns)} if issns else {}
        works, _ = await openalex.search(query, max_results=int(arguments.get("max_results", 300)),
                                         sort='citedby', **kwargs)
    nodes = {}
    for w in works:
        rec = clean_openalex_work(w)
        key = rec['openalex_id']
        if not key:
            continue
        nodes[key] = _node(rec, key)
        ref_ids = [short_id(r) for r in (w.get('referenced_works') or [])]
        nodes[key]['refs_retrieved'] = len(ref_ids)
        nodes[key]['_ref_ids'] = ref_ids
    for key, n in nodes.items():
        n['parents'] = [r for r in dict.fromkeys(n.pop('_ref_ids')) if r in nodes and r != key]
    return nodes, errors


def _label(nodes, key):
    return _make_node_label(nodes.get(key, {}), key)


async def _citation_network(arguments: dict) -> list:
    srv = server_module()
    source = _source(arguments)
    ids, query = arguments.get("ids"), arguments.get("query")
    if bool(ids) == bool(query):
        raise ValueError("Give either ids or query.")
    if ids and len(ids) > MAX_CORPUS:
        raise ValueError(f"At most {MAX_CORPUS} ids per call.")
    inline = arguments.get("inline") or "edges"
    if inline not in ("summary", "edges", "full"):
        raise ValueError("inline must be 'summary', 'edges' or 'full'")
    k = int(arguments.get("key_routes", 10))
    check = arguments.get("check_completeness", True)

    if source == 'openalex':
        nodes, errors = await _collect_openalex(srv.openalex, arguments)
    else:
        nodes, errors = await _collect_scopus(srv.client, arguments)
    if not nodes:
        raise ValueError("No papers found for the given ids or query.")

    # Completeness against Crossref.
    short = []
    if check:
        counts = await crossref_reference_counts(n['doi'] for n in nodes.values() if n.get('doi'))
        for key, n in nodes.items():
            cr = counts.get((n.get('doi') or '').lower())
            n['crossref_refs'] = cr
            n['completeness'] = assess(n.get('refs_retrieved'), cr) if key not in errors else 'unknown'
            if n['completeness'] == 'short':
                short.append(key)

    records = [dict(n, scopus_id=n['id'] if source == 'scopus' else None,
                    openalex_id=n['id'] if source == 'openalex' else None)
               for n in nodes.values()]
    mp = compute_main_path(records)
    routes = key_route_paths(mp['edges'], k) if k > 0 else None
    edges = [{'citing': e['target'], 'cited': e['source'], 'spc': e['spc_weight']}
             for e in mp['edges']]
    in_network = {e['citing'] for e in edges} | {e['cited'] for e in edges}
    isolated = [key for key in nodes if key not in in_network]

    base = _base_name(f"network-{query or f'{len(nodes)}-ids'}")
    out = _output_dir()
    corpus = {
        'source': source,
        'query': query,
        'nodes': list(nodes.values()),
        'edges': edges,
        'main_path': mp['main_path'],
        'global_main_path': mp['global_main_path'],
        'key_routes': routes,
        'removed_cycle_edges': mp.get('removed_cycle_edges') or [],
        'reference_fetch_errors': errors,
        'server_version': srv.SERVER_VERSION,
    }
    json_path = out / f'{base}.json'
    json_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding='utf-8')
    net_path = write_pajek(
        [{'id': key, 'label': _label(nodes, key)} for key in nodes],
        [{'source': e['cited'], 'target': e['citing'], 'weight': e['spc']} for e in edges],
        out / f'{base}.net')
    csv_path = out / f'{base}-edges.csv'
    csv_path.write_text('citing,cited,spc\n' + ''.join(
        f"{e['citing']},{e['cited']},{e['spc']}\n" for e in edges), encoding='utf-8')

    lines = [
        f"Citation network ({source}): {len(nodes)} papers, {len(edges)} within-set "
        f"citation edges, {len(isolated)} papers with no edge.",
        f"Server version: {srv.SERVER_VERSION}",
        f"Corpus JSON: {json_path}",
        f"Pajek .net (cited → citing, SPC weights): {net_path}",
        f"Edge CSV: {csv_path}",
    ]
    if errors:
        lines.append(f"Reference lists that failed to load ({len(errors)}): "
                     + '; '.join(f"{k}: {v[:80]}" for k, v in list(errors.items())[:5]))
    if check:
        unknown = sum(1 for n in nodes.values() if n.get('completeness') == 'unknown')
        lines.append(f"Completeness vs Crossref: {len(short)} short, "
                     f"{len(nodes) - len(short) - unknown} ok, {unknown} unknown.")
        for key in short[:25]:
            n = nodes[key]
            lines.append(f"  SHORT {key} {_label(nodes, key)}: {n.get('refs_retrieved')} "
                         f"references retrieved, Crossref lists {n.get('crossref_refs')}. "
                         f"{(n.get('title') or '')[:80]}")
    if mp.get('removed_cycle_edges'):
        lines.append(f"Cycles: removed {len(mp['removed_cycle_edges'])} edge(s) running "
                     "against publication order: " + ', '.join(
                         f"{e['source']}→{e['target']}" for e in mp['removed_cycle_edges'][:10]))
    if mp['main_path']:
        lines.append("Main path (local): " + ' → '.join(_label(nodes, x) for x in mp['main_path']))
        lines.append("Main path (global): " + ' → '.join(_label(nodes, x) for x in mp['global_main_path']))
    else:
        lines.append(f"Main path: {mp.get('note') or 'none'}")
    if routes and routes['routes']:
        lines.append(f"Key routes (top {k} SPC edges): {len(routes['nodes'])} papers, "
                     f"{len(routes['edges'])} edges.")
        for r in routes['routes']:
            lines.append(f"  [{r['spc_weight']}] " + ' → '.join(_label(nodes, x) for x in r['path']))
    lines.append("Node key: " + '; '.join(
        f"{key} = {_label(nodes, key)}" for key in (mp['global_main_path'] or [])))

    if inline == 'full':
        lines.append("Corpus JSON:\n" + json.dumps(corpus, ensure_ascii=False))
    elif inline == 'edges':
        if len(edges) <= INLINE_EDGE_LIMIT:
            lines.append("Edges (citing cited spc):\n" + '\n'.join(
                f"{e['citing']} {e['cited']} {e['spc']}" for e in edges))
        else:
            lines.append(f"{len(edges)} edges: too many to list; see the CSV or use inline='full'.")
    return [types.TextContent(type="text", text='\n'.join(lines))]


# ── resolve_citers ───────────────────────────────────────────────────────


async def _resolve_citers(arguments: dict) -> list:
    srv = server_module()
    source = _source(arguments)
    seeds_raw = arguments.get("seed_ids") or []
    if not seeds_raw:
        raise ValueError("seed_ids is required")
    queries = arguments.get("queries") or []
    issns = resolve_scope(arguments.get("scope"))
    verify = arguments.get("verify", True)
    cap = int(arguments.get("max_results", 1000))
    inline = arguments.get("inline") or "compact"

    hits = {}      # key -> record
    found_by = {}  # key -> [strategy]
    seeds = {}     # seed key -> {'doi', 'label'}
    strategy_counts = {}

    def add(strategy, recs, key_field):
        strategy_counts[strategy] = len(recs)
        for r in recs:
            key = r.get(key_field)
            if not key:
                continue
            hits.setdefault(key, r)
            found_by.setdefault(key, []).append(strategy)

    if source == 'openalex':
        oa = srv.openalex
        kwargs = {'extra_filter': openalex_issn_filter(issns)} if issns else {}
        raw_by_key = {}
        for s in seeds_raw:
            work = await _resolve_openalex_work(s)
            wid = short_id(work['id'])
            rec = clean_openalex_work(work)
            seeds[wid] = {'doi': rec['doi'], 'label': _make_node_label(rec, wid)}
            works, _ = await oa.citing(wid, max_results=cap, sort='citedby', **kwargs)
            raw_by_key.update({short_id(w['id']): w for w in works})
            add(f"cites:{wid}", [clean_openalex_work(w) for w in works], 'openalex_id')
        for q in queries:
            works, _ = await oa.search(q, max_results=cap, sort='citedby', **kwargs)
            raw_by_key.update({short_id(w['id']): w for w in works})
            add(q, [clean_openalex_work(w) for w in works], 'openalex_id')
        cites = {k: [s for s in seeds if s in {short_id(r) for r in
                                               (raw_by_key[k].get('referenced_works') or [])}]
                 for k in hits} if verify else {}
        errors = {}
    else:
        client = srv.client
        for s in seeds_raw:
            sid = to_scopus_id(s)
            details = clean_abstract_details(await client.get_abstract(sid))
            seeds[sid] = {'doi': (details.get('doi') or '').lower() or None,
                          'label': _make_node_label(
                              {'creator': ((details.get('authors') or [{}])[0] or {}).get('surname'),
                               'year': (details.get('cover_date') or '')[:4],
                               'title': details.get('title')}, sid)}
            raw = await client.search_all(scope_scopus_query(f"REF({to_eid(sid)})", issns),
                                          max_results=cap, sort='citedby-count')
            add(f"REF({to_eid(sid)})", clean_search_results(raw), 'scopus_id')
        for q in queries:
            raw = await client.search_all(scope_scopus_query(q, issns),
                                          max_results=cap, sort='citedby-count')
            add(q, clean_search_results(raw), 'scopus_id')
        cites, errors = {}, {}
        if verify:
            seed_dois = {v['doi']: k for k, v in seeds.items() if v['doi']}
            results = await _gather_limited(list(hits), client.get_references)
            for key, raw, err in results:
                if err:
                    errors[key] = err
                    continue
                found = []
                for r in clean_references(raw):
                    s = r.get('scopus_id') if r.get('scopus_id') in seeds else \
                        seed_dois.get((r.get('doi') or '').lower())
                    if s and s not in found:
                        found.append(s)
                cites[key] = found

    seed_keys = set(seeds)
    for s in seed_keys:  # a seed citing another seed is not a hit
        hits.pop(s, None)
    rows = []
    for key, r in hits.items():
        row = {
            'id': key, 'doi': r.get('doi'), 'year': _year(r),
            'creator': r.get('creator'), 'title': r.get('title'),
            'venue': r.get('publication_name'), 'found_by': found_by[key],
        }
        if verify:
            if key in errors:
                row['verified'] = None
                row['error'] = errors[key]
            else:
                row['cites'] = cites.get(key, [])
                row['verified'] = bool(row['cites'])
        rows.append(row)
    rows.sort(key=lambda r: (str(r['year'] or ''), r['id']))

    confirmed = [r for r in rows if r.get('verified')]
    unconfirmed = [r for r in rows if r.get('verified') is False]
    base = _base_name(f"citers-{'-'.join(seed_keys)}")
    out = _output_dir()
    json_path = out / f'{base}.json'
    json_path.write_text(json.dumps({'seeds': seeds, 'strategies': strategy_counts,
                                     'hits': rows}, ensure_ascii=False, indent=2),
                         encoding='utf-8')

    seed_text = ', '.join(f"{k} ({v['label']})" for k, v in seeds.items())
    lines = [f"Citers of {seed_text} "
             f"({source}): {len(rows)} distinct hits across {len(strategy_counts)} strategies.",
             f"Server version: {srv.SERVER_VERSION}", f"Results JSON: {json_path}"]
    if issns:
        lines.append(f"Scope: {len(issns)} ISSNs.")
    for strategy, n in strategy_counts.items():
        mine = {r['id'] for r in rows if strategy in r['found_by']}
        line = f"  {strategy}: {n} hits"
        if verify:
            conf = {r['id'] for r in confirmed}
            line += (f", {len(mine & conf)} confirmed; misses {len(conf - mine)} "
                     f"confirmed citers found by other strategies")
        lines.append(line)
    if verify:
        lines.append(f"Confirmed (cite at least one seed in their Scopus/OpenAlex "
                     f"reference list): {len(confirmed)}. Unconfirmed: {len(unconfirmed)} "
                     f"(false positives, or reference lists missing the seed; check "
                     f"with get_references(check_completeness=true)). Not checked "
                     f"(reference fetch failed): {len(errors)}.")
    if inline == 'compact':
        lines.append("Hits, one JSON object per line:")
        lines.extend(json.dumps(r, ensure_ascii=False) for r in rows)
    return [types.TextContent(type="text", text='\n'.join(lines))]


# ── citation_context ─────────────────────────────────────────────────────


async def _doi_and_title(identifier: str):
    """(doi, title) for a DOI, Scopus ID/EID or OpenAlex ID."""
    srv = server_module()
    ident = str(identifier).strip()
    low = ident.lower()
    for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
        if low.startswith(prefix):
            low = low[len(prefix):]
    if low.startswith('10.') and '/' in low:
        return low, None
    key = openalex_work_key(ident)
    if key:
        work = await srv.openalex.get_work(ident)
        if not work:
            raise ValueError(f"OpenAlex has no work {ident!r}")
        return (bare_doi(work.get('doi')) or '').lower() or None, work.get('title')
    details = clean_abstract_details(await srv.client.get_abstract(to_scopus_id(ident)))
    return (details.get('doi') or '').lower() or None, details.get('title')


async def _citation_context(arguments: dict) -> list:
    pairs_in = arguments.get("pairs") or []
    if arguments.get("citing") and arguments.get("cited"):
        pairs_in = [{'citing': arguments['citing'], 'cited': arguments['cited']}] + list(pairs_in)
    if not pairs_in:
        raise ValueError("Give citing and cited, or pairs.")
    if len(pairs_in) > MAX_CONTEXT_PAIRS:
        raise ValueError(f"At most {MAX_CONTEXT_PAIRS} pairs per call.")

    resolved, problems, cache = [], [], {}
    for p in pairs_in:
        try:
            for side in ('citing', 'cited'):
                if p[side] not in cache:
                    cache[p[side]] = await _doi_and_title(p[side])
            citing_doi, citing_title = cache[p['citing']]
            cited_doi, cited_title = cache[p['cited']]
            if not citing_doi and not cited_doi:
                raise ValueError("neither paper has a DOI; Semantic Scholar lookup needs one")
            resolved.append({'citing': p['citing'], 'cited': p['cited'],
                             'citing_doi': citing_doi, 'citing_title': citing_title,
                             'cited_doi': cited_doi, 'cited_title': cited_title})
        except Exception as exc:
            problems.append({'citing': p.get('citing'), 'cited': p.get('cited'),
                             'context': {'status': 'error', 'detail': str(exc)[:200]}})
    results = await citation_contexts(resolved) + problems
    lines = [f"Citation contexts from Semantic Scholar for {len(results)} pair(s)."]
    for r in results:
        c = r['context']
        head = f"{r['citing']} → {r['cited']}: {c['status']}"
        if c['status'] == 'found':
            head += (f"; intents: {', '.join(c['intents']) or 'none given'}; "
                     f"influential: {str(c['is_influential']).lower()}; "
                     f"{c['n_contexts']} context(s); via {c.get('via')}")
        elif c.get('detail'):
            head += f" ({c['detail']})"
        lines.append(head)
        for ctx in c.get('contexts') or []:
            lines.append(f"    “{ctx}”")
        if c.get('note'):
            lines.append(f"    {c['note']}")
    return [types.TextContent(type="text", text='\n'.join(lines))]


HANDLERS = {
    'citation_network': _citation_network,
    'resolve_citers': _resolve_citers,
    'citation_context': _citation_context,
}
