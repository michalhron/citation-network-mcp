"""Transmission audit of a main path, and index coverage of a citer set.

path_transmission gathers the evidence for each consecutive edge of a main
path (does the later paper carry the earlier one's construct forward?) and
proposes a draft label for a human coder. index_coverage makes the index
dependence of a citer set a reported property of a study.
"""
import csv
import json
import logging
from pathlib import Path

import mcp.types as types

from .. import jobs
from ..baskets import (
    SCOPE_SCHEMA,
    normalize_issn,
    openalex_issn_filter,
    resolve_scope,
    scope_scopus_query,
)
from ..fulltext_contexts import construct_near_marker, is_list_citation
from ..openalex import clean_openalex_work, normalize_title, short_id
from ..records import clean_abstract_details, clean_search_results, to_eid, to_scopus_id
from ..semantic_scholar import citing_papers, venue_issns
from .common import _resolve_openalex_work, server_module
from ..output import _output_dir
from .corpus import _base_name, _issn_ok, gather_contexts, venue_abbr

logger = logging.getLogger("scopus-plus-mcp")

MAX_PATH = 40
ENGAGED_INTENTS = {'methodology', 'result'}
DISCLAIMER = (
    "Draft labels are heuristics for a human coder to confirm or overturn, not "
    "findings: substantive = the citing paper engages the cited work (influential, "
    "method/result intent, or two or more non-list contexts) and a context names a "
    "construct term in the cited work's own clause; construct-shifted = engages it without naming the construct; "
    "hollow = only background or list citations; unresolved = no context sentences "
    "from any source."
)

TOOLS = [
    types.Tool(
        name="path_transmission",
        description=(
            "Transmission audit of a main path: for each consecutive edge (later paper "
            "citing the earlier one) it gathers the citing sentences (Semantic Scholar, "
            "then the citing paper's full text), Semantic Scholar's intent and "
            "influential flags, how many contexts name the construct terms, and whether "
            "the citation sits in a list of three or more works. Proposes a draft label "
            "per edge (substantive, construct-shifted, hollow, unresolved) with its "
            "evidence, and writes a CSV coding sheet with blank columns for two "
            "independent coders. " + DISCLAIMER
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "The main path, oldest first (as citation_network reports it): "
                        "Scopus IDs, DOIs or OpenAlex IDs."
                    ),
                },
                "construct_terms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The construct and its variants, e.g. ['organizing vision'].",
                },
                "corpus_json": {
                    "type": "string",
                    "description": "Optional citation_network corpus file, to add each edge's SPC weight.",
                },
                "max_contexts": {"type": "integer", "default": 5,
                                 "description": "Most contexts kept per edge (default 5)."},
            },
            "required": ["path_ids", "construct_terms"],
        },
    ),
    types.Tool(
        name="index_coverage",
        description=(
            "Which papers cite the seeds according to Scopus, OpenAlex and Semantic "
            "Scholar, under the same journal scope, and how the three sets overlap. "
            "Papers are matched by DOI, else by title and year. Makes index coverage a "
            "reported property of a study rather than a hidden one. Scopus side: REF() "
            "search (not verified; use resolve_citers for that)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "seed_ids": {"type": "array", "items": {"type": "string"},
                             "description": "Seed papers (Scopus IDs or EIDs)."},
                "scope": SCOPE_SCHEMA,
                "max_results": {"type": "integer", "default": 1000,
                                "description": "Cap per index and seed (default 1000)."},
            },
            "required": ["seed_ids"],
        },
    ),
]


# ── path_transmission ───────────────────────────────────────────────────


def _label(ident):
    if not ident:
        return '?'
    who = (ident.get('surnames') or [None])[0] or (ident.get('title') or '?')[:30]
    return f"{who} {str(ident.get('year') or '')[:4]}".strip()


def draft_label(c: dict, terms, cited_ident) -> dict:
    """The heuristic label and its evidence for one edge's context dict."""
    contexts = c.get('contexts') or []
    intents = set(c.get('intents') or [])
    influential = bool(c.get('is_influential'))
    names = (cited_ident or {}).get('surnames') or c.get('cited_surnames') or []
    year = (cited_ident or {}).get('year')
    low_terms = [t.lower() for t in terms if t]
    construct = [ctx for ctx in contexts if construct_near_marker(ctx, names, year, low_terms)]
    lists = [ctx for ctx in contexts if is_list_citation(ctx, names, year)]
    non_list = len(contexts) - len(lists)
    engaged = influential or bool(intents & ENGAGED_INTENTS) or non_list >= 2
    if not contexts:
        label = 'unresolved'
    elif engaged and construct:
        label = 'substantive'
    elif engaged:
        label = 'construct-shifted'
    else:
        label = 'hollow'
    evidence = []
    if influential:
        evidence.append('S2 influential')
    if intents:
        evidence.append('intents ' + ', '.join(sorted(intents)))
    evidence.append(f"{len(construct)}/{len(contexts)} contexts name the construct for this work")
    if lists:
        evidence.append(f"{len(lists)} list citation(s)")
    if not contexts:
        evidence.append(f"no sentences ({c.get('status')}"
                        + (f"; {c['fulltext_note']}" if c.get('fulltext_note') else '') + ")")
    return {'label': label, 'evidence': '; '.join(evidence), 'construct_mentions': len(construct),
            'list_citations': len(lists)}


def _spc_lookup(path):
    if not path:
        return {}
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding='utf-8'))
        return {(e['citing'], e['cited']): e.get('spc') for e in data.get('edges') or []}
    except Exception as exc:
        logger.info(f"corpus_json unreadable: {exc}")
        return {}


async def _path_transmission(arguments: dict) -> list:
    srv = server_module()
    path = [str(x) for x in arguments.get("path_ids") or []]
    terms = arguments.get("construct_terms") or []
    if len(path) < 2:
        raise ValueError("path_ids needs at least two papers, oldest first.")
    if len(path) > MAX_PATH:
        raise ValueError(f"At most {MAX_PATH} papers per path.")
    if not terms:
        raise ValueError("construct_terms is required, e.g. ['organizing vision'].")
    spc = _spc_lookup(arguments.get("corpus_json"))
    pairs = [{'citing': b, 'cited': a} for a, b in zip(path, path[1:])]
    jobs.progress(f"path_transmission: contexts for {len(pairs)} edges")
    results = await gather_contexts(pairs, terms, int(arguments.get("max_contexts", 5)))
    order = {(p['citing'], p['cited']): i for i, p in enumerate(pairs)}
    results.sort(key=lambda r: order.get((r['citing'], r['cited']), 0))

    rows = []
    for i, r in enumerate(results, start=1):
        c = r['context']
        cited, citing = r.get('cited_ident'), r.get('citing_ident')
        d = draft_label(c, terms, cited)
        rows.append({
            'edge': i, 'citing_id': r['citing'], 'citing_label': _label(citing),
            'cited_id': r['cited'], 'cited_label': _label(cited),
            'spc': spc.get((to_scopus_id(r['citing']), to_scopus_id(r['cited']))),
            'context_source': c.get('context_source'), 's2_status': c.get('s2_status') or c.get('status'),
            's2_intents': ', '.join(c.get('intents') or []),
            'influential': bool(c.get('is_influential')),
            'n_contexts': len(c.get('contexts') or []),
            'construct_mentions': d['construct_mentions'], 'list_citations': d['list_citations'],
            'draft_label': d['label'], 'evidence': d['evidence'],
            'contexts': ' || '.join(c.get('contexts') or []),
            'coder_1_label': '', 'coder_2_label': '', 'coder_notes': '',
        })

    base = _base_name(f"transmission-{rows[0]['cited_label']}-{rows[-1]['citing_label']}")
    out = _output_dir()
    csv_path = out / f'{base}-coding.csv'
    with csv_path.open('w', newline='', encoding='utf-8-sig') as f:  # BOM: Excel reads UTF-8
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    json_path = out / f'{base}.json'
    json_path.write_text(json.dumps({'path': path, 'construct_terms': terms, 'edges': rows,
                                     'disclaimer': DISCLAIMER}, ensure_ascii=False, indent=2),
                         encoding='utf-8')

    counts = {}
    for row in rows:
        counts[row['draft_label']] = counts.get(row['draft_label'], 0) + 1
    lines = [
        f"Transmission audit of a {len(path)}-paper path for "
        f"{', '.join(repr(t) for t in terms)}: " + ', '.join(
            f"{counts.get(k, 0)} {k}" for k in ('substantive', 'construct-shifted', 'hollow', 'unresolved')) + '.',
        DISCLAIMER,
        f"Server version: {srv.SERVER_VERSION}",
        f"Coding sheet (CSV, two blank coder columns): {csv_path}",
        f"JSON: {json_path}",
    ]
    for row, r in zip(rows, results):
        spc_text = f" [SPC {row['spc']}]" if row['spc'] is not None else ''
        lines.append(f"{row['edge']}. {row['citing_label']} → {row['cited_label']}{spc_text}: "
                     f"{row['draft_label'].upper()} ({row['evidence']}; source: {row['context_source']})")
        ctxs = r['context'].get('contexts') or []
        if ctxs:
            quote = ctxs[0] if len(ctxs[0]) <= 220 else ctxs[0][:220] + '…'
            lines.append(f"    “{quote}”")
    return [types.TextContent(type="text", text='\n'.join(lines))]


# ── index_coverage ──────────────────────────────────────────────────────


def _key(doi, title, year):
    if doi:
        return 'doi:' + doi.lower()
    t = normalize_title(title)
    return f"title:{t}:{str(year or '')[:4]}" if t else None


async def _index_coverage(arguments: dict) -> list:
    srv = server_module()
    client, oa = srv.client, srv.openalex
    seeds_raw = arguments.get("seed_ids") or []
    if not seeds_raw:
        raise ValueError("seed_ids is required")
    issns = resolve_scope(arguments.get("scope"))
    cap = int(arguments.get("max_results", 1000))
    scope = set(issns or [])
    sets = {'scopus': {}, 'openalex': {}, 'semantic_scholar': {}}
    seed_keys, notes = set(), []

    for s in seeds_raw:
        sid = to_scopus_id(s)
        details = clean_abstract_details(await client.get_abstract(sid))
        doi = (details.get('doi') or '').lower() or None
        year = (details.get('cover_date') or '')[:4]
        seed_keys.add(_key(doi, details.get('title'), year))
        jobs.progress(f"index_coverage: Scopus REF({sid})")
        raw = await client.search_all(scope_scopus_query(f"REF({to_eid(sid)})", issns),
                                      max_results=cap, sort='citedby-count')
        for r in clean_search_results(raw):
            k = _key(r.get('doi'), r.get('title'), (r.get('cover_date') or '')[:4])
            if k:
                sets['scopus'].setdefault(k, {'title': r.get('title'), 'year': (r.get('cover_date') or '')[:4],
                                              'venue': r.get('publication_name'), 'scopus_id': r.get('scopus_id')})
        if doi:
            try:
                jobs.progress(f"index_coverage: OpenAlex citers of {sid}")
                work = await _resolve_openalex_work(doi)
                kwargs = {'extra_filter': openalex_issn_filter(issns)} if issns else {}
                works, _ = await oa.citing(short_id(work['id']), max_results=cap, sort='citedby', **kwargs)
                for w in works:
                    rec = clean_openalex_work(w)
                    k = _key(rec['doi'], rec['title'], rec['year'])
                    if k and (rec['title'] or '').strip():
                        sets['openalex'].setdefault(k, {'title': rec['title'], 'year': rec['year'],
                                                        'venue': rec['publication_name'],
                                                        'openalex_id': rec['openalex_id']})
            except Exception as exc:
                notes.append(f"OpenAlex failed for {sid}: {exc}")
        try:
            jobs.progress(f"index_coverage: Semantic Scholar citers of {sid}")
            _, citers = await citing_papers({'doi': doi, 'title': details.get('title'), 'year': year})
            for c in citers:
                if scope and not scope & {normalize_issn(i) for i in venue_issns(c) if _issn_ok(i)}:
                    continue
                k = _key((c.get('externalIds') or {}).get('DOI'), c.get('title'), c.get('year'))
                if k:
                    sets['semantic_scholar'].setdefault(k, {
                        'title': c.get('title'), 'year': str(c.get('year') or ''),
                        'venue': (c.get('publicationVenue') or {}).get('name')})
        except Exception as exc:
            notes.append(f"Semantic Scholar failed for {sid}: {exc}")

    for name in sets:
        for k in seed_keys:
            sets[name].pop(k, None)
    # Title keys let a no-DOI record in one index meet a DOI record in another.
    title_of = {}
    for name, found in sets.items():
        for k, rec in found.items():
            title_of.setdefault(_key(None, rec['title'], rec['year']), set()).add(k)
    canonical = {}
    for keys in title_of.values():
        doi_keys = sorted(k for k in keys if k.startswith('doi:'))
        for k in keys:
            canonical[k] = doi_keys[0] if doi_keys else k
    membership, records = {}, {}
    for name, found in sets.items():
        for k, rec in found.items():
            ck = canonical.get(k, k)
            membership.setdefault(ck, set()).add(name)
            records.setdefault(ck, rec)
    regions = {}
    for ck, names in membership.items():
        regions.setdefault(' & '.join(sorted(names)), []).append(ck)
    order = ['openalex & scopus & semantic_scholar', 'openalex & scopus', 'scopus & semantic_scholar',
             'openalex & semantic_scholar', 'scopus', 'openalex', 'semantic_scholar']

    base = _base_name(f"coverage-{'-'.join(to_scopus_id(s) for s in seeds_raw)}")
    json_path = _output_dir() / f'{base}.json'
    json_path.write_text(json.dumps({
        'seeds': seeds_raw, 'scope': issns, 'totals': {n: len(v) for n, v in sets.items()},
        'regions': {r: [dict(records[k], key=k) for k in ks] for r, ks in regions.items()},
        'notes': notes}, ensure_ascii=False, indent=2), encoding='utf-8')

    lines = [f"Citers of {', '.join(seeds_raw)}" + (f" in {len(issns)} scoped ISSNs" if issns else '')
             + ": Scopus {0}, OpenAlex {1}, Semantic Scholar {2} ({3} distinct papers).".format(
                 len(sets['scopus']), len(sets['openalex']), len(sets['semantic_scholar']), len(membership)),
             f"Server version: {srv.SERVER_VERSION}", f"JSON: {json_path}",
             "Overlap (matched by DOI, else title and year):"]
    for region in order:
        ks = regions.get(region, [])
        line = f"  {region.replace('semantic_scholar', 'S2')}: {len(ks)}"
        if ks and region.count('&') < 2 and len(ks) <= 12:
            line += ' — ' + '; '.join(
                f"{str(records[k].get('year') or '')[:4]} {venue_abbr(records[k].get('venue'))} "
                f"{(records[k].get('title') or '')[:50]}" for k in ks)
        lines.append(line)
    lines.append("Scopus side is the unverified REF() search; resolve_citers verifies it. "
                 "OpenAlex files some AIS conference papers under the journal; Semantic "
                 "Scholar is scoped by its venue ISSNs, which some records lack.")
    lines.extend(notes)
    return [types.TextContent(type="text", text='\n'.join(lines))]


HANDLERS = {
    'path_transmission': _path_transmission,
    'index_coverage': _index_coverage,
}
