"""Corpus-level tools: within-set citation network with main path and key
routes, verified citer sets, and citation contexts.

Every per-paper step is isolated: one malformed record or failed request is
reported against that paper and never aborts the batch.
"""
import asyncio
import json
import logging
import math
import re
from datetime import datetime

import mcp.types as types

from .. import jobs
from ..baskets import (
    SCOPE_SCHEMA,
    normalize_issn,
    openalex_issn_filter,
    resolve_scope,
    scope_scopus_query,
)
from ..completeness import RULE_TEXT, assess, external_reference_counts
from ..graphs import _make_node_label
from ..lineage import compute_main_path, key_route_paths, write_pajek
from ..openalex import bare_doi, clean_openalex_work, normalize_title, openalex_work_key, short_id
from ..output import _output_dir, _query_slug
from ..records import (
    clean_abstract_details,
    clean_references,
    clean_search_results,
    reported_reference_total,
    to_eid,
    to_scopus_id,
)
from ..client import FULLTEXT_MIN_CHARS
from ..fulltext_contexts import contexts_from_text
from ..semantic_scholar import (
    DEFAULT_MAX_CONTEXTS,
    citation_contexts,
    citing_papers,
    clean_contexts,
    venue_issns,
)
from .common import SOURCE_SCHEMA, _resolve_openalex_work, _source, server_module

logger = logging.getLogger("scopus-plus-mcp")

MAX_CORPUS = 1000
REF_CONCURRENCY = 4
EID_BATCH = 25          # EIDs or DOIs per metadata search (one Scopus page)
INLINE_EDGE_LIMIT = 2000
MAX_CONTEXT_PAIRS = 50
RETRY_PAUSE = 5.0       # seconds before the final pass over rate-limited papers
DEFAULT_PAGE_SIZE = 50

VENUE_ABBR = {
    'mis quarterly': 'MISQ', 'mis quarterly management information systems': 'MISQ',
    'information systems research': 'ISR',
    'journal of management information systems': 'JMIS',
    'european journal of information systems': 'EJIS',
    'information systems journal': 'ISJ',
    'journal of the association for information systems': 'JAIS',
    'journal of information technology': 'JIT',
    'journal of strategic information systems': 'JSIS',
    'the journal of strategic information systems': 'JSIS',
    'organization science': 'OrgSci',
    'management science': 'MgmtSci',
}

INLINE_SCHEMA = {
    "type": "string",
    "enum": ["summary", "edges", "nodes", "full"],
    "default": "edges",
    "description": (
        "What the reply carries besides the file paths. 'summary': counts, "
        "flags and paths. 'edges' (default): also every edge as a compact line "
        "(up to 2,000). 'nodes': one compact line per paper (ID, author year, "
        "venue, references retrieved/reported, comparison count and source, "
        "completeness, error) plus the edges: node-level data for callers that "
        "cannot read the server's files, about 25k characters for 150 papers. "
        "'full': the corpus as JSON, paged by page/page_size nodes."
    ),
}


TOOLS = [
    types.Tool(
        name="citation_network",
        description=(
            "Direct-citation network within a set of papers, in one call: fetches "
            "every paper's reference list, keeps only the references to other "
            "papers in the set, and runs main-path analysis (SPC weights, local "
            "and global main path, key routes). Give ids (Scopus IDs/EIDs; with "
            "source='openalex', DOIs or OpenAlex IDs) or a query. "
            "Completeness: each list is compared with an independent reference "
            "count (Crossref, else OpenAlex, else Semantic Scholar; the source is "
            f"reported per paper); {RULE_TEXT}. Papers whose references could not "
            "be loaded or parsed are listed, retried once after rate limits, and "
            "the main path is marked provisional while any are missing. Likely "
            "duplicate records are listed. Writes JSON, Pajek .net (arcs from "
            "cited to citing, SPC weights; Pajek, VOSviewer, Gephi) and an edge "
            "CSV. Cost: one reference request per paper (cached). Sets over about "
            "50 papers may return a job ID: poll job_status, then job_result."
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
                        "paths (Liu & Lu 2012; default 10, 0 = none). Key edges that "
                        "extend into the same route are merged."
                    ),
                    "default": 10,
                },
                "check_completeness": {
                    "type": "boolean",
                    "description": "Compare each reference list with an independent count (default true).",
                    "default": True,
                },
                "inline": INLINE_SCHEMA,
                "page": {"type": "integer", "default": 1,
                         "description": "With inline='full': which page of nodes (1-based)."},
                "page_size": {"type": "integer", "default": DEFAULT_PAGE_SIZE,
                              "description": "With inline='full': nodes per page (default 50)."},
            },
        },
    ),
    types.Tool(
        name="resolve_citers",
        description=(
            "All papers citing one or more seed papers, found by several search "
            "strategies at once and verified. Runs REF() on each seed plus any "
            "extra queries (for example a title-phrase query), merges the hits, "
            "then checks each hit's own reference list for the seeds. Reports a "
            "per-strategy table (hits, confirmed, unconfirmed, verification "
            "failed, confirmed citers the strategy missed). With cross_check "
            "(default on when scope is given) it also asks OpenAlex and Semantic "
            "Scholar which in-scope papers cite the seeds and lists those Scopus "
            "misses or cannot confirm, with Scopus's reference count against the "
            "external one, so truncated Scopus reference lists become visible. "
            "The Scopus result stays Scopus-only. Long runs may return a job ID: "
            "poll job_status, then job_result."
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
                "cross_check": {
                    "type": "boolean",
                    "description": (
                        "Scopus only: list in-scope citers that OpenAlex or Semantic "
                        "Scholar know and Scopus misses (default: on when scope is set)."
                    ),
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
                        "year, title, status, seeds found in its references, "
                        "strategies). 'summary': counts only."
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
            "or OpenAlex IDs, resolved to Semantic Scholar by DOI, MAG ID, then "
            "title and year (the route is reported). Statuses: found, "
            "contexts_withheld (the citation is known, its sentences are not), "
            "edge_absent_in_s2, citing_paper_unresolved, cited_paper_unresolved. "
            "Contexts are cleaned of page headers and citation-free noise and "
            "ranked, most informative first. Where Semantic Scholar has no usable "
            "sentences, the citing paper's full text is searched instead "
            "(context_source: semantic_scholar, fulltext_sciencedirect, "
            "fulltext_oa or none)."
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
                "max_contexts": {
                    "type": "integer",
                    "default": DEFAULT_MAX_CONTEXTS,
                    "description": "Most contexts returned per pair (default 3).",
                },
                "construct_terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Terms that make a context more informative, e.g. ['organizing vision'].",
                },
                "fulltext_fallback": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "When Semantic Scholar has no usable sentences, fetch the citing "
                        "paper's full text (ScienceDirect, then open access), find the cited "
                        "work in its reference list and return the sentences that cite it."
                    ),
                },
            },
        },
    ),
]


# ── shared helpers ───────────────────────────────────────────────────────


def _base_name(slug: str) -> str:
    ts = datetime.now().strftime('%Y%m%dT%H%M%S')
    return f'scopus-{_query_slug(slug)}-{ts}'


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:240]}"


def _is_rate_limit(message: str) -> bool:
    return '429' in message or 'Rate limit' in message


def venue_abbr(venue) -> str:
    v = ' '.join(str(venue or '').replace(':', ' ').split()).lower()
    if v in VENUE_ABBR:
        return VENUE_ABBR[v]
    words = [w for w in v.split() if w not in ('of', 'the', 'and', 'for', 'in', 'on', '&')]
    return ''.join(w[0].upper() for w in words)[:10] or '?'


async def _scopus_records_for(client, field, values):
    """Search-result metadata for Scopus IDs (field 'EID') or DOIs ('DOI'),
    25 per search. Returns {value: record}; missing values are absent."""
    found = {}
    for i in range(0, len(values), EID_BATCH):
        chunk = values[i:i + EID_BATCH]
        if field == 'EID':
            query = ' OR '.join(f'EID({to_eid(x)})' for x in chunk)
        else:
            query = ' OR '.join(f'DOI("{x}")' for x in chunk)
        raw = await client.search_all(query, max_results=len(chunk), sort='coverDate')
        for rec in clean_search_results(raw):
            key = rec.get('scopus_id') if field == 'EID' else (rec.get('doi') or '').lower()
            if key:
                found[key] = rec
    return found


async def _fetch_references(client, ids, label):
    """{id: raw REF response} and {id: error} for every ID, four at a time.

    Rate-limited papers get one more, sequential pass after a pause, before
    anything is computed from the lists."""
    raw, errors = {}, {}
    done = 0
    semaphore = asyncio.Semaphore(REF_CONCURRENCY)

    async def one(sid):
        nonlocal done
        async with semaphore:
            try:
                raw[sid] = await client.get_references(sid)
            except Exception as exc:
                errors[sid] = _err(exc)
            done += 1
            jobs.progress(f"{label}: reference lists {done}/{len(ids)}")

    await asyncio.gather(*(one(i) for i in ids))
    limited = [sid for sid, e in errors.items() if _is_rate_limit(e)]
    if limited:
        jobs.progress(f"{label}: retrying {len(limited)} rate-limited reference list(s)")
        await asyncio.sleep(RETRY_PAUSE)
        for sid in limited:
            try:
                raw[sid] = await client.get_references(sid)
                errors.pop(sid)
            except Exception as exc:
                errors[sid] = _err(exc)
    return raw, errors


def _year(rec):
    return rec.get('year') or (str(rec.get('cover_date') or '')[:4] or None)


def _node(rec, key):
    return {
        'id': key,
        'scopus_id': rec.get('scopus_id'),
        'openalex_id': rec.get('openalex_id'),
        'doi': (rec.get('doi') or '').lower() or None,
        'title': rec.get('title'),
        'creator': rec.get('creator'),
        'year': _year(rec),
        'cover_date': rec.get('cover_date'),
        'venue': rec.get('publication_name'),
        'issn': rec.get('issn'),
        'cited_by_count': rec.get('cited_by_count'),
    }


def _label(nodes, key):
    return _make_node_label(nodes.get(key, {}), key)


def possible_duplicates(nodes: dict) -> list:
    """Groups of node IDs sharing a DOI, or a normalized title and year."""
    groups = {}
    for key, n in nodes.items():
        if n.get('doi'):
            groups.setdefault(('doi', n['doi']), []).append(key)
        title = normalize_title(n.get('title'))
        if title:
            groups.setdefault(('title', title, n.get('year')), []).append(key)
    seen, out = set(), []
    for ids in groups.values():
        ids = sorted(set(ids))
        if len(ids) > 1 and tuple(ids) not in seen:
            seen.add(tuple(ids))
            out.append(ids)
    return out


# ── citation_network ─────────────────────────────────────────────────────


def _link(nodes, refs_by_id, errors):
    """Within-set parents per node, from cleaned reference lists; a node
    whose linking fails is recorded in errors, not raised."""
    by_doi = {n['doi']: k for k, n in nodes.items() if n.get('doi')}
    for sid, cleaned in refs_by_id.items():
        try:
            parents = []
            for r in cleaned:
                target = r.get('scopus_id') if r.get('scopus_id') in nodes else \
                    by_doi.get(r.get('doi') or '')
                if target and target != sid and target not in parents:
                    parents.append(target)
            nodes[sid]['parents'] = parents
        except Exception as exc:
            errors[sid] = _err(exc)
            nodes[sid]['parents'] = []


async def _collect_scopus(client, arguments):
    ids, query = arguments.get("ids"), arguments.get("query")
    issns = resolve_scope(arguments.get("scope"))
    if ids and issns:
        raise ValueError("scope filters a query; with ids, pass only in-scope papers.")
    if ids:
        wanted = [to_scopus_id(i) for i in ids]
        found = await _scopus_records_for(client, 'EID', wanted)
        recs = [found.get(x) or {'scopus_id': x, 'metadata_missing': True} for x in wanted]
    else:
        raw = await client.search_all(scope_scopus_query(query, issns),
                                      max_results=int(arguments.get("max_results", 300)),
                                      sort='citedby-count')
        recs = clean_search_results(raw)
    nodes = {r['scopus_id']: _node(r, r['scopus_id']) for r in recs if r.get('scopus_id')}

    raw_refs, fetch_errors = await _fetch_references(client, list(nodes), 'citation_network')
    parse_errors, refs_by_id = {}, {}
    for sid, data in raw_refs.items():
        try:
            cleaned = clean_references(data)
            refs_by_id[sid] = cleaned
            nodes[sid]['refs_retrieved'] = len(cleaned)
            nodes[sid]['refs_reported'] = reported_reference_total(data)
            block = (data.get('abstracts-retrieval-response') or {}).get('references') or {}
            nodes[sid]['refs_recovered'] = sum(1 for r in cleaned if r.get('recovered_from'))
            nodes[sid]['refs_unparseable'] = int(block.get('@unservable') or 0)
        except Exception as exc:
            parse_errors[sid] = _err(exc)
    _link(nodes, refs_by_id, parse_errors)
    return nodes, fetch_errors, parse_errors


async def _collect_openalex(openalex, arguments):
    ids, query = arguments.get("ids"), arguments.get("query")
    issns = resolve_scope(arguments.get("scope"))
    works, fetch_errors, parse_errors = [], {}, {}
    if ids:
        for i in ids:
            try:
                works.append(await _resolve_openalex_work(i))
            except Exception as exc:
                fetch_errors[str(i)] = _err(exc)
    else:
        kwargs = {'extra_filter': openalex_issn_filter(issns)} if issns else {}
        works, _ = await openalex.search(query, max_results=int(arguments.get("max_results", 300)),
                                         sort='citedby', **kwargs)
    nodes, ref_ids = {}, {}
    for w in works:
        try:
            rec = clean_openalex_work(w)
            key = rec['openalex_id']
            if not key:
                continue
            nodes[key] = _node(rec, key)
            ref_ids[key] = [short_id(r) for r in (w.get('referenced_works') or [])]
            nodes[key]['refs_retrieved'] = len(ref_ids[key])
        except Exception as exc:
            parse_errors[short_id(w.get('id')) or '?'] = _err(exc)
    for key, refs in ref_ids.items():
        nodes[key]['parents'] = [r for r in dict.fromkeys(refs) if r in nodes and r != key]
    return nodes, fetch_errors, parse_errors


def _node_line(key, n, nodes, errors):
    retrieved, reported = n.get('refs_retrieved'), n.get('refs_reported')
    refs = 'refs ?' if retrieved is None else f"refs {retrieved}"
    if reported and reported != retrieved:
        refs += f"/{reported}"
    parts = [key, _label(nodes, key), venue_abbr(n.get('venue')), refs]
    if n.get('external_refs'):
        parts.append(f"ext {n['external_refs']} {n.get('completeness_source')}")
    if n.get('completeness'):
        parts.append(n['completeness'])
    if key in errors:
        parts.append(f"ERROR {errors[key][:70]}")
    return ' | '.join(str(p) for p in parts)


async def _citation_network(arguments: dict) -> list:
    srv = server_module()
    source = _source(arguments)
    ids, query = arguments.get("ids"), arguments.get("query")
    if bool(ids) == bool(query):
        raise ValueError("Give either ids or query.")
    if ids and len(ids) > MAX_CORPUS:
        raise ValueError(f"At most {MAX_CORPUS} ids per call.")
    inline = arguments.get("inline") or "edges"
    if inline not in ("summary", "edges", "nodes", "full"):
        raise ValueError("inline must be 'summary', 'edges', 'nodes' or 'full'")
    k = int(arguments.get("key_routes", 10))
    check = arguments.get("check_completeness", True)

    if source == 'openalex':
        nodes, fetch_errors, parse_errors = await _collect_openalex(srv.openalex, arguments)
    else:
        nodes, fetch_errors, parse_errors = await _collect_scopus(srv.client, arguments)
    if not nodes:
        raise ValueError("No papers found for the given ids or query.")
    errors = {**fetch_errors, **parse_errors}
    missing = [key for key in nodes if key in errors]

    short = []
    if check:
        jobs.progress("citation_network: completeness counts")
        counts = await external_reference_counts(
            (n['doi'] for n in nodes.values() if n.get('doi')), openalex=srv.openalex)
        for key, n in nodes.items():
            ext, src = counts.get(n.get('doi') or '', (None, None))
            n['external_refs'], n['completeness_source'] = ext, src
            n['completeness'] = 'unknown' if key in errors else assess(n.get('refs_retrieved'), ext)
            if n['completeness'] == 'short':
                short.append(key)

    jobs.progress("citation_network: main path")
    records = [dict(n, scopus_id=n['id'] if source == 'scopus' else None,
                    openalex_id=n['id'] if source == 'openalex' else None)
               for n in nodes.values()]
    mp = compute_main_path(records)
    routes = key_route_paths(mp['edges'], k) if k > 0 else None
    edges = [{'citing': e['target'], 'cited': e['source'], 'spc': e['spc_weight']}
             for e in mp['edges']]
    in_network = {e['citing'] for e in edges} | {e['cited'] for e in edges}
    isolated = [key for key in nodes if key not in in_network]
    duplicates = possible_duplicates(nodes)

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
        'reference_fetch_errors': fetch_errors,
        'paper_errors': parse_errors,
        'possible_duplicates': duplicates,
        'provisional': bool(missing),
        'completeness_rule': RULE_TEXT,
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
    ]
    if fetch_errors:
        lines.append(f"Reference lists that failed to load ({len(fetch_errors)}): " + '; '.join(
            f"{key} ({e[:90]})" for key, e in list(fetch_errors.items())[:10]))
    if parse_errors:
        lines.append(f"Papers skipped after errors ({len(parse_errors)}): " + '; '.join(
            f"{key} ({e[:90]})" for key, e in list(parse_errors.items())[:10]))
    if missing:
        lines.append(f"PROVISIONAL: main path computed with {len(missing)} of {len(nodes)} "
                     "reference lists missing; results may change. Rerun later (responses "
                     "are cached, so only the missing lists are fetched again).")
    lines += [
        f"Server version: {srv.SERVER_VERSION}",
        f"Corpus JSON: {json_path}",
        f"Pajek .net (cited → citing, SPC weights): {net_path}",
        f"Edge CSV: {csv_path}",
    ]
    recovered = sum(n.get('refs_recovered') or 0 for n in nodes.values())
    unparseable = {key: n['refs_unparseable'] for key, n in nodes.items() if n.get('refs_unparseable')}
    if recovered:
        lines.append(f"Recovered {recovered} reference(s) the REF view does not serve "
                     "(its last entry) from the FULL view.")
    if unparseable:
        lines.append(f"refs_unparseable: {sum(unparseable.values())} reference(s) Scopus counts "
                     f"but serves in neither view, in {len(unparseable)} paper(s): "
                     + ', '.join(f"{key} ({v})" for key, v in list(unparseable.items())[:10]))
    if check:
        unknown = sum(1 for n in nodes.values() if n.get('completeness') == 'unknown')
        sources = {}
        for n in nodes.values():
            if n.get('completeness_source'):
                sources[n['completeness_source']] = sources.get(n['completeness_source'], 0) + 1
        lines.append(f"Completeness ({RULE_TEXT}): {len(short)} short, "
                     f"{len(nodes) - len(short) - unknown} ok, {unknown} unknown. Comparison "
                     "counts from " + (', '.join(f"{s} {c}" for s, c in sources.items()) or 'none') + ".")
        for key in short[:25]:
            n = nodes[key]
            lines.append(f"  SHORT {key} {_label(nodes, key)}: {n.get('refs_retrieved')} "
                         f"references retrieved, {n['completeness_source']} lists "
                         f"{n.get('external_refs')}. {(n.get('title') or '')[:80]}")
        path = mp.get('global_main_path') or []
        if path:
            states = [nodes[x].get('completeness') for x in path if x in nodes]
            lines.append(f"Main path: {len(path)} papers, {states.count('ok')} ok, "
                         f"{states.count('short')} short, {states.count('unknown')} unknown.")
    if duplicates:
        lines.append(f"Possible duplicate records ({len(duplicates)}): " + '; '.join(
            ' = '.join(f"{x} {_label(nodes, x)}" for x in group) for group in duplicates[:10]))
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
        n_keys = sum(len(r['key_edges']) for r in routes['routes'])
        lines.append(f"Key routes: top {n_keys} SPC edges extend into {len(routes['routes'])} "
                     f"distinct routes ({len(routes['nodes'])} papers, {len(routes['edges'])} edges).")
        for r in routes['routes']:
            lines.append(f"  [{r['spc_weight']}] " + ' → '.join(_label(nodes, x) for x in r['path']))
    lines.append("Node key: " + '; '.join(
        f"{key} = {_label(nodes, key)}" for key in (mp['global_main_path'] or [])))

    edge_lines = "Edges (citing cited spc):\n" + '\n'.join(
        f"{e['citing']} {e['cited']} {e['spc']}" for e in edges)
    if inline == 'full':
        size = max(1, int(arguments.get("page_size") or DEFAULT_PAGE_SIZE))
        pages = max(1, math.ceil(len(nodes) / size))
        page = min(max(1, int(arguments.get("page") or 1)), pages)
        chunk = corpus['nodes'][(page - 1) * size: page * size]
        body = {'nodes': chunk}
        if page == 1:
            body.update({k2: v for k2, v in corpus.items() if k2 != 'nodes'})
        lines.append(f"Corpus JSON, page {page} of {pages} (nodes {(page - 1) * size + 1}-"
                     f"{(page - 1) * size + len(chunk)} of {len(nodes)}; edges and paths on "
                     f"page 1)" + (f"; call again with page={page + 1} for more" if page < pages else '')
                     + ":\n" + json.dumps(body, ensure_ascii=False, separators=(',', ':')))
    elif inline == 'nodes':
        lines.append("Nodes (id | author year | venue | refs retrieved/reported | comparison "
                     "count | completeness | error):\n" + '\n'.join(
                         _node_line(key, n, nodes, errors) for key, n in nodes.items()))
        lines.append(edge_lines if len(edges) <= INLINE_EDGE_LIMIT else
                     f"{len(edges)} edges: too many to list; see the CSV.")
    elif inline == 'edges':
        lines.append(edge_lines if len(edges) <= INLINE_EDGE_LIMIT else
                     f"{len(edges)} edges: too many to list; see the CSV or use inline='full'.")
    return [types.TextContent(type="text", text='\n'.join(lines))]


# ── resolve_citers ───────────────────────────────────────────────────────


async def _cross_check(srv, seeds, issns, rows, cap):
    """In-scope citers of the seeds per OpenAlex and Semantic Scholar that
    the Scopus analysis misses or cannot confirm. Never merged into it."""
    scope = set(issns or [])
    external = {}  # doi or title key -> record

    def add(key, rec, index):
        entry = external.setdefault(key, dict(rec, found_in=[]))
        if index not in entry['found_in']:
            entry['found_in'].append(index)

    for sid, seed in seeds.items():
        ident = {'doi': seed.get('doi'), 'title': seed.get('title'), 'year': seed.get('year')}
        if seed.get('doi'):
            try:
                work = await _resolve_openalex_work(seed['doi'])
                kwargs = {'extra_filter': openalex_issn_filter(issns)} if issns else {}
                works, _ = await srv.openalex.citing(short_id(work['id']), max_results=cap,
                                                     sort='citedby', **kwargs)
                for w in works:
                    rec = clean_openalex_work(w)
                    key = (rec['doi'] or '').lower() or normalize_title(rec['title'])
                    add(key, {'doi': (rec['doi'] or '').lower() or None, 'title': rec['title'],
                              'year': rec['year'], 'creator': rec['creator'],
                              'venue': rec['publication_name']}, 'openalex')
            except Exception as exc:
                logger.info(f"OpenAlex cross-check failed for {sid}: {exc}")
        try:
            _, citers = await citing_papers(ident)
            for c in citers:
                if scope and not scope & {normalize_issn(i) for i in venue_issns(c) if _issn_ok(i)}:
                    continue
                doi = ((c.get('externalIds') or {}).get('DOI') or '').lower() or None
                key = doi or normalize_title(c.get('title'))
                if key:
                    add(key, {'doi': doi, 'title': c.get('title'), 'year': str(c.get('year') or ''),
                              'creator': None,
                              'venue': (c.get('publicationVenue') or {}).get('name')},
                        'semantic_scholar')
        except Exception as exc:
            logger.info(f"Semantic Scholar cross-check failed for {sid}: {exc}")

    by_doi = {r['doi'].lower(): r for r in rows if r.get('doi')}
    by_title = {normalize_title(r['title']): r for r in rows if r.get('title')}
    seed_keys = {v['doi'] for v in seeds.values() if v.get('doi')} | \
        {normalize_title(v.get('title')) for v in seeds.values() if v.get('title')}
    candidates = []
    for key, ext in external.items():
        if not (ext.get('title') or '').strip():
            continue  # issue-level or empty records
        if (ext['doi'] or '') in seed_keys or normalize_title(ext['title']) in seed_keys:
            continue  # a seed citing the other seed
        row = by_doi.get(ext['doi'] or '') or by_title.get(normalize_title(ext['title']))
        if row is not None and row.get('status') == 'confirmed':
            continue
        ext['scopus_id'] = row['id'] if row else None
        ext['scopus_status'] = row['status'] if row else None
        candidates.append(ext)

    client = srv.client
    lookup = [c['doi'] for c in candidates if c['doi'] and not c['scopus_id']]
    found = await _scopus_records_for(client, 'DOI', lookup) if lookup else {}
    no_doi = [c for c in candidates if not c['doi'] and not c['scopus_id'] and c.get('title')]
    by_title_found = await _scopus_records_by_title(client, no_doi, issns) if no_doi else {}
    for c in candidates:
        rec = found.get(c['doi']) if c['doi'] else by_title_found.get(normalize_title(c['title']))
        if not c['scopus_id'] and rec:
            c['scopus_id'] = rec['scopus_id']
            c['scopus_status'] = 'not_returned_by_scopus_search'
        elif not c['scopus_id'] and not c['doi'] and c['found_in'] == ['openalex']:
            # OpenAlex files many AIS conference papers under the journal.
            c['scopus_status'] = 'openalex_only_unverifiable'
        elif not c['scopus_id']:
            c['scopus_status'] = 'not_in_scopus'
    in_scopus = [c for c in candidates if c['scopus_id']]
    raw, errors = await _fetch_references(client, [c['scopus_id'] for c in in_scopus], 'cross-check')
    for c in in_scopus:
        try:
            refs = clean_references(raw[c['scopus_id']]) if c['scopus_id'] in raw else None
            c['scopus_refs_retrieved'] = len(refs) if refs is not None else None
            c['seeds_in_scopus_refs'] = [s for s in seeds if refs and any(
                r.get('scopus_id') == s or (seeds[s].get('doi') and r.get('doi') == seeds[s]['doi'])
                for r in refs)]
        except Exception as exc:
            c['error'] = _err(exc)
    counts = await external_reference_counts((c['doi'] for c in candidates if c['doi']),
                                             openalex=srv.openalex)
    for c in candidates:
        c['external_refs'], c['external_refs_source'] = counts.get(c['doi'] or '', (None, None))
        if c.get('scopus_refs_retrieved') is not None:
            c['completeness'] = assess(c['scopus_refs_retrieved'], c['external_refs'])
            if c.get('seeds_in_scopus_refs'):
                c['scopus_status'] = 'cites_seed_in_scopus_but_missed_by_search'
            elif c['scopus_status'] == 'unconfirmed':
                c['scopus_status'] = 'found_but_seed_not_in_scopus_refs'
    candidates.sort(key=lambda c: (str(c.get('year') or ''), c.get('title') or ''))
    return candidates


async def _scopus_records_by_title(client, candidates, issns=None, batch=10):
    """{normalized title: record} for papers without a DOI, by exact title
    and year within the scope, ten per search. The scope matters: a JAIS
    article's title and year also match its ICIS conference version."""
    found = {}
    for i in range(0, len(candidates), batch):
        chunk = candidates[i:i + batch]
        clauses = []
        for c in chunk:
            title = ' '.join(re.sub(r'["{}()\[\]]', ' ', c['title']).split())
            year = str(c.get('year') or '')[:4]
            clauses.append(f'(TITLE("{title}")' + (f' AND PUBYEAR IS {year})' if year.isdigit() else ')'))
        try:
            raw = await client.search_all(scope_scopus_query(' OR '.join(clauses), issns),
                                          max_results=batch * 2, sort='coverDate')
        except Exception as exc:
            logger.info(f"Scopus title lookup failed: {exc}")
            continue
        for rec in clean_search_results(raw):
            found.setdefault(normalize_title(rec.get('title')), rec)
    return found


def _compact_hit(r, codes):
    out = {'id': r['id'], 'doi': r.get('doi'), 'year': r.get('year'), 'au': r.get('creator'),
           'title': (r.get('title') or '')[:60], 'venue': venue_abbr(r.get('venue')),
           'status': r['status'], 'by': [codes[s] for s in r['found_by']]}
    if r.get('cites'):
        out['cites'] = r['cites']
    if r.get('error'):
        out['error'] = r['error'][:120]
    return {k: v for k, v in out.items() if v not in (None, '')}


def _compact_missing(c):
    out = {'scopus_id': c.get('scopus_id'), 'doi': c.get('doi'), 'year': c.get('year'),
           'au': c.get('creator'), 'title': (c.get('title') or '')[:60],
           'venue': venue_abbr(c.get('venue')), 'status': c.get('scopus_status'),
           'in': c.get('found_in'), 'scopus_refs': c.get('scopus_refs_retrieved'),
           'ext_refs': c.get('external_refs')}
    return {k: v for k, v in out.items() if v not in (None, '', [])}


def _issn_ok(value) -> bool:
    try:
        normalize_issn(value)
        return True
    except ValueError:
        return False


async def _resolve_citers(arguments: dict) -> list:
    srv = server_module()
    source = _source(arguments)
    seeds_raw = arguments.get("seed_ids") or []
    if not seeds_raw:
        raise ValueError("seed_ids is required")
    queries = arguments.get("queries") or []
    issns = resolve_scope(arguments.get("scope"))
    verify = arguments.get("verify", True)
    cross_check = arguments.get("cross_check")
    if cross_check is None:
        cross_check = bool(issns)
    cap = int(arguments.get("max_results", 1000))
    inline = arguments.get("inline") or "compact"

    hits = {}      # key -> record
    found_by = {}  # key -> [strategy]
    seeds = {}     # seed key -> {'doi', 'label', 'title', 'year'}
    strategy_counts = {}
    cites, errors = {}, {}

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
            seeds[wid] = {'doi': rec['doi'], 'label': _make_node_label(rec, wid),
                          'title': rec['title'], 'year': rec['year']}
            works, _ = await oa.citing(wid, max_results=cap, sort='citedby', **kwargs)
            raw_by_key.update({short_id(w['id']): w for w in works})
            add(f"cites:{wid}", [clean_openalex_work(w) for w in works], 'openalex_id')
        for q in queries:
            works, _ = await oa.search(q, max_results=cap, sort='citedby', **kwargs)
            raw_by_key.update({short_id(w['id']): w for w in works})
            add(q, [clean_openalex_work(w) for w in works], 'openalex_id')
        if verify:
            for k in hits:
                try:
                    refs = {short_id(r) for r in (raw_by_key[k].get('referenced_works') or [])}
                    cites[k] = [s for s in seeds if s in refs]
                except Exception as exc:
                    errors[k] = _err(exc)
    else:
        client = srv.client
        for s in seeds_raw:
            sid = to_scopus_id(s)
            details = clean_abstract_details(await client.get_abstract(sid))
            year = (details.get('cover_date') or '')[:4]
            seeds[sid] = {'doi': (details.get('doi') or '').lower() or None,
                          'title': details.get('title'), 'year': year,
                          'label': _make_node_label(
                              {'creator': ((details.get('authors') or [{}])[0] or {}).get('surname'),
                               'year': year, 'title': details.get('title')}, sid)}
            jobs.progress(f"resolve_citers: searching REF({sid})")
            raw = await client.search_all(scope_scopus_query(f"REF({to_eid(sid)})", issns),
                                          max_results=cap, sort='citedby-count')
            add(f"REF({to_eid(sid)})", clean_search_results(raw), 'scopus_id')
        for q in queries:
            jobs.progress(f"resolve_citers: searching {q}")
            raw = await client.search_all(scope_scopus_query(q, issns),
                                          max_results=cap, sort='citedby-count')
            add(q, clean_search_results(raw), 'scopus_id')
        if verify:
            seed_dois = {v['doi']: k for k, v in seeds.items() if v['doi']}
            to_check = [h for h in hits if h not in seeds]
            raw_refs, errors = await _fetch_references(client, to_check, 'resolve_citers')
            for key, data in raw_refs.items():
                try:
                    found = []
                    for r in clean_references(data):
                        s = r.get('scopus_id') if r.get('scopus_id') in seeds else \
                            seed_dois.get(r.get('doi') or '')
                        if s and s not in found:
                            found.append(s)
                    cites[key] = found
                except Exception as exc:
                    errors[key] = _err(exc)

    for s in seeds:  # a seed citing another seed is not a hit
        hits.pop(s, None)
    rows = []
    for key, r in hits.items():
        row = {
            'id': key, 'doi': (r.get('doi') or '').lower() or None, 'year': _year(r),
            'creator': r.get('creator'), 'title': r.get('title'),
            'venue': r.get('publication_name'), 'found_by': found_by[key],
        }
        if verify:
            if key in errors:
                row['status'] = 'verification_failed'
                row['error'] = errors[key]
            else:
                row['cites'] = cites.get(key, [])
                row['status'] = 'confirmed' if row['cites'] else 'unconfirmed'
        else:
            row['status'] = 'unverified'
        rows.append(row)
    rows.sort(key=lambda r: (str(r['year'] or ''), r['id']))

    missing = []
    if cross_check and source == 'scopus':
        jobs.progress("resolve_citers: cross-checking OpenAlex and Semantic Scholar")
        missing = await _cross_check(srv, seeds, issns, rows, cap)

    by_status = {s: [r for r in rows if r['status'] == s]
                 for s in ('confirmed', 'unconfirmed', 'verification_failed')}
    table = []
    confirmed_ids = {r['id'] for r in by_status['confirmed']}
    for strategy, n in strategy_counts.items():
        mine = [r for r in rows if strategy in r['found_by']]
        table.append({
            'strategy': strategy, 'hits': n,
            'confirmed': sum(r['status'] == 'confirmed' for r in mine),
            'unconfirmed': sum(r['status'] == 'unconfirmed' for r in mine),
            'verification_failed': sum(r['status'] == 'verification_failed' for r in mine),
            'confirmed_missed': len(confirmed_ids - {r['id'] for r in mine}),
        })

    base = _base_name(f"citers-{'-'.join(seeds)}")
    out = _output_dir()
    json_path = out / f'{base}.json'
    json_path.write_text(json.dumps({'seeds': seeds, 'strategies': table, 'hits': rows,
                                     'missing_from_scopus': missing},
                                    ensure_ascii=False, indent=2), encoding='utf-8')

    seed_text = ', '.join(f"{k} ({v['label']})" for k, v in seeds.items())
    lines = [f"Citers of {seed_text} ({source}): {len(rows)} distinct hits across "
             f"{len(strategy_counts)} strategies."]
    if by_status['verification_failed']:
        lines.append(f"Verification failed for {len(by_status['verification_failed'])} hit(s), "
                     "counted as neither confirmed nor unconfirmed: " + '; '.join(
                         f"{r['id']} ({r['error'][:80]})" for r in by_status['verification_failed'][:10]))
    lines += [f"Server version: {srv.SERVER_VERSION}", f"Results JSON: {json_path}"]
    if issns:
        lines.append(f"Scope: {len(issns)} ISSNs.")
    if verify:
        lines.append("Strategy | hits | confirmed | unconfirmed | verification failed | "
                     "confirmed citers missed")
        for t in table:
            lines.append(f"  {t['strategy']} | {t['hits']} | {t['confirmed']} | "
                         f"{t['unconfirmed']} | {t['verification_failed']} | {t['confirmed_missed']}")
        lines.append(f"Confirmed (a seed is in the hit's own reference list): "
                     f"{len(by_status['confirmed'])}. Unconfirmed: {len(by_status['unconfirmed'])} "
                     "(cite no seed: false positives, citers of related work, or reference "
                     "lists missing the seed). Verification failed: "
                     f"{len(by_status['verification_failed'])}.")
    else:
        for t in table:
            lines.append(f"  {t['strategy']}: {t['hits']} hits (not verified)")
    if cross_check and source == 'scopus':
        lines.extend(_cross_check_lines(missing))
    if inline == 'compact':
        codes = {t['strategy']: f"S{i + 1}" for i, t in enumerate(table)}
        lines.append("Hits, one JSON object per line (found_by: " + ', '.join(
            f"{c} = {st}" for st, c in codes.items()) + "; full records in the JSON file):")
        lines.extend(json.dumps(_compact_hit(r, codes), ensure_ascii=False) for r in rows)
        listed = [c for c in missing if c.get('scopus_status') != 'openalex_only_unverifiable']
        if listed:
            lines.append("missing_from_scopus, one JSON object per line (OpenAlex-only "
                         "entries without DOI are in the JSON file only):")
            lines.extend(json.dumps(_compact_missing(c), ensure_ascii=False) for c in listed)
    return [types.TextContent(type="text", text='\n'.join(lines))]


CROSS_CHECK_GROUPS = [
    ('cites_seed_in_scopus_but_missed_by_search',
     'In Scopus and citing a seed there, but not returned by the searches'),
    ('not_returned_by_scopus_search',
     'In Scopus, not returned by the searches, seed absent from its Scopus reference list'),
    ('found_but_seed_not_in_scopus_refs',
     'Found by the searches, seed absent from its Scopus reference list'),
    ('verification_failed', 'Found by the searches, verification failed'),
    ('not_in_scopus', 'Not in Scopus (coverage)'),
    ('openalex_only_unverifiable',
     'Only in OpenAlex, no DOI, not matched in Scopus by title (often conference papers '
     'OpenAlex files under the journal)'),
]


def _cross_check_lines(missing):
    if not missing:
        return ["Cross-check: OpenAlex and Semantic Scholar list no in-scope citers "
                "beyond the confirmed Scopus set."]

    def label(c):
        who = c.get('creator') or (c.get('title') or '')[:40]
        return f"{who} {str(c.get('year') or '')[:4]} {venue_abbr(c.get('venue'))}".strip()

    def refs(c):
        if c.get('scopus_refs_retrieved') is None:
            return ''
        ext = c.get('external_refs')
        held = f"Scopus holds {c['scopus_refs_retrieved']}" + (f" of ~{ext}" if ext else '') + " refs"
        return f" ({held}{', SHORT' if c.get('completeness') == 'short' else ''})"

    lines = [f"Other indexes (OpenAlex, Semantic Scholar) list {len(missing)} in-scope "
             "citer(s) of the seeds that Scopus misses or cannot confirm; they are NOT added "
             "to the Scopus result:"]
    for status, title in CROSS_CHECK_GROUPS:
        group = [c for c in missing if c['scopus_status'] == status]
        if not group:
            continue
        shown = group if status not in ('not_in_scopus', 'openalex_only_unverifiable') else group[:6]
        more = f" ... and {len(group) - len(shown)} more" if len(group) > len(shown) else ''
        lines.append(f"  {title} ({len(group)}): "
                     + '; '.join(label(c) + refs(c) for c in shown) + more)
    return lines


# ── citation_context ─────────────────────────────────────────────────────


async def _identity(identifier: str) -> dict:
    """What Semantic Scholar resolution needs: DOI, title, year, MAG ID."""
    srv = server_module()
    ident = str(identifier).strip()
    low = ident.lower()
    for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
        if low.startswith(prefix):
            low = low[len(prefix):]
    out = {'key': ident, 'doi': None, 'title': None, 'year': None, 'mag': None}
    if low.startswith('10.') and '/' in low:
        out['doi'] = low
        work = None
        try:
            work = await srv.openalex.get_work(low)
        except Exception as exc:
            logger.info(f"OpenAlex lookup for {low} failed: {exc}")
    elif openalex_work_key(ident):
        work = await srv.openalex.get_work(ident)
        if not work:
            raise ValueError(f"OpenAlex has no work {ident!r}")
    else:
        details = clean_abstract_details(await srv.client.get_abstract(to_scopus_id(ident)))
        out.update(doi=(details.get('doi') or '').lower() or None, title=details.get('title'),
                   year=(details.get('cover_date') or '')[:4] or None,
                   surnames=[a.get('surname') for a in details.get('authors') or [] if a.get('surname')])
        return out
    if work:
        out['doi'] = out['doi'] or (bare_doi(work.get('doi')) or '').lower() or None
        out['title'] = work.get('title')
        out['year'] = work.get('publication_year')
        out['mag'] = (work.get('ids') or {}).get('mag')
        out['surnames'] = [(a.get('author') or {}).get('display_name', '').split()[-1]
                           for a in work.get('authorships') or []
                           if (a.get('author') or {}).get('display_name')]
    return out


async def _citing_fulltext(doi: str):
    """(text, source) for a citing paper: ScienceDirect when entitled,
    else the open-access waterfall; (None, None) when neither has it."""
    srv = server_module()
    try:
        sd = await srv.client.get_sciencedirect_fulltext(doi)
        text = ((sd or {}).get('full-text-retrieval-response') or {}).get('originalText') or ''
        if len(text.strip()) >= FULLTEXT_MIN_CHARS:
            return text, 'fulltext_sciencedirect'
    except Exception as exc:
        logger.info(f"ScienceDirect full text failed for {doi}: {exc}")
    try:
        oa = await srv.fetch_oa_fulltext(doi)
        if oa.get('text'):
            return oa['text'], 'fulltext_oa'
    except Exception as exc:
        logger.info(f"Open-access full text failed for {doi}: {exc}")
    return None, None


async def gather_contexts(pairs_in, terms=(), max_contexts=DEFAULT_MAX_CONTEXTS, fulltext=True):
    """Contexts for [{'citing', 'cited'}]: Semantic Scholar first, then the
    citing paper's full text where S2 has no usable sentences. Each result
    carries 'context_source': semantic_scholar, fulltext_oa,
    fulltext_sciencedirect or none."""
    resolved, problems, cache = [], [], {}
    for p in pairs_in:
        try:
            for side in ('citing', 'cited'):
                if p[side] not in cache:
                    cache[p[side]] = await _identity(p[side])
            resolved.append({'citing': p['citing'], 'cited': p['cited'],
                             'citing_ident': cache[p['citing']], 'cited_ident': cache[p['cited']]})
        except Exception as exc:
            problems.append({'citing': p.get('citing'), 'cited': p.get('cited'),
                             'context': {'status': 'error', 'detail': _err(exc),
                                         'context_source': 'none'}})
    results = await citation_contexts(resolved, terms, max_contexts)
    texts = {}
    for r in results:
        c = r['context']
        if c['status'] == 'found':
            c['context_source'] = 'semantic_scholar'
            continue
        c['context_source'] = 'none'
        doi = r['citing_ident'].get('doi')
        if not fulltext or not doi:
            continue
        if doi not in texts:
            jobs.progress(f"full text of {doi}")
            texts[doi] = await _citing_fulltext(doi)
        text, source = texts[doi]
        if not text:
            c['fulltext_note'] = 'no full text available (not entitled, no open copy)'
            continue
        cited = dict(r['cited_ident'])
        cited['surnames'] = cited.get('surnames') or c.get('cited_surnames') or []
        found = contexts_from_text(text, cited)
        kept, total, _ = clean_contexts(found['contexts'], cited['surnames'], cited.get('year'),
                                        terms, max_contexts)
        if kept:
            c.update({'s2_status': c['status'], 'status': 'found', 'context_source': source,
                      'contexts': kept, 'n_contexts': len(kept), 'n_contexts_total': total,
                      'reference_entry': (found['reference_entry'] or '')[:300]})
            c.setdefault('intents', [])
            c.setdefault('is_influential', False)
            c.pop('note', None)
        else:
            c['fulltext_note'] = f"full text checked ({source}): {found['reason'] or 'no citing sentence'}"
    return results + problems


async def _citation_context(arguments: dict) -> list:
    pairs_in = list(arguments.get("pairs") or [])
    if arguments.get("citing") and arguments.get("cited"):
        pairs_in = [{'citing': arguments['citing'], 'cited': arguments['cited']}] + pairs_in
    if not pairs_in:
        raise ValueError("Give citing and cited, or pairs.")
    if len(pairs_in) > MAX_CONTEXT_PAIRS:
        raise ValueError(f"At most {MAX_CONTEXT_PAIRS} pairs per call.")
    results = await gather_contexts(pairs_in, arguments.get("construct_terms") or [],
                                    int(arguments.get("max_contexts", DEFAULT_MAX_CONTEXTS)),
                                    arguments.get("fulltext_fallback", True))
    lines = [f"Citation contexts for {len(results)} pair(s) (Semantic Scholar, then the "
             "citing paper's full text)."]
    for r in results:
        c = r['context']
        head = f"{r['citing']} → {r['cited']}: {c['status']}"
        if c['status'] in ('found', 'contexts_withheld'):
            head += (f"; source: {c.get('context_source')}; "
                     f"intents: {', '.join(c.get('intents') or []) or 'none given'}; "
                     f"influential: {str(c.get('is_influential', False)).lower()}; "
                     f"{c.get('n_contexts', 0)} of {c.get('n_contexts_total', 0)} context(s) shown")
            if c.get('s2_status'):
                head += f" (Semantic Scholar: {c['s2_status']})"
        elif c.get('detail'):
            head += f" ({c['detail']})"
        if c.get('citing_route') or c.get('cited_route'):
            head += (f" [resolved: citing by {c.get('citing_route') or '-'}, "
                     f"cited by {c.get('cited_route') or '-'}]")
        lines.append(head)
        for ctx in c.get('contexts') or []:
            lines.append(f"    “{ctx}”")
        for key in ('note', 'fulltext_note'):
            if c.get(key):
                lines.append(f"    {c[key]}")
    return [types.TextContent(type="text", text='\n'.join(lines))]


HANDLERS = {
    'citation_network': _citation_network,
    'resolve_citers': _resolve_citers,
    'citation_context': _citation_context,
}
