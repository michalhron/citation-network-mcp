"""Citation networks: bibliographic coupling, co-citation, lineage."""
import logging
import mcp.types as types

from ..openalex import clean_openalex_work, short_id
from ..graphs import _make_node_label, compute_pairwise_edges, write_graph_to_disk
from ..lineage import compute_main_path, render_lineage_html, render_lineage_png, write_lineage_to_disk
from ..output import _query_slug
from ..records import clean_abstract_details, clean_references, clean_search_results, to_eid, to_scopus_id
from .common import SOURCE_SCHEMA, _resolve_openalex_work, _source, server_module

logger = logging.getLogger("citation-network-mcp")


async def _openalex_seed_sets(seed_ids: list, mode: str, max_citing: int = 500):
    """Per-seed reference sets ('references') or citer sets ('citing').

    Returns (seed_sets, seed_meta, skipped) keyed by OpenAlex work ID, the
    same structure the Scopus branches build.
    """
    srv = server_module()
    openalex = srv.openalex
    seed_sets: dict = {}
    seed_meta: dict = {}
    skipped: list = []
    for raw_id in seed_ids:
        try:
            work = await _resolve_openalex_work(raw_id)
            wid = short_id(work.get('id'))
            rec = clean_openalex_work(work)
            seed_meta[wid] = {
                'title': rec['title'] or wid,
                'creator': rec['creator'],
                'year': rec['year'],
                'venue': rec['publication_name'],
            }
            if mode == 'references':
                keys = {short_id(r) for r in (work.get('referenced_works') or [])}
            else:
                citers, _ = await openalex.citing(wid, max_results=max_citing)
                keys = {short_id(c.get('id')) for c in citers}
            keys.discard(wid)
        except Exception as exc:
            logger.warning(f"openalex {mode} for {raw_id}: {exc}")
            skipped.append(f"{raw_id} ({exc})")
            continue
        if not keys:
            # Common for AIS eLibrary papers: OpenAlex indexes them without
            # reference lists.
            what = 'references' if mode == 'references' else 'citing works'
            skipped.append(f"{raw_id} (OpenAlex {wid} lists no {what})")
            continue
        seed_sets[wid] = keys
    return seed_sets, seed_meta, skipped


def _network_response(label: str, file_prefix: str, seed_sets: dict, seed_meta: dict,
                      n_seeds: int, params_note: str, skipped: list,
                      skipped_reason: str, source: str, min_shared: int):
    """Shared tail of bibliographic_coupling and co_citation."""
    edges = compute_pairwise_edges(seed_sets, min_shared=min_shared)
    nodes = [
        {
            'id': sid,
            'label': _make_node_label(seed_meta.get(sid, {}), node_id=sid),
            'title': seed_meta.get(sid, {}).get('title'),
            'creator': seed_meta.get(sid, {}).get('creator'),
            'year': seed_meta.get(sid, {}).get('year'),
            'venue': seed_meta.get(sid, {}).get('venue'),
        }
        for sid in seed_sets
    ]
    prefix = f'{file_prefix}-openalex' if source == 'openalex' else file_prefix
    paths = write_graph_to_disk(nodes, edges, f'{prefix}-{n_seeds}seeds')

    top10 = edges[:10]
    top10_lines = [
        f"  {seed_meta.get(e['source'], {}).get('title', e['source'])!r} → "
        f"{seed_meta.get(e['target'], {}).get('title', e['target'])!r}: "
        f"weight={e['weight']}, cosine={e['cosine']:.4f}"
        for e in top10
    ]
    text = (
        f"{label}: {len(seed_sets)}/{n_seeds} seeds processed, "
        f"{len(edges)} edges emitted ({params_note}).\n"
    )
    if source == 'openalex':
        text += "Source: OpenAlex (node IDs are OpenAlex work IDs).\n"
    text += (
        f"Graph files:\n"
        f"  GraphML: {paths['graphml_path']}\n"
        f"  CSV:     {paths['csv_path']}\n"
    )
    if paths.get('png_path'):
        text += f"  PNG:     {paths['png_path']}\n"
    text += f"\nTop {len(top10)} edges by weight:\n"
    text += '\n'.join(top10_lines) if top10_lines else '  (none)'
    if skipped:
        text += f"\n\nSkipped ({skipped_reason}): {skipped}"
    return [types.TextContent(type="text", text=text)]


TOOLS = [
    types.Tool(
        name="bibliographic_coupling",
        description=(
            "Build a bibliographic-coupling graph for a set of seed papers. "
            "Two seeds are coupled when they share cited references; edge weight = "
            "count of shared references, cosine = Salton index. "
            "Maps the current research front. Scopus needs an entitled (subscriber) "
            "key for REF-view access; source='openalex' does not. "
            "Output: GraphML + CSV edge list written to disk."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "seed_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Seed papers: Scopus IDs (bare numeric or SCOPUS_ID: prefixed). "
                        "With source='openalex', DOIs and OpenAlex work IDs also work."
                    )
                },
                "source": SOURCE_SCHEMA,
                "min_shared": {
                    "type": "integer",
                    "description": "Minimum shared references for an edge to be emitted (default 2).",
                    "default": 2
                }
            },
            "required": ["seed_ids"]
        }
    ),
    types.Tool(
        name="co_citation",
        description=(
            "Build a co-citation graph for a set of seed papers. "
            "Two seeds are co-cited when a later paper cites both; edge weight = "
            "count of co-citing papers, cosine = Salton index. "
            "Maps the intellectual base of a field. "
            "max_citing_per_seed bounds the API quota used per seed. "
            "Output: GraphML + CSV edge list written to disk."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "seed_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Seed papers: Scopus IDs (bare numeric or SCOPUS_ID: prefixed). "
                        "With source='openalex', DOIs and OpenAlex work IDs also work."
                    )
                },
                "source": SOURCE_SCHEMA,
                "min_shared": {
                    "type": "integer",
                    "description": "Minimum co-citing papers for an edge to be emitted (default 2).",
                    "default": 2
                },
                "max_citing_per_seed": {
                    "type": "integer",
                    "description": "Cap on citing papers fetched per seed (default 500). Limits quota usage.",
                    "default": 500
                }
            },
            "required": ["seed_ids"]
        }
    ),
    types.Tool(
        name="citation_lineage",
        description=(
            "Walk the citation lineage of a seed paper across multiple generations. "
            "Forward: generation 1 = papers that cite the seed; generation 2 = papers "
            "that cite those; up to 3 generations. Backward: walks cited references. "
            "All papers are deduplicated globally. Output: corpus written to disk as "
            "JSON plus compact inline summary; the corpus is also returned inline as "
            "base64 so sandboxed callers can inspect it. Use sort='citedby' (default "
            "for forward) to collect the most-cited citers first, which gives a "
            "meaningful citation-backbone; sort='coverDate' collects the most recent "
            "citers first (which can produce a recency-dominated walk). "
            "source='openalex' walks OpenAlex instead (no Scopus entitlement; "
            "node IDs are OpenAlex work IDs; reference lists are thinner and "
            "absent for AIS eLibrary papers). "
            "Server version is included in every response."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": (
                        "Scopus ID or EID of the seed paper. With source='openalex', "
                        "a DOI or OpenAlex work ID also works."
                    )
                },
                "source": SOURCE_SCHEMA,
                "generations": {
                    "type": "integer",
                    "description": "Number of generations to walk (default 1, max 3).",
                    "default": 1,
                    "maximum": 3
                },
                "max_per_node": {
                    "type": "integer",
                    "description": "Cap on citing papers fetched per paper per generation (default 200).",
                    "default": 200
                },
                "min_citing": {
                    "type": "integer",
                    "description": (
                        "Only expand papers that have at least this many citing papers "
                        "(default 0 = expand all up to max_per_node). Pruning high "
                        "values avoids exploding on trivially-cited nodes. "
                        "Ignored for backward direction."
                    ),
                    "default": 0
                },
                "direction": {
                    "type": "string",
                    "description": (
                        "'forward' (default): walk citing papers via search_all + REF(). "
                        "Fan-out can be large; use max_per_node to bound quota. "
                        "'backward': walk cited references via get_references, "
                        "up to max_per_node per paper; references with no ID "
                        "are skipped."
                    ),
                    "enum": ["forward", "backward"],
                    "default": "forward"
                },
                "sort": {
                    "type": "string",
                    "description": (
                        "How to rank citing papers before the max_per_node cap is "
                        "applied (forward direction only; ignored for backward). "
                        "'citedby' (default): highest citation count first — captures "
                        "the high-flow backbone. "
                        "'coverDate': most recent first — captures the current fringe "
                        "but may produce a recency-dominated walk on high-citation seeds. "
                        "'relevancy': Scopus relevance score (Scopus only)."
                    ),
                    "enum": ["citedby", "coverDate", "relevancy"],
                    "default": "citedby"
                }
            },
            "required": ["seed_id"]
        }
    ),
]


async def _bibliographic_coupling(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    seed_ids_raw = arguments.get("seed_ids", [])
    min_shared = int(arguments.get("min_shared", 2))
    source = _source(arguments)

    if not seed_ids_raw:
        raise ValueError("seed_ids is required and must be non-empty")

    seed_sets: dict = {}
    seed_meta: dict = {}
    skipped: list = []

    if source == 'openalex':
        seed_sets, seed_meta, skipped = await _openalex_seed_sets(seed_ids_raw, 'references')
    else:
        for raw_id in seed_ids_raw:
            sid = to_scopus_id(str(raw_id))
            try:
                raw = await client.get_references(sid)
                refs = clean_references(raw)
                ref_keys: set = set()
                for r in refs:
                    key = r.get('scopus_id') or r.get('doi')
                    if key and key != sid:
                        ref_keys.add(key)
                if not ref_keys:
                    skipped.append(sid)
                    logger.info(f"bibliographic_coupling: {sid} has no usable refs, skipping")
                    continue
                seed_sets[sid] = ref_keys
                # The REF view's coredata often lacks the title; fall back to
                # the (cached) abstract so labels are not bare IDs.
                details = clean_abstract_details(raw)
                if not details.get('title'):
                    details = clean_abstract_details(await client.get_abstract(sid))
                authors = details.get('authors') or []
                seed_meta[sid] = {
                    'title': details.get('title') or sid,
                    'creator': authors[0].get('name') if authors else None,
                    'year': (details.get('cover_date') or '')[:4] or None,
                    'venue': details.get('publication_name'),
                }
            except Exception as exc:
                logger.warning(f"bibliographic_coupling: error for {sid}: {exc}")
                skipped.append(sid)

    return _network_response(
        'Bibliographic coupling', 'bibcoupling', seed_sets, seed_meta,
        len(seed_ids_raw), f'min_shared={min_shared}', skipped,
        'no usable references or API error', source, min_shared,
    )


async def _co_citation(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    seed_ids_raw = arguments.get("seed_ids", [])
    min_shared = int(arguments.get("min_shared", 2))
    max_citing = int(arguments.get("max_citing_per_seed", 500))
    source = _source(arguments)

    if not seed_ids_raw:
        raise ValueError("seed_ids is required and must be non-empty")

    seed_sets = {}
    seed_meta = {}
    skipped = []

    if source == 'openalex':
        seed_sets, seed_meta, skipped = await _openalex_seed_sets(
            seed_ids_raw, 'citing', max_citing=max_citing)
    else:
        for raw_id in seed_ids_raw:
            sid = to_scopus_id(str(raw_id))
            # Fetch seed metadata (one abstract call per seed)
            try:
                raw_meta = await client.get_abstract(sid)
                details = clean_abstract_details(raw_meta)
                authors = details.get('authors') or []
                seed_meta[sid] = {
                    'title': details.get('title') or sid,
                    'creator': authors[0].get('name') if authors else None,
                    'year': (details.get('cover_date') or '')[:4] or None,
                    'venue': details.get('publication_name'),
                }
            except Exception as exc:
                logger.warning(f"co_citation: metadata fetch failed for {sid}: {exc}")
                seed_meta[sid] = {'title': sid, 'creator': None, 'year': None, 'venue': None}

            # Fetch citing papers via search_all with REF() query
            try:
                raw_citers = await client.search_all(
                    f"REF({to_eid(sid)})", max_results=max_citing
                )
                citers = clean_search_results(raw_citers)
                citer_ids: set = set()
                for c in citers:
                    cid = c.get('scopus_id')
                    if cid and cid != sid:
                        citer_ids.add(cid)
                if not citer_ids:
                    skipped.append(sid)
                    logger.info(f"co_citation: {sid} has no citing papers, skipping")
                    continue
                seed_sets[sid] = citer_ids
            except Exception as exc:
                logger.warning(f"co_citation: citing fetch failed for {sid}: {exc}")
                skipped.append(sid)

    return _network_response(
        'Co-citation', 'cocitation', seed_sets, seed_meta,
        len(seed_ids_raw),
        f'min_shared={min_shared}, max_citing_per_seed={max_citing}',
        skipped, 'no citing papers or API error', source, min_shared,
    )


async def _citation_lineage(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    SERVER_VERSION = srv.SERVER_VERSION
    seed_id_raw = arguments.get("seed_id")
    if not seed_id_raw:
        raise ValueError("seed_id is required")

    generations = min(int(arguments.get("generations", 1)), 3)
    max_per_node = int(arguments.get("max_per_node", 200))
    min_citing = int(arguments.get("min_citing", 0))
    direction = (arguments.get("direction") or "forward").lower()
    if direction not in ("forward", "backward"):
        raise ValueError("direction must be 'forward' or 'backward'")

    # sort controls ranking of citing papers before the max_per_node cap.
    # Default 'citedby' for forward walks ensures the backbone (high-citation
    # papers) is captured, not the current fringe.  Scopus API sort values:
    # 'citedby-count', 'coverDate', 'relevancy'.
    _SORT_MAP = {
        'citedby':   'citedby-count',
        'coverDate': 'coverDate',
        'relevancy': 'relevancy',
    }
    sort_arg = (arguments.get("sort") or ("citedby" if direction == "forward" else "coverDate"))
    if sort_arg not in _SORT_MAP:
        raise ValueError(f"sort must be one of {list(_SORT_MAP)}")
    api_sort = _SORT_MAP[sort_arg]
    source = _source(arguments)
    if source == 'openalex' and sort_arg == 'relevancy':
        raise ValueError(
            "sort='relevancy' is Scopus-only; OpenAlex cannot rank citing "
            "works by relevance. Use 'citedby' or 'coverDate'."
        )

    if source == 'openalex':
        try:
            seed_work = await _resolve_openalex_work(seed_id_raw)
        except Exception as exc:
            raise ValueError(
                f"Could not resolve seed {seed_id_raw} in OpenAlex: {exc}"
            ) from exc
        seed_rec = clean_openalex_work(seed_work)
        seed_id = seed_rec['openalex_id']
        seed_paper = {
            'scopus_id': None,
            'openalex_id': seed_id,
            'doi': seed_rec['doi'],
            'title': seed_rec['title'],
            'year': seed_rec['year'],
            'venue': seed_rec['publication_name'],
            'generation': 0,
            'parents': [],
            'cited_by_count': seed_rec['cited_by_count'],
        }
    else:
        seed_id = to_scopus_id(str(seed_id_raw))

        # Fetch seed metadata (generation 0)
        try:
            raw_meta = await client.get_abstract(seed_id)
            details = clean_abstract_details(raw_meta)
        except Exception as exc:
            raise ValueError(
                f"Could not fetch seed metadata for {seed_id}: {exc}"
            ) from exc

        seed_paper = {
            'scopus_id': seed_id,
            'doi': details.get('doi'),
            'title': details.get('title'),
            'year': (details.get('cover_date') or '')[:4] or None,
            'venue': details.get('publication_name'),
            'generation': 0,
            'parents': [],
            'cited_by_count': details.get('cited_by_count'),
        }

    seen: set = {seed_id}
    all_papers: dict = {seed_id: seed_paper}
    to_expand = [seed_id]
    api_calls = 1  # the get_abstract above
    gen_counts: dict = {0: 1}

    async def _fetch_next(parent_id: str) -> list:
        """Return a flat list of candidate-paper dicts for the next generation.

        Each dict carries: key (stable dedup id), scopus_id, doi, title,
        year, venue, cited_by_count.  The only difference between directions
        is which client method is called and how the raw result is mapped.
        """
        if source == 'openalex':
            if direction == 'forward':
                works, _ = await openalex.citing(
                    parent_id, max_results=max_per_node, sort=sort_arg)
            else:
                # Singleton lookups are free and cached; hydrating the
                # reference list costs one request per 50 references.
                parent_work = await openalex.get_work(parent_id)
                works = (await openalex.references(parent_work, limit=max_per_node)
                         if parent_work else [])
            result = []
            for w in works:
                rec = clean_openalex_work(w)
                if not rec['openalex_id']:
                    continue
                result.append({
                    'key': rec['openalex_id'],
                    'scopus_id': None,
                    'openalex_id': rec['openalex_id'],
                    'doi': rec['doi'],
                    'title': rec['title'],
                    'year': rec['year'],
                    'venue': rec['publication_name'],
                    'cited_by_count': rec['cited_by_count'],
                })
            return result
        if direction == 'forward':
            raw = await client.search_all(
                f"REF({to_eid(parent_id)})",
                max_results=max_per_node,
                sort=api_sort,
            )
            return [
                {
                    'key': p['scopus_id'],
                    'scopus_id': p['scopus_id'],
                    'doi': p.get('doi'),
                    'title': p.get('title'),
                    'year': (p.get('cover_date') or '')[:4] or None,
                    'venue': p.get('publication_name'),
                    'cited_by_count': p.get('cited_by_count') or '0',
                }
                for p in clean_search_results(raw)
                if p.get('scopus_id')
            ]
        else:  # backward — walk cited references
            raw = await client.get_references(parent_id)
            result = []
            for r in clean_references(raw, limit=max_per_node):
                sid = r.get('scopus_id')
                doi = r.get('doi')
                if not sid and not doi:
                    continue
                # Prefer scopus_id as key; fall back to doi: prefix to avoid collisions
                key = sid if sid else f'doi:{doi}'
                result.append({
                    'key': key,
                    'scopus_id': sid,
                    'doi': doi,
                    'title': r.get('title'),
                    'year': r.get('year'),
                    'venue': r.get('source'),
                    'cited_by_count': None,
                })
            return result

    fetch_attempts = 0
    fetch_errors: list = []  # list of (parent_id, error_message)

    for gen in range(1, generations + 1):
        next_to_expand = []
        for parent_id in to_expand:
            if not parent_id:
                continue
            fetch_attempts += 1
            try:
                papers = await _fetch_next(parent_id)
                api_calls += 1
            except Exception as exc:
                err_msg = str(exc)
                logger.warning(
                    f"citation_lineage ({direction}): fetch failed for "
                    f"{parent_id}: {err_msg}"
                )
                fetch_errors.append((parent_id, err_msg))
                continue

            for p in papers:
                key = p['key']
                if key in seen:
                    # Record an additional parent only when it is from the
                    # immediately preceding generation (parent_gen == child_gen - 1).
                    # Skipping same-generation parents prevents within-generation
                    # edges from entering the lineage DAG and derailing the SPC
                    # main-path (the backward-walk bug: a gen-1 paper's references
                    # can include other gen-1 papers, which would otherwise create
                    # gen1→gen1 edges that the SPC greedy walk traverses sideways).
                    if key in all_papers and parent_id not in all_papers[key]['parents']:
                        child_gen = all_papers[key]['generation']
                        parent_gen = all_papers.get(parent_id, {}).get('generation')
                        if parent_gen is not None and parent_gen == child_gen - 1:
                            all_papers[key]['parents'].append(parent_id)
                    continue
                seen.add(key)
                cbc_str = p.get('cited_by_count')
                try:
                    cbc = int(cbc_str or 0)
                except (ValueError, TypeError):
                    cbc = 0
                all_papers[key] = {
                    'scopus_id': p['scopus_id'],
                    'doi': p['doi'],
                    'title': p['title'],
                    'year': p['year'],
                    'venue': p['venue'],
                    'generation': gen,
                    'parents': [parent_id],
                    'cited_by_count': cbc_str,
                }
                if p.get('openalex_id'):
                    all_papers[key]['openalex_id'] = p['openalex_id']
                gen_counts[gen] = gen_counts.get(gen, 0) + 1

                # Expansion criteria for the next generation.
                # Only expand papers the backend can fetch by ID.
                # min_citing applies to forward only (backward refs lack cbc).
                expand_id = p.get('openalex_id') or p['scopus_id']
                if gen < generations and expand_id:
                    if direction == 'backward' or cbc >= min_citing:
                        next_to_expand.append(expand_id)

        to_expand = next_to_expand
        if not to_expand:
            break

    records_list = list(all_papers.values())

    # Main-path analysis (SPC)
    mp_result = compute_main_path(records_list)
    main_path_ids = mp_result['main_path']
    spc_edges = mp_result['edges']

    # Consistent base filename for all three output files
    import base64
    import json as _json
    from datetime import datetime
    _ts = datetime.now().strftime('%Y%m%dT%H%M%S')
    _slug = _query_slug(
        f'lineage-openalex-{seed_id}' if source == 'openalex' else f'lineage-{seed_id}')
    base_fname = f'scopus-{_slug}-{_ts}'

    json_path = write_lineage_to_disk(
        records_list, seed_id,
        main_path=main_path_ids, spc_edges=spc_edges,
        base_filename=base_fname,
    )
    html_path = render_lineage_html(records_list, main_path_ids, seed_id, base_fname)
    png_path = render_lineage_png(records_list, main_path_ids, seed_id, base_fname)

    # Inline corpus as base64 so sandboxed callers can access it
    # without host filesystem access.
    _corpus_payload = {
        'seed_id': seed_id,
        'records': records_list,
        'main_path': main_path_ids,
        'spc_edges': spc_edges,
    }
    corpus_b64 = base64.b64encode(
        _json.dumps(_corpus_payload, ensure_ascii=False).encode('utf-8')
    ).decode('ascii')

    non_seed = [r for r in records_list if r['generation'] > 0]
    top10 = sorted(
        non_seed,
        key=lambda r: int(r.get('cited_by_count') or 0),
        reverse=True,
    )[:10]

    gen_summary = ', '.join(
        f"gen {g}: {c}"
        for g, c in sorted(gen_counts.items())
        if g > 0
    )

    # Main-path labels: first-author surname + year if available
    mp_labels = []
    for nid in main_path_ids:
        rec = all_papers.get(nid, {})
        mp_labels.append(_make_node_label(rec, nid))

    # ── Degeneracy guard ────────────────────────────────────────────
    # Detect and report a recency-dominated or collapsed walk so the
    # caller is never silently misled.
    _gen1_count  = gen_counts.get(1, 0)
    _gen2_count  = gen_counts.get(2, 0)
    _gen1_capped = (generations >= 2 and _gen1_count >= max_per_node)
    _path_short  = len(main_path_ids) < 3
    # "Recency fringe" heuristic: if ALL of the top-5 cited papers in
    # gen-1 are from the last 2 years and have < 20 citations each.
    import datetime as _dt
    _current_year = _dt.datetime.now().year
    _gen1_papers  = [r for r in non_seed if r['generation'] == 1]
    _top5_gen1    = sorted(
        _gen1_papers,
        key=lambda r: int(r.get('cited_by_count') or 0),
        reverse=True,
    )[:5]
    _recency_fringe = bool(
        _top5_gen1
        and all(
            int(r.get('year') or 0) >= _current_year - 1
            and int(r.get('cited_by_count') or 0) < 20
            for r in _top5_gen1
        )
    )
    _degenerate = (
        direction == 'forward'
        and (
            (_gen1_capped and _gen2_count == 0)
            or _path_short
            or _recency_fringe
        )
    )

    # SPC completeness note
    _spc_complete = bool(spc_edges)

    all_fetches_failed = (
        fetch_attempts > 0
        and len(fetch_errors) == fetch_attempts
    )
    some_fetches_failed = fetch_errors and not all_fetches_failed

    if all_fetches_failed:
        # Don't report "no papers found" — report the real cause.
        first_err = fetch_errors[0][1]
        text = (
            f"Citation lineage ({direction}) for {seed_id} "
            f"({seed_paper.get('title', seed_id)!r}).\n"
            f"Walk FAILED: all {len(fetch_errors)} node fetch(es) returned API errors.\n"
            f"API calls: {api_calls}.\n"
            f"Server version: {SERVER_VERSION}\n"
            f"Error: {first_err}\n"
        )
        if len(fetch_errors) > 1:
            text += f"(and {len(fetch_errors) - 1} more failures)\n"
    else:
        text = (
            f"Citation lineage ({direction}) for {seed_id} "
            f"({seed_paper.get('title', seed_id)!r}).\n"
            f"Generations walked: {generations}. "
            f"Papers per generation: {gen_summary or 'none (no papers found)'}.\n"
            f"Total unique papers (excl. seed): {len(non_seed)}. "
            f"API calls: {api_calls}. Sort: {sort_arg}.\n"
            f"Server version: {SERVER_VERSION}\n"
            f"Corpus written to: {json_path}\n"
            f"Corpus (base64, UTF-8 JSON): {corpus_b64}\n"
        )
        if source == 'openalex':
            text += "Source: OpenAlex (node IDs are OpenAlex work IDs).\n"
        if not _spc_complete:
            text += (
                "SPC arc weights: NOT computed (no edges in lineage graph; "
                "main path is unweighted).\n"
            )
        if html_path:
            text += f"Interactive HTML: {html_path}\n"
        if png_path:
            text += f"PNG: {png_path}\n"
        if main_path_ids:
            text += (
                f"Main path ({len(main_path_ids)} nodes): "
                + " → ".join(mp_labels) + "\n"
            )
        elif mp_result.get('note'):
            text += f"Main path: {mp_result['note']}\n"

        if _degenerate:
            _reasons = []
            if _gen1_capped and _gen2_count == 0:
                _reasons.append(
                    f"gen-1 hit the max_per_node cap ({max_per_node}) "
                    f"and gen-2 did not expand"
                )
            if _path_short:
                _reasons.append(
                    f"main_path has only {len(main_path_ids)} node(s)"
                )
            if _recency_fringe:
                _reasons.append(
                    "top gen-1 papers are all recent (≤2 years old) "
                    "with low citation counts"
                )
            text += (
                f"\nWARNING: forward walk appears recency-dominated or collapsed "
                f"({'; '.join(_reasons)}). "
                f"Treat the path as a recent-citer sample, not a citation backbone. "
                f"Consider sort='citedby' or a higher min_citing threshold.\n"
            )

        text += "\nTop 10 most-cited papers in lineage:\n"
        for r in top10:
            text += (
                f"  [gen {r['generation']}] {r.get('title', r['scopus_id'])!r} "
                f"({r.get('year', '?')}, {r.get('venue', '?')}) "
                f"— {r.get('cited_by_count', '?')} citations\n"
            )
        if not top10:
            text += "  (no papers found)\n"

    if some_fetches_failed:
        text += (
            f"\nWarning: {len(fetch_errors)} node fetch(es) failed with API errors "
            f"(partial walk — results may be incomplete). "
            f"First error: {fetch_errors[0][1]}\n"
        )

    return [types.TextContent(type="text", text=text)]


HANDLERS = {
    'bibliographic_coupling': _bibliographic_coupling,
    'co_citation': _co_citation,
    'citation_lineage': _citation_lineage,
}
