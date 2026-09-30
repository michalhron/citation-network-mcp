"""Offline tests for the corpus and audit features: reference totals and
filters, completeness against Crossref, query-vs-entitlement error notes,
ISSN scoping, cycle breaking, key routes, Pajek export, citation_network,
resolve_citers and citation_context."""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scopus_mcp import semantic_scholar as s2
from scopus_mcp.baskets import resolve_scope, scope_scopus_query
from scopus_mcp.client import (
    ENTITLEMENT_NOTE,
    FIELD_RESTRICTION_NOTE,
    QUERY_SYNTAX_NOTE,
    ScopusClient,
)
from scopus_mcp.completeness import assess
from scopus_mcp.lineage import compute_main_path, earliest_date, key_route_paths, write_pajek


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _call(name, args, client_mock=None, openalex_mock=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'openalex', openalex_mock or MagicMock()), \
         patch.object(server, 'client', client_mock or MagicMock()):
        return _run(server.handle_call_tool(name, args))[0].text


def _ref_response(refs, reported=None):
    return {'abstracts-retrieval-response': {'references': {
        '@total-references': str(reported if reported is not None else len(refs)),
        'reference': [
            {'@id': str(i + 1), 'title': f'Ref {sid}', 'scopus-id': sid,
             'ce:doi': doi, 'prism:coverDate': '2000-01-01'}
            for i, (sid, doi) in enumerate(refs)
        ]}}}


def _search_response(entries):
    return {'search-results': {'entry': [
        {'dc:identifier': f'SCOPUS_ID:{sid}', 'dc:title': f'Paper {sid}',
         'dc:creator': creator, 'prism:coverDate': date, 'prism:doi': doi,
         'prism:publicationName': 'MIS Quarterly', 'prism:issn': '02767783',
         'citedby-count': '3'}
        for sid, creator, date, doi in entries
    ]}}


# ── baskets ──────────────────────────────────────────────────────────────


def test_scope_basket_names_and_issn_lists():
    ais8 = resolve_scope('ais8')
    assert '0276-7783' in ais8 and '1557-928X' in ais8 and len(ais8) == 16
    assert '1558-3457' in ais8  # JAIS's second ISSN in Scopus
    assert resolve_scope('Basket of Eight') == ais8
    assert resolve_scope(['02767783', '1047-7047']) == ['0276-7783', '1047-7047']
    assert resolve_scope('0276-7783, 1047-7047') == ['0276-7783', '1047-7047']
    assert resolve_scope(None) is None
    with pytest.raises(ValueError, match='Known baskets'):
        resolve_scope('premier journals')


def test_scope_query_uses_issns():
    q = scope_scopus_query('REF(2-s2.0-1)', ['0276-7783', '1047-7047'])
    assert q == '(REF(2-s2.0-1)) AND (ISSN(02767783) OR ISSN(10477047))'
    assert scope_scopus_query('x', None) == 'x'


# ── error notes ──────────────────────────────────────────────────────────

_REQ = httpx.Request('GET', 'https://api.elsevier.com/content/search/scopus')
TRANSLATING = {'service-error': {'status': {'statusCode': 'INVALID_INPUT',
                                            'statusText': 'Error translating query'}}}
RESTRICTED = {'service-error': {'status': {
    'statusCode': 'INVALID_INPUT',
    'statusText': 'Use of certain field restrictions in the search query is not '
                  'allowed for this requestor.'}}}


def _client():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}), \
         patch('scopus_mcp.client.CacheManager') as cache:
        cache.return_value.get.return_value = None
        return ScopusClient()


def test_translation_error_with_working_search_blames_the_query():
    client = _client()

    async def respond(method, url, params=None, **kw):
        if params and params.get('query') == 'ALL(gene)':
            return httpx.Response(200, json={'search-results': {'entry': []}}, request=_REQ)
        return httpx.Response(400, json=TRANSLATING, request=_REQ)
    with patch('scopus_mcp.client.httpx.AsyncClient.request', new=AsyncMock(side_effect=respond)):
        with pytest.raises(Exception) as err:
            _run(client.search_scopus('REFPUBYEAR(1997)', count=1))
        # The verdict is kept for the session: no second probe.
        with pytest.raises(Exception):
            _run(client.search_scopus('REFPUBYEAR(1998)', count=1))
    assert QUERY_SYNTAX_NOTE in str(err.value)
    assert ENTITLEMENT_NOTE not in str(err.value)
    assert client._search_entitled is True


def test_translation_error_with_failing_search_blames_entitlement():
    client = _client()
    with patch('scopus_mcp.client.httpx.AsyncClient.request',
               new=AsyncMock(return_value=httpx.Response(400, json=TRANSLATING, request=_REQ))):
        with pytest.raises(Exception) as err:
            _run(client.search_scopus('TITLE(x)', count=1))
    assert ENTITLEMENT_NOTE in str(err.value)
    assert client._search_entitled is False


def test_field_restriction_gets_its_own_note():
    client = _client()
    with patch('scopus_mcp.client.httpx.AsyncClient.request',
               new=AsyncMock(return_value=httpx.Response(400, json=RESTRICTED, request=_REQ))):
        with pytest.raises(Exception) as err:
            _run(client.search_scopus('REFEID(2-s2.0-1)', count=1))
    assert FIELD_RESTRICTION_NOTE in str(err.value)
    assert ENTITLEMENT_NOTE not in str(err.value)


# ── get_references ───────────────────────────────────────────────────────


def _scopus_with_refs(refs_by_id, reported=None):
    m = MagicMock()
    m.get_references = AsyncMock(side_effect=lambda sid: _ref_response(
        refs_by_id[sid], reported))
    return m


def test_get_references_reports_truncation():
    refs = [(str(i), None) for i in range(257)]
    text = _call('get_references', {'scopus_id': '9', 'count': 200},
                 _scopus_with_refs({'9': refs}))
    assert text.startswith('Returned 200 of 257 references (truncated: true; '
                           'raise count above 200 for the rest).')


def test_get_references_reports_unservable_references():
    text = _call('get_references', {'scopus_id': '9', 'count': 50},
                 _scopus_with_refs({'9': [('1', None), ('2', None)]}, reported=3))
    assert 'Returned 2 of 2 references (truncated: false).' in text
    assert 'Scopus reports 3 references but serves 2' in text


def test_get_references_filter_ids_matches_ids_eids_and_dois():
    refs = [('11', None), ('12', '10.1/ABC'), ('13', None), ('14', None)]
    text = _call('get_references',
                 {'scopus_id': '9', 'filter_ids': ['2-s2.0-11', 'https://doi.org/10.1/abc', '99']},
                 _scopus_with_refs({'9': refs}))
    assert text.startswith("2 of the document's 4 references match filter_ids (3 IDs).")
    assert "'scopus_id': '11'" in text and "'scopus_id': '12'" in text
    assert "'scopus_id': '13'" not in text


def test_get_references_flags_short_list():
    m = _scopus_with_refs({'9': [(str(i), None) for i in range(32)]})
    m.get_abstract = AsyncMock(return_value={'abstracts-retrieval-response': {
        'coredata': {'prism:doi': '10.1/miranda'}}})
    with patch('scopus_mcp.tools.citations.external_reference_counts',
               new=AsyncMock(return_value={'10.1/miranda': (95, 'crossref')})):
        text = _call('get_references', {'scopus_id': '9', 'check_completeness': True}, m)
    assert 'Completeness: SHORT. crossref lists 95 references; this list has 32' in text


def test_assess_thresholds():
    assert assess(32, 95) == 'short'
    assert assess(90, 95) == 'ok'       # within 20%
    assert assess(2, 5) == 'ok'         # fewer than 5 missing
    assert assess(10, None) == 'unknown'
    assert assess(None, 40) == 'unknown'


# ── search_all inline ────────────────────────────────────────────────────


def test_search_all_compact_inline_returns_every_record(tmp_path):
    entries = [(str(i), 'Swanson E.B.', '1997-01-01', f'10.1/{i}') for i in range(60)]
    m = MagicMock()
    m.search_all = AsyncMock(return_value={**_search_response(entries),
                                           '_meta': {'total_available': 60, 'total_fetched': 60}})
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('search_all', {'query': 'x', 'inline': 'compact', 'scope': 'ais8'}, m)
    lines = [ln for ln in text.splitlines() if ln.startswith('{')]
    assert len(lines) == 60
    assert json.loads(lines[0]) == {'scopus_id': '0', 'doi': '10.1/0', 'year': '1997',
                                    'creator': 'Swanson E.B.', 'title': 'Paper 0',
                                    'publication_name': 'MIS Quarterly',
                                    'issn': '02767783', 'cited_by_count': '3'}
    query = m.search_all.await_args.args[0]
    assert query.startswith('(x) AND (ISSN(0960085X) OR ')


# ── main path: cycles, key routes, Pajek ─────────────────────────────────


def test_earliest_date_prefers_online_date():
    assert earliest_date({'cover_date': '2004-01-01', 'publication_date': '2002-11-01'}) == '2002-11-01'
    assert earliest_date({'year': '2010'}) == '2010-12-31'
    assert earliest_date({}) == ''


def test_cycle_is_broken_against_publication_order_without_dropping_papers():
    recs = [
        {'scopus_id': 'A', 'cover_date': '1997-01-01', 'parents': []},
        {'scopus_id': 'B', 'cover_date': '2003-06-01', 'parents': ['A', 'C']},
        # Online-first: cited by B although its issue is dated later.
        {'scopus_id': 'C', 'cover_date': '2004-01-01', 'publication_date': '2002-11-01',
         'parents': ['A', 'B']},
        {'scopus_id': 'D', 'year': '2010', 'parents': ['B', 'C']},
    ]
    mp = compute_main_path(recs)
    assert [(e['source'], e['target']) for e in mp['removed_cycle_edges']] == [('B', 'C')]
    nodes = {e['source'] for e in mp['edges']} | {e['target'] for e in mp['edges']}
    assert nodes == {'A', 'B', 'C', 'D'}
    assert mp['global_main_path'][0] == 'A' and mp['global_main_path'][-1] == 'D'


def test_key_routes_merge_identical_routes():
    edges = [{'source': 'A', 'target': 'B', 'spc_weight': 5},
             {'source': 'B', 'target': 'C', 'spc_weight': 4},
             {'source': 'A', 'target': 'D', 'spc_weight': 1}]
    routes = key_route_paths(edges, k=3)
    assert routes['routes'][0]['path'] == ['A', 'B', 'C']
    assert routes['routes'][0]['key_edges'] == [['A', 'B'], ['B', 'C']]
    assert routes['routes'][1]['path'] == ['A', 'D']
    assert routes['nodes'] == ['A', 'B', 'C', 'D']


def test_pajek_format(tmp_path):
    path = write_pajek([{'id': 'x', 'label': 'Swanson "1997"'}, {'id': 'y'}],
                       [{'source': 'x', 'target': 'y', 'weight': 3},
                        {'source': 'x', 'target': 'zz'}], tmp_path / 'n.net')
    assert open(path).read() == "*Vertices 2\n1 \"Swanson '1997'\"\n2 \"y\"\n*Arcs\n1 2 3\n"


# ── citation_network ─────────────────────────────────────────────────────


def test_citation_network_scopus_ids(tmp_path):
    client = MagicMock()
    client.search_all = AsyncMock(return_value=_search_response([
        ('1', 'Swanson E.B.', '1997-01-01', '10.1/a'),
        ('2', 'Ramiller N.C.', '2003-01-01', '10.1/b'),
        ('3', 'Wang P.', '2010-01-01', '10.1/c'),
    ]))
    refs = {'1': [('900', None)],
            '2': [('1', None), ('901', None)],
            '3': [('2', None), ('777', '10.1/A')]}  # cites 1 by DOI only
    client.get_references = AsyncMock(side_effect=lambda sid: _ref_response(refs[sid]))
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch('scopus_mcp.tools.corpus.external_reference_counts',
               new=AsyncMock(return_value={'10.1/a': (1, 'crossref'), '10.1/b': (2, 'crossref'),
                                           '10.1/c': (40, 'openalex')})):
        text = _call('citation_network', {'ids': ['1', '2-s2.0-2', '3']}, client)
    assert 'Citation network (scopus): 3 papers, 3 within-set citation edges, 0 papers with no edge.' in text
    assert ': 1 short, 2 ok, 0 unknown. Comparison counts from crossref 2, openalex 1.' in text
    assert 'SHORT 3 Wang 2010: 2 references retrieved, openalex lists 40.' in text
    assert 'Main path: 3 papers, 2 ok, 1 short, 0 unknown.' in text
    assert 'Main path (global): Swanson 1997 → Ramiller 2003 → Wang 2010' in text
    assert '3 2 ' in text and '2 1 ' in text
    query = client.search_all.await_args.args[0]
    assert query == 'EID(2-s2.0-1) OR EID(2-s2.0-2) OR EID(2-s2.0-3)'
    net = next(tmp_path.glob('*.net')).read_text()
    assert net.startswith('*Vertices 3\n1 "Swanson 1997"')
    corpus = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert {(e['citing'], e['cited']) for e in corpus['edges']} == {('2', '1'), ('3', '2'), ('3', '1')}


def test_citation_network_needs_ids_or_query():
    assert 'Give either ids or query.' in _call('citation_network', {})


# ── resolve_citers ───────────────────────────────────────────────────────


def test_resolve_citers_merges_strategies_and_verifies(tmp_path):
    client = MagicMock()
    client.get_abstract = AsyncMock(return_value={'abstracts-retrieval-response': {
        'coredata': {'prism:doi': '10.1287/orsc.8.5.458', 'dc:title': 'OV',
                     'prism:coverDate': '1997-01-01'}}})

    async def search_all(query, max_results=200, sort=None):
        if query.startswith('REF('):
            return _search_response([('10', 'A', '2001-01-01', None),
                                     ('11', 'B', '2002-01-01', None)])
        return _search_response([('11', 'B', '2002-01-01', None),
                                 ('12', 'C', '2005-01-01', None),
                                 ('13', 'D', '2006-01-01', None)])
    client.search_all = AsyncMock(side_effect=search_all)
    refs = {'10': [('31512927', None)], '11': [('31512927', None)],
            '12': [('55', '10.1287/ORSC.8.5.458')],   # cites the seed by DOI only
            '13': [('56', None)]}                     # false positive
    client.get_references = AsyncMock(side_effect=lambda sid: _ref_response(refs[sid]))
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('resolve_citers', {'seed_ids': ['31512927'],
                                        'queries': ['REFTITLE("organizing vision")']}, client)
    assert '4 distinct hits across 2 strategies' in text
    assert '  REF(2-s2.0-31512927) | 2 | 2 | 0 | 0 | 1' in text
    assert '  REFTITLE("organizing vision") | 3 | 2 | 1 | 0 | 1' in text
    assert "seed is in the hit's own reference list): 3. Unconfirmed: 1" in text
    rows = [json.loads(ln) for ln in text.splitlines() if ln.startswith('{')]
    assert {r['id']: r['status'] for r in rows} == {
        '10': 'confirmed', '11': 'confirmed', '12': 'confirmed', '13': 'unconfirmed'}
    assert rows[2]['cites'] == ['31512927']


# ── citation_context ─────────────────────────────────────────────────────


def _s2_transport(handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return real(*args, **kwargs)
    return patch.object(s2.httpx, 'AsyncClient', side_effect=factory)


def _s2_papers(papers):
    """Handler piece: DOI lookups for {doi: paperId}."""
    def lookup(path):
        for doi, pid in papers.items():
            if path.endswith(f'DOI:{doi}'):
                return httpx.Response(200, json={'paperId': pid, 'title': pid, 'year': 2010,
                                                 'authors': [{'name': 'Peiyu Wang'}]})
        return None
    return lookup


def test_contexts_fall_back_to_cited_paper_when_references_are_elided():
    lookup = _s2_papers({'10.1/km': 'KM', '10.2/wang': 'WANG'})

    def handler(request):
        path = request.url.path
        found = lookup(path)
        if found:
            return found
        if path.endswith('KM/references'):
            return httpx.Response(200, json={'data': None, 'citingPaperInfo': {}})
        assert path.endswith('WANG/citations')
        return httpx.Response(200, json={'data': [
            {'citingPaper': {'paperId': 'OTHER'}, 'contexts': ['x'], 'intents': []},
            {'citingPaper': {'paperId': 'KM'},
             'contexts': ['inoculation against following IT fashions (Wang, 2010).'],
             'intents': ['background'], 'isInfluential': False},
        ]})
    with _s2_transport(handler):
        out = _run(s2.citation_contexts([{'citing_ident': {'doi': '10.1/km'},
                                          'cited_ident': {'doi': '10.2/wang'}}]))
    ctx = out[0]['context']
    assert ctx['status'] == 'found' and ctx['via'] == 'cited paper citations'
    assert ctx['intents'] == ['background']
    assert ctx['contexts'] == ['inoculation against following IT fashions (Wang, 2010).']
    assert ctx['citing_route'] == 'doi' and ctx['cited_route'] == 'doi'


def test_citation_context_tool_formats_pairs():
    async def fake(pairs, terms, max_contexts):
        return [dict(p, context={'status': 'found', 'intents': ['background'],
                                 'is_influential': False, 'n_contexts': 1, 'n_contexts_total': 2,
                                 'contexts': ['as argued (Wang, 2010)'], 'via': 'cited paper citations',
                                 'citing_route': 'doi', 'cited_route': 'title_match'})
                for p in pairs]
    oa = MagicMock()
    oa.get_work = AsyncMock(return_value=None)
    with patch('scopus_mcp.tools.corpus.citation_contexts', new=fake):
        text = _call('citation_context', {'citing': '10.1/km', 'cited': '10.2/wang'}, openalex_mock=oa)
    assert '10.1/km → 10.2/wang: found; source: semantic_scholar; intents: background; influential: false' in text
    assert '[resolved: citing by doi, cited by title_match]' in text
    assert '“as argued (Wang, 2010)”' in text
