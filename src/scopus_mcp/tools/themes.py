"""Thematic evolution of a corpus."""
import asyncio
import csv
import json
import logging
from pathlib import Path

import mcp.types as types

from .. import jobs
from ..baskets import openalex_issn_filter, resolve_scope, scope_scopus_query
from ..importers import load_corpus_file
from ..openalex import clean_openalex_work, short_id
from ..output import _output_dir
from ..records import _reconstruct_abstract_from_openalex, clean_search_results, scalar, to_scopus_id
from ..themes import (
    evolution,
    fates,
    make_periods,
    normalize_term,
    period_themes,
    phrases,
    render_strategic_png,
    track_construct,
)
from .common import _resolve_openalex_work, _source, server_module
from .corpus import MAX_CORPUS, REF_CONCURRENCY, _base_name, _scopus_records_for
from .history import CORPUS_PROPS

logger = logging.getLogger("scopus-plus-mcp")

TERM_MODES = ('author_keywords', 'all_keywords', 'title_abstract')

TOOLS = [
    types.Tool(
        name="thematic_evolution",
        description=(
            "Themes of a corpus and how they change over time (Cobo et al. 2011; as in "
            "bibliometrix's thematic map and thematic evolution). Per period: keyword "
            "co-occurrence clusters, each placed in the strategic diagram by Callon "
            "centrality and density (motor, basic, niche, emerging or declining); between "
            "periods: which themes continue, split, merge, appear or vanish (inclusion "
            "index). With construct_terms it follows a construct through the periods: the "
            "theme that holds it, where that theme sits, and the keywords it keeps company "
            "with, i.e. whether the construct stays central, drifts or dissolves into "
            "another. Give ids or a query. Writes JSON, CSV and a PNG of the strategic "
            "diagrams. Cost: one abstract request per paper (cached)."
        ),
        inputSchema={"type": "object", "properties": {
            **CORPUS_PROPS,
            "cut_years": {"type": "array", "items": {"type": "integer"},
                          "description": ("First years of the later periods, e.g. [2005, 2012] "
                                          "gives up to 2004, 2005-2011 and 2012 on. Default: "
                                          "n_periods of similar size.")},
            "n_periods": {"type": "integer", "default": 3,
                          "description": "Periods of similar paper counts when cut_years is not given (default 3)."},
            "terms": {"type": "string", "enum": list(TERM_MODES), "default": "author_keywords",
                      "description": ("'author_keywords' (default; papers without them fall back to "
                                      "Scopus index terms), 'all_keywords' (author keywords plus "
                                      "index terms), 'title_abstract' (phrases from title and "
                                      "abstract; for corpora with few keywords). With "
                                      "source='openalex', keywords are OpenAlex's own.")},
            "construct_terms": {"type": "array", "items": {"type": "string"},
                                "description": "A construct to follow, e.g. ['organizing vision']."},
            "min_freq": {"type": "integer", "default": 2,
                         "description": "Keywords must appear in at least this many papers of a period (default 2)."},
        }},
    ),
]


def _scopus_terms(raw, mode):
    root = (raw or {}).get('abstracts-retrieval-response') or {}
    ak = ((root.get('authkeywords') or {}).get('author-keyword')) or []
    ak = [scalar(k) for k in (ak if isinstance(ak, list) else [ak])]
    ix = ((root.get('idxterms') or {}).get('mainterm')) or []
    ix = [scalar(k) for k in (ix if isinstance(ix, list) else [ix])]
    core = root.get('coredata') or {}
    if mode == 'title_abstract':
        return phrases(f"{scalar(core.get('dc:title')) or ''}. {scalar(core.get('dc:description')) or ''}"), 'text'
    if mode == 'all_keywords':
        terms, kind = ak + ix, 'author+index'
    else:
        terms, kind = (ak, 'author') if any(ak) else (ix, 'index')
    return sorted({normalize_term(t) for t in terms if t} - {''}), kind if any(terms) else 'none'


async def _collect_scopus_docs(client, arguments, mode):
    ids, query = arguments.get("ids"), arguments.get("query")
    issns = resolve_scope(arguments.get("scope"))
    if ids:
        wanted = [to_scopus_id(i) for i in ids]
        found = await _scopus_records_for(client, 'EID', wanted)
        recs = [found[x] for x in wanted if x in found]
    else:
        raw = await client.search_all(scope_scopus_query(query, issns),
                                      max_results=int(arguments.get("max_results", 300)),
                                      sort='citedby-count')
        recs = clean_search_results(raw)
    semaphore = asyncio.Semaphore(REF_CONCURRENCY)
    docs, done = [], 0

    async def one(rec):
        nonlocal done
        async with semaphore:
            try:
                raw = await client.get_abstract(rec['scopus_id'])
            except Exception as exc:
                logger.info(f"abstract failed for {rec['scopus_id']}: {exc}")
                raw = None
            done += 1
            jobs.progress(f"thematic_evolution: records {done}/{len(recs)}")
        terms, kind = _scopus_terms(raw, mode)
        year = str(rec.get('cover_date') or '')[:4]
        if year.isdigit():
            docs.append({'id': rec['scopus_id'], 'year': int(year), 'terms': terms, 'kind': kind})
    await asyncio.gather(*(one(r) for r in recs if r.get('scopus_id')))
    return docs


async def _collect_openalex_docs(openalex, arguments, mode):
    ids, query = arguments.get("ids"), arguments.get("query")
    issns = resolve_scope(arguments.get("scope"))
    if ids:
        works = [await _resolve_openalex_work(i) for i in ids]
    else:
        kwargs = {'extra_filter': openalex_issn_filter(issns)} if issns else {}
        works, _ = await openalex.search(query, max_results=int(arguments.get("max_results", 300)),
                                         sort='citedby', **kwargs)
    by_id = {short_id(w['id']): w for w in works}
    details = {short_id(w['id']): w for w in await openalex.works_by_ids(
        list(by_id), select='id,title,keywords,abstract_inverted_index')}
    docs = []
    for wid, w in by_id.items():
        rec = clean_openalex_work(w)
        d = details.get(wid) or {}
        if mode == 'title_abstract':
            text = f"{d.get('title') or ''}. {_reconstruct_abstract_from_openalex(d.get('abstract_inverted_index') or {}) or ''}"
            terms, kind = phrases(text), 'text'
        else:
            kws = [k.get('display_name') for k in d.get('keywords') or [] if k.get('display_name')]
            terms, kind = sorted({normalize_term(k) for k in kws} - {''}), ('openalex' if kws else 'none')
        if str(rec['year'] or '').isdigit():
            docs.append({'id': wid, 'year': int(rec['year']), 'terms': terms, 'kind': kind})
    return docs


def _docs_from_file(corpus: dict, mode: str):
    """Documents straight from an imported corpus: its own keywords and
    abstracts, no API calls."""
    docs = []
    for i, r in enumerate(corpus.get('records') or []):
        year = str(r.get('year') or '')
        if not year.isdigit():
            continue
        if mode == 'title_abstract':
            terms, kind = phrases(f"{r.get('title') or ''}. {r.get('abstract') or ''}"), 'text'
        else:
            ak, ix = r.get('author_keywords') or [], r.get('index_keywords') or []
            raw = ak + ix if mode == 'all_keywords' else (ak or ix)
            kind = ('author+index' if mode == 'all_keywords' else 'author' if ak else 'index') if raw else 'none'
            terms = sorted({normalize_term(t) for t in raw} - {''})
        docs.append({'id': r.get('scopus_id') or r.get('doi') or str(i), 'year': int(year),
                     'terms': terms, 'kind': kind})
    return docs


async def _thematic_evolution(arguments: dict) -> list:
    srv = server_module()
    from_file = arguments.get("corpus_file")
    ids, query = arguments.get("ids"), arguments.get("query")
    if from_file and (ids or query):
        raise ValueError("Give corpus_file, ids or query, not several.")
    if not from_file and bool(ids) == bool(query):
        raise ValueError("Give either ids or query.")
    if ids and len(ids) > MAX_CORPUS:
        raise ValueError(f"At most {MAX_CORPUS} ids per call.")
    mode = arguments.get("terms") or 'author_keywords'
    if mode not in TERM_MODES:
        raise ValueError(f"terms must be one of {', '.join(TERM_MODES)}")
    source = _source(arguments)
    if from_file:
        source = 'corpus file'
        docs = _docs_from_file(load_corpus_file(from_file), mode)
    elif source == 'openalex':
        docs = await _collect_openalex_docs(srv.openalex, arguments, mode)
    else:
        docs = await _collect_scopus_docs(srv.client, arguments, mode)
    if not docs:
        raise ValueError("No dated papers found for the given ids or query.")
    bounds = make_periods([d['year'] for d in docs], arguments.get("cut_years"),
                          int(arguments.get("n_periods") or 3))
    min_freq = int(arguments.get("min_freq") or 2)
    periods = []
    for lo, hi in bounds:
        jobs.progress(f"thematic_evolution: themes {lo}-{hi}")
        period_docs = [d for d in docs if lo <= d['year'] <= hi]
        p = period_themes(period_docs, min_freq=min_freq,
                          keep_terms=[normalize_term(t) for t in arguments.get("construct_terms") or []])
        p.update({'years': (lo, hi), 'n_docs': len(period_docs),
                  'n_with_terms': sum(1 for d in period_docs if d['terms'])})
        periods.append(p)
    links = evolution(periods)
    fate = fates(periods, links)
    construct = track_construct(periods, arguments.get("construct_terms") or []) \
        if arguments.get("construct_terms") else []

    label = query or (Path(from_file).stem if from_file else f"{len(docs)} papers")
    base = _base_name(f"themes-{label}")
    out = _output_dir()
    serial = [{k: v for k, v in p.items() if k not in ('co_occurrence', 'frequency', 'doc_terms')} for p in periods]
    json_path = out / f'{base}.json'
    json_path.write_text(json.dumps({'terms': mode, 'periods': serial, 'links': links,
                                     'construct': construct}, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    csv_path = out / f'{base}.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['period', 'theme', 'label', 'quadrant', 'centrality', 'density', 'papers', 'fate', 'terms'])
        for i, p in enumerate(periods):
            for t in p['themes']:
                w.writerow([f"{p['years'][0]}-{p['years'][1]}", t['id'], t['label'], t.get('quadrant'),
                            t['centrality'], t['density'], t['n_docs'], fate.get((i, t['id']), ''),
                            '; '.join(t['terms'])])
    png = render_strategic_png(periods, out / f'{base}.png', f"Thematic evolution: {label}",
                               [normalize_term(t) for t in arguments.get("construct_terms") or []])

    kinds = {}
    for d in docs:
        kinds[d['kind']] = kinds.get(d['kind'], 0) + 1
    lines = [f"Thematic evolution of {len(docs)} papers ({source}; terms: {mode}; "
             + ', '.join(f"{k} {v}" for k, v in sorted(kinds.items())) + ").",
             f"Server version: {srv.SERVER_VERSION}",
             f"JSON: {json_path}", f"CSV: {csv_path}"]
    if png:
        lines.append(f"Strategic diagrams (PNG): {png}")
    for i, p in enumerate(periods):
        lines.append(f"Period {i + 1}: {p['years'][0]}–{p['years'][1]}, {p['n_docs']} papers "
                     f"({p['n_with_terms']} with terms), {len(p['themes'])} themes from {p['n_terms']} "
                     f"keywords used by {min_freq}+ papers:")
        for t in p['themes'][:10]:
            f = fate.get((i, t['id']))
            lines.append(f"  T{i + 1}.{t['id']} [{t.get('quadrant')}] {t['label']} — {t['n_docs']} papers, "
                         f"centrality {t['centrality']:g}, density {t['density']:g}" + (f"; {f}" if f else ''))
        if len(p['themes']) > 10:
            lines.append(f"  ... {len(p['themes']) - 10} smaller themes in the files")
    if links:
        lines.append("Flows between periods (inclusion index; shared keywords):")
        for l in sorted(links, key=lambda l: (l['from_period'], -l['inclusion']))[:25]:
            lines.append(f"  T{l['from_period'] + 1}.{l['from']} {l['from_label']} → "
                         f"T{l['to_period'] + 1}.{l['to']} {l['to_label']} ({l['inclusion']}; "
                         f"{', '.join(l['shared'][:4])})")
    if construct:
        lines.append(f"Construct {', '.join(repr(t) for t in arguments['construct_terms'])} by period:")
        for c in construct:
            where = '; '.join(f"T{c['period'] + 1}.{t['id']} {t['label']} [{t['quadrant']}]"
                              for t in c['themes']) or (f'below min_freq ({min_freq} papers)'
                                                        if c['keywords'] else 'absent')
            lines.append(f"  {c['years'][0]}–{c['years'][1]}: in {c['doc_mentions']} of {c['n_docs']} "
                         f"papers' terms; theme: {where}; co-occurs with: "
                         f"{', '.join(c['co_occurs_with']) or '—'}")
    lines.append("Themes are Louvain clusters of keyword co-occurrence (equivalence index); quadrants "
                 "are relative to each period's median centrality and density.")
    return [types.TextContent(type="text", text='\n'.join(lines))]


HANDLERS = {'thematic_evolution': _thematic_evolution}
