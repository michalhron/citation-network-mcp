"""Bibliometrics: counts, topic landscape, journal metrics and cut-offs, BibTeX."""
from datetime import date as _today_date, datetime as _today_datetime
import json

import mcp.types as types

from ..bibtex import generated_entry, make_keys_unique
from ..journals import category_names, clean_openalex_source, clean_serial_entry, format_issn, normalize_issn, resolve_categories, serial_entry_issns, srcid_queries, subject_ranks, venue_type
from ..openalex import clean_openalex_work, openalex_work_key
from ..utils import _output_dir, clean_abstract_details, to_scopus_id
from .common import SOURCE_SCHEMA, _source, _write_rows_csv, server_module


# Cap on the year span of a Scopus publication_counts call (one request per year).
MAX_SCOPUS_YEARS = 60


# Cap on identifiers per get_bibtex call.
MAX_BIBTEX = 200


# Cap on journals per get_journal_metrics call.
MAX_JOURNALS = 200


# topic_landscape: papers analysed per call, and how a sample is chosen
# when a topic has more (Scopus sort order, wording for the coverage line).
MAX_LANDSCAPE_PAPERS = 2000


LANDSCAPE_SAMPLES = {
    'recent': ('coverDate', 'most recent'),
    'cited': ('citedby-count', 'most cited'),
    'relevance': ('relevancy', 'most relevant'),
}


def _bibtex_author(display_name: str) -> str:
    """'Norman P. Hummon' -> 'Hummon, Norman P.' (BibTeX 'Last, First')."""
    parts = display_name.split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else display_name


async def _bibtex_target(identifier: str):
    """(doi, fallback metadata, origin) for one identifier.

    DOIs pass straight through. OpenAlex IDs and Scopus IDs are looked up
    for their DOI, keeping their metadata for a generated entry when there
    is none (Scopus metadata needs no subscriber entitlement).
    """
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    key = openalex_work_key(identifier)
    if key and key.startswith('doi:'):
        return key[4:], None, None
    if key:
        work = await openalex.get_work(key)
        if not work:
            raise ValueError("not found in OpenAlex")
        rec = clean_openalex_work(work)
        authors = [_bibtex_author(a['author']['display_name'])
                   for a in (work.get('authorships') or [])
                   if (a.get('author') or {}).get('display_name')]
        meta = {'title': rec['title'], 'authors': authors,
                'year': rec['year'], 'venue': rec['publication_name']}
        return rec['doi'], meta, 'OpenAlex'
    details = clean_abstract_details(await client.get_abstract(to_scopus_id(identifier)))
    if not details.get('title'):
        raise ValueError("not found in Scopus")
    authors = []
    for a in details.get('authors') or []:
        surname = a.get('surname') or (a.get('name') or '').split(' ')[0]
        if surname:
            initials = a.get('initials')
            authors.append(f"{surname}, {initials}" if initials else surname)
    meta = {'title': details.get('title'), 'authors': authors,
            'year': (details.get('cover_date') or '')[:4] or None,
            'venue': details.get('publication_name')}
    return details.get('doi'), meta, 'Scopus'


TOOLS = [
    types.Tool(
        name="publication_counts",
        description=(
            "Count publications per year for a query, e.g. to chart how "
            "attention to a topic rose and fell. Scopus: your query in Scopus "
            "syntax, one request per year, so from_year and to_year are required "
            f"(at most {MAX_SCOPUS_YEARS} years). OpenAlex: plain words matched "
            "against title and abstract (quote phrases), one request for all "
            "years. The two sources count differently; compare trends within "
            "one source, not levels across sources."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Scopus: Scopus syntax, e.g. 'TITLE-ABS-KEY(\"organizing "
                        "vision\")'. OpenAlex: e.g. '\"organizing vision\"'."
                    )
                },
                "from_year": {"type": "integer", "description": "First year (inclusive)."},
                "to_year": {"type": "integer", "description": "Last year (inclusive)."},
                "source": SOURCE_SCHEMA
            },
            "required": ["query"]
        }
    ),
    types.Tool(
        name="topic_landscape",
        description=(
            "Where and at what prestige a topic is published. Runs a Scopus query "
            "and reports (1) papers per broad subject area over all results, and "
            "(2) per subject category, how many papers appear in Q1, Q2, Q3 and Q4 "
            "journals of that category, with the main journals. A journal can be "
            "Q1 in one category and Q3 in another, so each paper counts in every "
            "category of its journal. By default quartiles count journal papers "
            "only: proceedings series such as IFAC-PapersOnLine or Procedia CIRP "
            "also carry CiteScore ranks, and are reported separately with book "
            "series, together with the overall mix of venue types. Large topics are "
            f"analysed on a sample of max_papers papers (up to {MAX_LANDSCAPE_PAPERS}): most "
            "recent by default, or most cited to see where influential work appears; "
            "coverage is stated. Needs Scopus search entitlement."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Scopus query, e.g. 'TITLE-ABS-KEY(\"organizing vision\")'."
                },
                "from_year": {"type": "integer", "description": "First publication year."},
                "to_year": {"type": "integer", "description": "Last publication year."},
                "max_papers": {"type": "integer", "default": 500,
                               "description": f"Papers to analyse by quartile (default 500, max {MAX_LANDSCAPE_PAPERS})."},
                "top_categories": {"type": "integer", "default": 15,
                                   "description": "Categories to report, largest first."},
                "sample": {"type": "string", "enum": ["recent", "cited", "relevance"],
                           "default": "recent",
                           "description": "Which papers to analyse when the topic has more than max_papers: most recent, most cited (where influential work appears), or most relevant."},
                "journals_only": {"type": "boolean", "default": True,
                                  "description": "Count only journal papers in the quartiles; ranked conference proceedings and book series are reported separately. False counts every ranked venue."}
            },
            "required": ["query"]
        }
    ),
    types.Tool(
        name="get_journal_metrics",
        description=(
            "Journal metrics for a list of journals, e.g. a litbaskets basket. "
            "Scopus (default): SJR, SNIP, CiteScore and CiteScore Tracker with "
            "their years, plus subject areas, from the Serial Title API. Give "
            "ISSNs, or Scopus source IDs (SRCIDs), which are mapped to ISSNs "
            "through one Scopus search each (needs search entitlement). "
            "source='openalex': OpenAlex's own measures (2-year mean citedness, "
            "h-index, i10-index), ISSNs only, no entitlement. Journals not found "
            "are listed, never dropped. Also written to CSV. "
            f"At most {MAX_JOURNALS} journals per call."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "issns": {
                    "type": "array", "items": {"type": "string"},
                    "description": "ISSNs, with or without hyphen."
                },
                "source_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Scopus source IDs (SRCID), Scopus only."
                },
                "source": SOURCE_SCHEMA
            },
            "required": []
        }
    ),
    types.Tool(
        name="find_journals",
        description=(
            "List the journals in one or more Scopus subject categories at or above "
            "a CiteScore percentile within that category: the quality cut-off for "
            "scoping a literature review (Q1 = 75, top 10% = 90). Categories are ASJC "
            "names or codes, e.g. 'Information Systems' (1710), 'Management "
            "Information Systems' (1404); ambiguous names return the candidates. "
            "Returns each journal's rank, percentile, quartile and CiteScore, a CSV, "
            "and ready-to-use Scopus query fragments SRCID(...) for search_all. "
            "Percentiles are from the latest complete CiteScore year."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array", "items": {"type": "string"},
                    "description": "ASJC category names or 4-digit codes."
                },
                "min_percentile": {
                    "type": "integer", "default": 75, "minimum": 0, "maximum": 99,
                    "description": "Keep journals at or above this percentile in the category (75 = Q1, 90 = top 10%)."
                },
                "journals_only": {
                    "type": "boolean", "default": True,
                    "description": "Exclude book series, conference proceedings and trade journals."
                }
            },
            "required": ["categories"]
        }
    ),
    types.Tool(
        name="get_bibtex",
        description=(
            "BibTeX entries for a list of papers, written to a .bib file and "
            "returned inline. Identifiers may be DOIs, Scopus IDs/EIDs or "
            "OpenAlex work IDs. Entries come from the publisher's metadata via "
            "DOI content negotiation (errors included, so check author names). "
            "Papers without a DOI, such as AIS conference papers, get a minimal "
            "entry built from Scopus or OpenAlex metadata, marked with a note. "
            f"At most {MAX_BIBTEX} identifiers per call."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "identifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "DOIs, Scopus IDs/EIDs, or OpenAlex work IDs (W...)."
                }
            },
            "required": ["identifiers"]
        }
    ),
]


async def _publication_counts(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    query = arguments.get("query")
    if not query:
        raise ValueError("query is required")
    source = _source(arguments)
    from_year = arguments.get("from_year")
    to_year = arguments.get("to_year")
    from_year = int(from_year) if from_year is not None else None
    to_year = int(to_year) if to_year is not None else None
    if from_year is not None and to_year is not None and from_year > to_year:
        raise ValueError(f"from_year {from_year} is after to_year {to_year}")

    if source == 'openalex':
        counts, total = await openalex.yearly_counts(query, from_year, to_year)
        note = "OpenAlex title-and-abstract match."
    else:
        if from_year is None or to_year is None:
            raise ValueError(
                "Scopus publication_counts needs from_year and to_year "
                "(one request per year); OpenAlex does not."
            )
        if to_year - from_year + 1 > MAX_SCOPUS_YEARS:
            raise ValueError(
                f"Scopus publication_counts spans at most {MAX_SCOPUS_YEARS} "
                f"years; split the range or use source='openalex'."
            )
        counts = await client.yearly_counts(query, from_year, to_year)
        total = sum(counts.values())
        note = "Scopus search hits per PUBYEAR for the query as written."

    # Years with no records are absent from grouped results; show them
    # as zero within the requested range so gaps are visible.
    if counts or (from_year is not None and to_year is not None):
        lo = from_year if from_year is not None else min(counts)
        hi = to_year if to_year is not None else max(counts)
        counts = {y: counts.get(y, 0) for y in range(lo, hi + 1)}
    current_year = _today_date.today().year
    if counts and max(counts) >= current_year:
        note += f" {current_year} is incomplete."
    peak = max(counts, key=counts.get) if any(counts.values()) else None
    result = {
        'source': source,
        'query': query,
        'counts': {str(y): n for y, n in counts.items()},
        'total': total,
        'peak_year': peak,
        'note': note,
    }
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _topic_landscape(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    query = (arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    from_year, to_year = arguments.get("from_year"), arguments.get("to_year")
    full_query = f"({query})"
    if from_year:
        full_query += f" AND PUBYEAR > {int(from_year) - 1}"
    if to_year:
        full_query += f" AND PUBYEAR < {int(to_year) + 1}"
    max_papers = max(1, min(int(arguments.get("max_papers", 500)), MAX_LANDSCAPE_PAPERS))
    top_n = max(1, int(arguments.get("top_categories", 15)))
    journals_only = bool(arguments.get("journals_only", True))
    sample = arguments.get("sample") or "recent"
    if sample not in LANDSCAPE_SAMPLES:
        raise ValueError(f"sample must be one of {list(LANDSCAPE_SAMPLES)}")

    # 1. Broad subject areas over the whole result set (one request).
    facet_data = (await client.search_facets(full_query, 'subjarea(count=30)')).get('search-results') or {}
    total = int(facet_data.get('opensearch:totalResults') or 0)
    broad = []
    for f in (facet_data.get('facet') if isinstance(facet_data.get('facet'), list)
              else [facet_data.get('facet')] if facet_data.get('facet') else []):
        cats = f.get('category')
        for c in (cats if isinstance(cats, list) else [cats] if cats else []):
            broad.append({'area': (c.get('label') or c.get('name') or '').replace(' (all)', ''),
                          'papers': int(c.get('hitCount') or 0)})

    # 2. The papers themselves, for per-category quartiles.
    raw = await client.search_all(full_query, max_results=max_papers,
                                  sort=LANDSCAPE_SAMPLES[sample][0])
    entries = (raw.get('search-results') or {}).get('entry') or []

    # 3. Their journals' per-category percentiles.
    venue = {}
    for e in entries:
        sid = e.get('source-id')
        if sid and sid not in venue:
            venue[sid] = {'name': e.get('prism:publicationName'),
                          'type': venue_type(e.get('prism:aggregationType')),
                          'issns': [i for i in (normalize_issn(e.get('prism:issn')),
                                                normalize_issn(e.get('prism:eIssn'))) if i]}
    names = category_names(await client.asjc_categories())
    ranks_by_sid = {}
    issns = sorted({i for v in venue.values() for i in v['issns']})
    for entry in (await client.serial_titles(issns) if issns else []):
        sid = str(entry.get('source-id') or '')
        if sid and sid not in ranks_by_sid:
            ranks_by_sid[sid] = subject_ranks(entry, names)
            # Serial Title's type is authoritative for ranked venues.
            if sid in venue and entry.get('prism:aggregationType'):
                venue[sid]['type'] = venue_type(entry.get('prism:aggregationType'))

    # 4. Count each paper under every category of its journal.
    from collections import Counter
    per_cat, unranked_venues = {}, Counter()
    unranked = 0
    mix = Counter()
    for e in entries:
        sid = e.get('source-id')
        kind = (venue.get(sid) or {}).get('type') or venue_type(e.get('prism:aggregationType'))
        ranks = (ranks_by_sid.get(str(sid)) or {}).get('ranks') or []
        mix[kind if ranks or kind != 'journal' else 'journal (unranked)'] += 1
        if not ranks:
            unranked += 1
            unranked_venues[(e.get('prism:publicationName') or 'unknown', kind)] += 1
            continue
        for r in ranks:
            c = per_cat.setdefault(r['code'], {
                'code': r['code'], 'category': r['category'] or names.get(r['code']),
                'papers': 0, 'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0,
                'ranked_non_journal': 0, '_journals': Counter(), '_other': Counter()})
            if journals_only and kind != 'journal':
                c['ranked_non_journal'] += 1
                c['_other'][(venue.get(sid, {}).get('name'), kind, r['quartile'])] += 1
                continue
            c['papers'] += 1
            c[r['quartile']] += 1
            c['_journals'][(venue.get(sid, {}).get('name'), kind, r['quartile'], r['percentile'])] += 1

    categories = []
    ranked = sorted(per_cat.values(), key=lambda c: -(c['papers'] + c['ranked_non_journal']))
    for c in ranked[:top_n]:
        journals, other = c.pop('_journals'), c.pop('_other')
        c['q1_share'] = round(c['Q1'] / c['papers'], 2) if c['papers'] else None
        c['top_journals'] = [{'journal': j, 'type': t, 'quartile': q, 'percentile': p, 'papers': n}
                             for (j, t, q, p), n in journals.most_common(3)]
        if journals_only:
            c['top_non_journal'] = [{'venue': v, 'type': t, 'quartile': q, 'papers': n}
                                    for (v, t, q), n in other.most_common(3)]
        categories.append(c)

    sampled = len(entries)
    result = {
        'query': full_query,
        'total_papers': total,
        'analysed_papers': sampled,
        'coverage': ('complete' if sampled >= total else
                     f"{LANDSCAPE_SAMPLES[sample][1]} {sampled} of {total} "
                     f"({round(100 * sampled / total)}%)"),
        'broad_areas_all_results': broad,
        'categories': categories,
        'quartiles_count': 'journal papers only' if journals_only else 'all ranked venues',
        'venue_mix': {k: {'papers': n, 'share': round(n / sampled, 2)}
                      for k, n in mix.most_common()} if sampled else {},
        'unranked': {'papers': unranked,
                     'share': round(unranked / sampled, 2) if sampled else None,
                     'top_venues': [{'venue': v, 'type': t, 'papers': n}
                                    for (v, t), n in unranked_venues.most_common(5)]},
        'notes': [
            "Quartiles are per category from each journal's latest complete CiteScore "
            "year (current standing, not standing at publication).",
            "A paper counts once in every category of its journal, so category "
            "totals add up to more than the papers analysed.",
            "Unranked papers appeared in venues without CiteScore ranks, typically "
            "conference proceedings, books or new journals.",
            "With journals_only (the default), Q1-Q4 count journal papers; papers in "
            "ranked proceedings and book series are in ranked_non_journal.",
        ],
    }
    flat = [{'category_code': c['code'], 'category': c['category'], 'papers': c['papers'],
             'Q1': c['Q1'], 'Q2': c['Q2'], 'Q3': c['Q3'], 'Q4': c['Q4'],
             'q1_share': c['q1_share'], 'ranked_non_journal': c['ranked_non_journal']}
            for c in categories]
    if flat:
        result['csv_path'] = str(_write_rows_csv(flat, 'landscape'))
    return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def _get_journal_metrics(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    raw_issns = [str(i).strip() for i in (arguments.get("issns") or []) if str(i).strip()]
    source_ids = [str(i).strip() for i in (arguments.get("source_ids") or []) if str(i).strip()]
    source = _source(arguments)
    if not raw_issns and not source_ids:
        raise ValueError("Give issns and/or source_ids.")
    if len(raw_issns) + len(source_ids) > MAX_JOURNALS:
        raise ValueError(f"At most {MAX_JOURNALS} journals per call.")
    if source == 'openalex' and source_ids:
        raise ValueError("OpenAlex has no Scopus source IDs; pass ISSNs, "
                         "or use source='scopus'.")

    rows, not_found = [], []
    issn_inputs = []
    for raw in raw_issns:
        compact = normalize_issn(raw)
        if compact:
            issn_inputs.append((raw, compact))
        else:
            not_found.append(f"{raw}: not a valid ISSN")

    if source == 'openalex':
        for raw, compact in issn_inputs:
            src = await openalex.get_source_by_issn(format_issn(compact))
            if src:
                rows.append({'input': raw, **clean_openalex_source(src)})
            else:
                not_found.append(f"{raw}: not in OpenAlex")
        note = ("OpenAlex measures (2-year mean citedness, h-index, i10-index); "
                "not comparable with SJR, SNIP or CiteScore.")
    else:
        srcid_issns = {}
        for sid in source_ids:
            try:
                found = await client.source_id_issns(sid)
            except Exception as exc:
                not_found.append(f"source_id {sid}: lookup failed ({exc})")
                continue
            if not found:
                not_found.append(f"source_id {sid}: no Scopus records")
                continue
            srcid_issns[sid] = [c for c in (normalize_issn(found['issn']),
                                            normalize_issn(found['eissn'])) if c]
        candidates = sorted({c for _, c in issn_inputs}
                            | {c for cs in srcid_issns.values() for c in cs})
        entries = await client.serial_titles(candidates) if candidates else []
        by_issn, by_srcid = {}, {}
        for entry in entries:
            cleaned = clean_serial_entry(entry)
            for c in serial_entry_issns(entry):
                by_issn.setdefault(c, cleaned)
            if cleaned['source_id']:
                by_srcid.setdefault(str(cleaned['source_id']), cleaned)

        for raw, compact in issn_inputs:
            hit = by_issn.get(compact)
            if hit:
                rows.append({'input': raw, **hit})
            else:
                not_found.append(
                    f"{raw}: not in Serial Title under this ISSN. Scopus may "
                    f"list the journal under its other (print/electronic) ISSN; "
                    f"try that, or its source_id.")
        for sid, cands in srcid_issns.items():
            hit = by_srcid.get(sid) or next(
                (by_issn[c] for c in cands if c in by_issn), None)
            if hit:
                rows.append({'input': f"source_id {sid}", **hit})
            else:
                not_found.append(f"source_id {sid}: no Serial Title entry for "
                                 f"ISSNs {[format_issn(c) for c in cands]}")
        note = "Latest year of each metric from the Scopus Serial Title API."

    result = {'source': source, 'journals': rows, 'not_found': not_found, 'note': note}
    if rows:
        result['csv_path'] = str(_write_rows_csv(rows, f'journals-{source}'))
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _find_journals(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    wanted = [str(c).strip() for c in (arguments.get("categories") or []) if str(c).strip()]
    if not wanted:
        raise ValueError("categories is required")
    min_pct = int(arguments.get("min_percentile", 75))
    if not 0 <= min_pct <= 99:
        raise ValueError("min_percentile must be between 0 and 99")
    journals_only = bool(arguments.get("journals_only", True))
    asjc = await client.asjc_categories()
    names = category_names(asjc)
    categories = resolve_categories(wanted, asjc)

    rows, per_category = [], []
    for cat in categories:
        entries = await client.journals_in_category(cat['code'])
        listed = kept = unranked = 0
        for e in entries:
            if journals_only and (e.get('prism:aggregationType') or '').lower() != 'journal':
                continue
            listed += 1
            ranks = subject_ranks(e, names)
            here = next((r for r in ranks['ranks'] if r['code'] == cat['code']), None)
            if here is None:
                unranked += 1
                continue
            if here['percentile'] < min_pct:
                continue
            kept += 1
            rows.append({
                'title': e.get('dc:title'), 'publisher': e.get('dc:publisher'),
                'issn': format_issn(normalize_issn(e.get('prism:issn'))),
                'eissn': format_issn(normalize_issn(e.get('prism:eIssn'))),
                'source_id': e.get('source-id'),
                'category_code': cat['code'], 'category': cat['category'],
                'percentile': here['percentile'], 'rank': here['rank'],
                'quartile': here['quartile'],
                'citescore': ranks['citescore'], 'citescore_year': ranks['year'],
            })
        per_category.append({**cat, 'titles_listed': listed, 'without_rank': unranked,
                             'at_or_above_cutoff': kept})
    rows.sort(key=lambda r: (r['category_code'], -r['percentile']))
    unique_ids = list(dict.fromkeys(
        r['source_id'] for r in sorted(rows, key=lambda r: -r['percentile']) if r['source_id']))
    result = {
        'min_percentile': min_pct,
        'categories': per_category,
        'unique_journals': len(unique_ids),
        'scopus_query_fragments': srcid_queries(unique_ids),
        'note': ("Percentile and quartile are within each category, from the latest "
                 "complete CiteScore year. Combine a fragment with a topic query, e.g. "
                 "TITLE-ABS-KEY(...) AND SRCID(...), in search_all."),
    }
    if rows:
        result['csv_path'] = str(_write_rows_csv(rows, f'journals-p{min_pct}'))
    if len(rows) <= 60:
        result['journals'] = rows
    else:
        result['journals_sample'] = rows[:20]
    return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def _get_bibtex(arguments: dict) -> list:
    srv = server_module()
    fetch_bibtex = srv.fetch_bibtex
    identifiers = [str(i).strip() for i in (arguments.get("identifiers") or [])
                   if str(i).strip()]
    if not identifiers:
        raise ValueError("identifiers is required and must be non-empty")
    if len(identifiers) > MAX_BIBTEX:
        raise ValueError(f"At most {MAX_BIBTEX} identifiers per call.")

    targets = []
    failures = []
    for ident in identifiers:
        try:
            targets.append((ident, *await _bibtex_target(ident)))
        except Exception as exc:
            failures.append(f"{ident}: {exc}")

    dois = sorted({doi.lower() for _, doi, _, _ in targets if doi})
    fetched = await fetch_bibtex(dois) if dois else {}

    entries = []
    generated = 0
    seen_dois = set()
    for ident, doi, meta, origin in targets:
        if doi:
            doi = doi.lower()
            if doi in seen_dois:
                continue  # same paper given twice
            seen_dois.add(doi)
            entry, err = fetched[doi]
            if entry:
                entries.append(entry)
                continue
            if not meta:
                failures.append(f"{ident}: {err}")
                continue
        entry = generated_entry(meta or {}, origin or 'available')
        if entry:
            entries.append(entry)
            generated += 1
        else:
            failures.append(f"{ident}: no DOI and no title to build an entry from")

    entries = make_keys_unique(entries)
    text = f"{len(entries)} BibTeX entries"
    if generated:
        text += f" ({generated} generated from metadata; check them)"
    if entries:
        ts = _today_datetime.now().strftime('%Y%m%dT%H%M%S')
        path = _output_dir() / f'bibtex-{len(entries)}-{ts}.bib'
        path.write_text('\n\n'.join(entries) + '\n', encoding='utf-8')
        text += f". Written to: {path}\n"
    else:
        text += ".\n"
    if failures:
        text += "Not resolved:\n" + '\n'.join(f"  {f}" for f in failures) + "\n"
    if entries:
        shown = entries[:50]
        text += "\n" + '\n\n'.join(shown)
        if len(entries) > len(shown):
            text += f"\n\n({len(entries) - len(shown)} more in the file.)"
    return [types.TextContent(type="text", text=text)]


HANDLERS = {
    'publication_counts': _publication_counts,
    'topic_landscape': _topic_landscape,
    'get_journal_metrics': _get_journal_metrics,
    'find_journals': _find_journals,
    'get_bibtex': _get_bibtex,
}
