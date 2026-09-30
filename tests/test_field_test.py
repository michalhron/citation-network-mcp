"""Regression tests for the 0.17.0 field test (organizing-vision network in
the Basket of Eight, 30 September 2026). Item numbers follow the report.

REF-view responses of the four papers that crashed citation_network are
recorded in tests/fixtures/ref_view (fetched live, reference lists complete
after the FULL-view recovery of item 4)."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scopus_mcp import completeness, jobs
from scopus_mcp import semantic_scholar as s2
from scopus_mcp.client import ScopusClient, bibliography_to_ref
from scopus_mcp.records import clean_references, reported_reference_total, scalar
from scopus_mcp.tools import corpus

FIXTURES = Path(__file__).parent / 'fixtures' / 'ref_view'
CRASHERS = {  # item 1: ce:doi arrives as a list of {'$': doi} nodes
    '84860686902': 'Flynn 2012, EJIS',
    '84922544679': 'Hoefnagel 2014, JSIS',
    '84923003895': 'Lyytinen 2015, ISJ',
    '85116360795': 'Mamonov 2021, JSIS',
}
SWANSON_1997 = '0031512927'


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fixture(sid):
    return json.loads((FIXTURES / f'{sid}.json').read_text())


def _call(name, args, client_mock=None, openalex_mock=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'openalex', openalex_mock or MagicMock()), \
         patch.object(server, 'client', client_mock or MagicMock()):
        return _run(server.handle_call_tool(name, args))[0].text


def _search(entries):
    return {'search-results': {'entry': [
        {'dc:identifier': f'SCOPUS_ID:{sid}', 'dc:title': title, 'dc:creator': creator,
         'prism:coverDate': date, 'prism:doi': doi, 'prism:publicationName': venue,
         'citedby-count': '1'}
        for sid, creator, date, doi, title, venue in entries]}}


def _refs(ids, reported=None):
    return {'abstracts-retrieval-response': {'references': {
        '@total-references': str(reported if reported is not None else len(ids)),
        'reference': [{'@id': str(i + 1), 'scopus-id': sid, 'title': f'R{sid}'}
                      for i, sid in enumerate(ids)]}}}


# ── P0 item 1: list-valued fields ─────────────────────────────────────────


def test_scalar_normalizes_every_field_shape():
    assert scalar([{'$': '10.2307/249008'}, {'$': '10.2307/249008'}]) == '10.2307/249008'
    assert scalar({'$': 'x'}) == 'x'
    assert scalar(['', None, 'y']) == 'y'
    assert scalar('  z ') == 'z'
    assert scalar([]) is None and scalar(None) is None


@pytest.mark.parametrize('sid', sorted(CRASHERS))
def test_crashing_records_parse(sid):
    data = _fixture(sid)
    refs = clean_references(data)
    assert len(refs) == reported_reference_total(data)
    assert all(r['doi'] is None or isinstance(r['doi'], str) for r in refs)
    listed = [r for r in data['abstracts-retrieval-response']['references']['reference']
              if isinstance(r.get('ce:doi'), list)]
    assert listed, 'fixture should still carry the list-valued DOI'
    by_pos = {r['position']: r for r in refs}
    assert by_pos[listed[0]['@id']]['doi'] == scalar(listed[0]['ce:doi']).lower()


def test_network_over_the_crashing_records(tmp_path):
    ids = [SWANSON_1997] + sorted(CRASHERS)
    client = MagicMock()
    client.search_all = AsyncMock(return_value=_search([
        (SWANSON_1997, 'Swanson E.B.', '1997-01-01', '10.1287/orsc.8.5.458', 'OV', 'Organization Science'),
        ('84860686902', 'Flynn D.', '2012-01-01', '10.1057/ejis.2011.27', 'F', 'European Journal of Information Systems'),
        ('84922544679', 'Hoefnagel R.', '2014-01-01', '10.1016/j.jsis.2014.09.002', 'H', 'Journal of Strategic Information Systems'),
        ('84923003895', 'Lyytinen K.', '2015-01-01', '10.1111/isj.12044', 'L', 'Information Systems Journal'),
        ('85116360795', 'Mamonov S.', '2021-01-01', '10.1016/j.jsis.2021.101696', 'M', 'Journal of Strategic Information Systems'),
    ]))
    client.get_references = AsyncMock(side_effect=_fixture)
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('citation_network', {'ids': ids, 'check_completeness': False,
                                          'key_routes': 0, 'inline': 'nodes'}, client)
    assert text.startswith('Citation network (scopus): 5 papers')
    assert 'skipped' not in text and 'Error' not in text
    corpus_json = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert corpus_json['paper_errors'] == {} and corpus_json['reference_fetch_errors'] == {}
    # Flynn 2012 and Lyytinen 2015 cite Swanson & Ramiller (1997) in Scopus;
    # Hoefnagel 2014 and Mamonov 2021 cite other organizing-vision work.
    cited = {(e['citing'], e['cited']) for e in corpus_json['edges']}
    assert {('84860686902', SWANSON_1997), ('84923003895', SWANSON_1997)} <= cited
    assert all(n['refs_retrieved'] == n['refs_reported'] for n in corpus_json['nodes'])


# ── P0 item 2: one bad record never sinks the batch ───────────────────────


def test_bad_record_is_skipped_not_fatal(tmp_path):
    client = MagicMock()
    client.search_all = AsyncMock(return_value=_search([
        ('1', 'A X.', '1997-01-01', None, 'a', 'MIS Quarterly'),
        ('2', 'B X.', '2003-01-01', None, 'b', 'MIS Quarterly'),
        ('3', 'C X.', '2010-01-01', None, 'c', 'MIS Quarterly')]))
    client.get_references = AsyncMock(side_effect=lambda sid: _refs({'1': [], '2': ['1'], '3': ['2']}[sid]))
    real = corpus.clean_references

    def flaky(data):
        refs = real(data)
        if refs and refs[0]['scopus_id'] == '2':
            raise TypeError("'list' object has no attribute 'lower'")
        return refs
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(corpus, 'clean_references', side_effect=flaky):
        text = _call('citation_network', {'ids': ['1', '2', '3'], 'check_completeness': False,
                                          'inline': 'summary'}, client)
    lines = text.splitlines()
    assert lines[1] == ("Papers skipped after errors (1): 3 (TypeError: 'list' object has "
                        "no attribute 'lower')")
    assert lines[2].startswith('PROVISIONAL: main path computed with 1 of 3 reference lists missing')
    corpus_json = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert corpus_json['paper_errors'] == {'3': "TypeError: 'list' object has no attribute 'lower'"}
    assert corpus_json['edges'] == [{'citing': '2', 'cited': '1', 'spc': 1}]


def test_verification_failure_is_its_own_bucket(tmp_path):
    client = MagicMock()
    client.get_abstract = AsyncMock(return_value={'abstracts-retrieval-response': {
        'coredata': {'dc:title': 'OV', 'prism:coverDate': '1997-01-01'}}})
    client.search_all = AsyncMock(return_value=_search([
        ('10', 'A X.', '2001-01-01', None, 'a', 'MIS Quarterly'),
        ('11', 'B X.', '2002-01-01', None, 'b', 'MIS Quarterly')]))

    async def refs(sid):
        if sid == '11':
            raise TypeError('boom')
        return _refs(['31512927'])
    client.get_references = AsyncMock(side_effect=refs)
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('resolve_citers', {'seed_ids': ['31512927']}, client)
    assert 'Verification failed for 1 hit(s), counted as neither confirmed nor unconfirmed: 11 (TypeError: boom)' in text
    assert '  REF(2-s2.0-31512927) | 2 | 1 | 0 | 1 | 0' in text
    rows = {r['id']: r for r in map(json.loads, (ln for ln in text.splitlines() if ln.startswith('{')))}
    assert rows['10']['status'] == 'confirmed' and rows['10']['cites'] == ['31512927']
    assert rows['11']['status'] == 'verification_failed'


# ── P1 item 3: rate limits ───────────────────────────────────────────────

_REQ = httpx.Request('GET', 'https://api.elsevier.com/content/abstract/scopus_id/1')


def _client():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}), \
         patch('scopus_mcp.client.CacheManager') as cache:
        cache.return_value.get.return_value = None
        return ScopusClient()


def test_429_backs_off_and_recovers():
    client = _client()
    responses = [httpx.Response(429, request=_REQ)] * 3 + [
        httpx.Response(200, json={'ok': 1}, request=_REQ)]
    with patch('scopus_mcp.client.httpx.AsyncClient.request', new=AsyncMock(side_effect=responses)), \
         patch('asyncio.sleep', new_callable=AsyncMock) as sleep:
        assert _run(client._request('GET', 'x', {'a': 1})) == {'ok': 1}
    delays = [c.args[0] for c in sleep.await_args_list]
    assert len(delays) == 3 and all(0 < d <= 30 for d in delays)
    assert 1.0 <= delays[0] <= 2.0 and 4.0 <= delays[2] <= 8.0   # 2^n * [0.5, 1]


def test_429_honours_reset_header_and_reports_missing_headers():
    client = _client()
    import time
    reset = str(int(time.time()) + 7)
    responses = [httpx.Response(429, headers={'X-RateLimit-Reset': reset}, request=_REQ)] + \
        [httpx.Response(429, request=_REQ)] * 6
    with patch('scopus_mcp.client.httpx.AsyncClient.request', new=AsyncMock(side_effect=responses)), \
         patch('asyncio.sleep', new_callable=AsyncMock) as sleep:
        with pytest.raises(Exception) as err:
            _run(client._request('GET', 'x'))
    assert 5 <= sleep.await_args_list[0].args[0] <= 9
    assert len(sleep.await_args_list) == 5            # SCOPUS_RATE_LIMIT_RETRIES default
    assert 'after 5 retries. Quota headers: quota headers not returned' in str(err.value)


def test_429_with_spent_quota_fails_at_once():
    client = _client()
    import time
    headers = {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': str(int(time.time()) + 86400)}
    with patch('scopus_mcp.client.httpx.AsyncClient.request',
               new=AsyncMock(return_value=httpx.Response(429, headers=headers, request=_REQ))), \
         patch('asyncio.sleep', new_callable=AsyncMock) as sleep:
        with pytest.raises(Exception, match='quota exhausted'):
            _run(client._request('GET', 'x'))
    sleep.assert_not_awaited()


def test_rate_limited_lists_get_a_final_pass(tmp_path):
    client = MagicMock()
    client.search_all = AsyncMock(return_value=_search([
        ('1', 'A X.', '1997-01-01', None, 'a', 'J'), ('2', 'B X.', '2003-01-01', None, 'b', 'J')]))
    calls = {'2': 0}

    async def refs(sid):
        if sid == '2':
            calls['2'] += 1
            if calls['2'] == 1:
                raise Exception("Rate limit exceeded (429) after 5 retries.")
            return _refs(['1'])
        return _refs([])
    client.get_references = AsyncMock(side_effect=refs)
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(corpus, 'RETRY_PAUSE', 0):
        text = _call('citation_network', {'ids': ['1', '2'], 'check_completeness': False}, client)
    assert calls['2'] == 2
    assert 'PROVISIONAL' not in text and 'failed to load' not in text
    assert '2 1 1' in text


def test_still_missing_after_final_pass_marks_path_provisional(tmp_path):
    client = MagicMock()
    client.search_all = AsyncMock(return_value=_search([
        ('1', 'A X.', '1997-01-01', None, 'a', 'J'), ('2', 'B X.', '2003-01-01', None, 'b', 'J')]))

    async def refs(sid):
        if sid == '2':
            raise Exception("Rate limit exceeded (429) after 5 retries. Quota headers: quota headers not returned")
        return _refs([])
    client.get_references = AsyncMock(side_effect=refs)
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(corpus, 'RETRY_PAUSE', 0):
        text = _call('citation_network', {'ids': ['1', '2'], 'check_completeness': False}, client)
    assert 'Reference lists that failed to load (1): 2 (Exception: Rate limit exceeded (429)' in text
    assert 'PROVISIONAL: main path computed with 1 of 2 reference lists missing; results may change.' in text


# ── P1 item 4: the reference the REF view never serves ────────────────────

RANGE_400 = Exception("Scopus API error 400: 'startref' or 'refcount' parameter missing or invalid")


def _full_view(n):
    return {'abstracts-retrieval-response': {'item': {'bibrecord': {'tail': {'bibliography': {
        '@refcount': str(n), 'reference': [
            {'@id': str(i), 'ref-info': {
                'ref-title': {'ref-titletext': f'T{i}'}, 'ref-sourcetitle': 'Annual Review of Sociology',
                'ref-publicationyear': {'@first': '1987'},
                'refd-itemidlist': {'itemid': [{'$': f'id{i}', '@idtype': 'SGR'},
                                               {'$': f'10.1/{i}', '@idtype': 'DOI'}]},
                'ref-authors': {'author': [{'ce:indexed-name': 'Zucker L.G.', 'ce:surname': 'Zucker'}]}}}
            for i in range(1, n + 1)]}}}}}}


def _ref_side_effect(total, full=True):
    def side_effect(method, endpoint, params=None, **kw):
        if params.get('view') == 'FULL':
            if not full:
                raise Exception('Authentication failed')
            return _full_view(total)
        start = int(params.get('startref', 1))
        count = int(params.get('refcount', 40))
        if start - 1 + count > total - 1:            # the last entry is never served
            if 'startref' in params:
                raise RANGE_400
            count = total - 1 - (start - 1)
        return {'abstracts-retrieval-response': {'references': {
            '@total-references': str(total),
            'reference': [{'@id': str(i), 'scopus-id': f'id{i}'} for i in range(start, start + count)]}}}
    return side_effect


def test_last_reference_recovered_from_full_view():
    client = _client()
    with patch.object(client, '_request', new=AsyncMock(side_effect=_ref_side_effect(73))):
        data = _run(client.get_references('31512927'))
    refs = clean_references(data)
    assert len(refs) == 73
    assert refs[-1] == {'position': '73', 'title': 'T73', 'authors': ['Zucker L.G.'],
                        'source': 'Annual Review of Sociology', 'year': '1987',
                        'scopus_id': 'id73', 'doi': '10.1/73', 'fulltext': None,
                        'recovered_from': 'FULL'}
    assert data['abstracts-retrieval-response']['references']['@unservable'] == 0


def test_unrecoverable_reference_is_reported_as_unparseable(tmp_path):
    client = _client()
    with patch.object(client, '_request', new=AsyncMock(side_effect=_ref_side_effect(73, full=False))):
        data = _run(client.get_references('31512927'))
    assert len(clean_references(data)) == 72
    assert data['abstracts-retrieval-response']['references']['@unservable'] == 1
    mock = MagicMock()
    mock.search_all = AsyncMock(return_value=_search([('31512927', 'S E.', '1997-01-01', None, 't', 'J')]))
    mock.get_references = AsyncMock(return_value=data)
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('citation_network', {'ids': ['31512927'], 'check_completeness': False,
                                          'inline': 'nodes'}, mock)
    assert "refs_unparseable: 1 reference(s) Scopus counts but serves in neither view" in text
    assert '31512927 | S 1997 | J | refs 72/73' in text


def test_bibliography_entry_reshaped_like_ref_view():
    entry = _full_view(1)['abstracts-retrieval-response']['item']['bibrecord']['tail']['bibliography']['reference'][0]
    ref = bibliography_to_ref(entry)
    assert ref['scopus-id'] == 'id1' and ref['ce:doi'] == '10.1/1'
    assert ref['prism:coverDate'] == '1987-01-01' and ref['@recovered'] == 'FULL'


# ── P1 item 5: comparison counts beyond Crossref ─────────────────────────


def test_external_counts_fall_back_in_order():
    completeness._CACHE.clear()
    oa = MagicMock()
    oa.reference_counts = AsyncMock(return_value={'10.25300/misq/2015/39.3.04': 71})
    with patch.object(completeness, 'crossref_reference_counts',
                      new=AsyncMock(return_value={'10.1/a': 40, '10.25300/misq/2015/39.3.04': None,
                                                  '10.2307/x': None})), \
         patch.object(completeness, 'semantic_scholar_reference_counts',
                      new=AsyncMock(return_value={'10.2307/x': 55})) as s2counts:
        out = _run(completeness.external_reference_counts(
            ['10.1/A', '10.25300/MISQ/2015/39.3.04', '10.2307/x', '10.9/none'], openalex=oa))
    assert out == {'10.1/a': (40, 'crossref'),
                   '10.25300/misq/2015/39.3.04': (71, 'openalex'),
                   '10.2307/x': (55, 'semantic_scholar'),
                   '10.9/none': (None, None)}
    oa.reference_counts.assert_awaited_once_with(['10.25300/misq/2015/39.3.04', '10.2307/x', '10.9/none'])
    s2counts.assert_awaited_once_with(['10.2307/x', '10.9/none'])


def test_short_rule_is_explicit():
    assert completeness.assess(32, 71) == 'short'
    assert completeness.assess(66, 71) == 'ok'       # 93%: within the 90% ratio
    assert completeness.assess(10, 14) == 'ok'       # 71%, but only 4 missing
    assert '90%' in completeness.RULE_TEXT and '5 references' in completeness.RULE_TEXT


# ── P1 item 6: citers other indexes know and Scopus misses ────────────────


def test_cross_check_surfaces_miranda_2015(tmp_path):
    client = MagicMock()
    client.get_abstract = AsyncMock(return_value={'abstracts-retrieval-response': {
        'coredata': {'dc:title': 'The organizing vision in IS innovation',
                     'prism:coverDate': '1997-01-01', 'prism:doi': '10.1287/orsc.8.5.458'}}})

    async def search_all(query, max_results=200, sort=None):
        if query.startswith('(REF('):
            return _search([('10', 'Wang P.', '2001-01-01', '10.1/wang', 'W', 'MIS Quarterly')])
        assert query == 'DOI("10.25300/misq/2015/39.3.04")'
        return _search([('84947750305', 'Miranda S.M.', '2015-01-01', '10.25300/MISQ/2015/39.3.04',
                         'Jamming with social media', 'MIS Quarterly')])
    client.search_all = AsyncMock(side_effect=search_all)
    client.get_references = AsyncMock(side_effect=lambda sid: {
        '10': _refs(['31512927']), '84947750305': _refs([f'x{i}' for i in range(32)])}[sid])
    oa = MagicMock()
    oa.get_work = AsyncMock(return_value={'id': 'https://openalex.org/W1', 'doi': 'https://doi.org/10.1287/orsc.8.5.458'})
    miranda = {'id': 'https://openalex.org/W9', 'doi': 'https://doi.org/10.25300/misq/2015/39.3.04',
               'title': 'Jamming with social media', 'publication_year': 2015,
               'authorships': [{'author': {'display_name': 'Shaila M. Miranda'}}],
               'primary_location': {'source': {'display_name': 'MIS Quarterly'}}}
    wang = dict(miranda, id='https://openalex.org/W8', doi='https://doi.org/10.1/wang', title='W')
    oa.citing = AsyncMock(return_value=([miranda, wang], {}))
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(corpus, 'citing_papers', new=AsyncMock(return_value=({'paperId': 'P'}, [
             {'title': 'Jamming with Social Media', 'year': 2015,
              'externalIds': {'DOI': '10.25300/MISQ/2015/39.3.04'},
              'publicationVenue': {'name': 'MIS Quarterly', 'issn': '0276-7783'}},
             {'title': 'Out of scope', 'year': 2016, 'externalIds': {'DOI': '10.9/elsewhere'},
              'publicationVenue': {'name': 'Other', 'issn': '1234-5679'}}]))), \
         patch.object(corpus, 'external_reference_counts',
                      new=AsyncMock(return_value={'10.25300/misq/2015/39.3.04': (75, 'crossref')})):
        text = _call('resolve_citers', {'seed_ids': ['31512927'], 'scope': 'ais8'}, client, oa)
    assert 'Other indexes (OpenAlex, Semantic Scholar) list 1 in-scope citer(s)' in text
    assert ('  In Scopus, not returned by the searches, seed absent from its Scopus reference '
            'list (1): Miranda S.M. 2015 MISQ (Scopus holds 32 of ~75 refs, SHORT)') in text
    missing = json.loads(next(tmp_path.glob('*.json')).read_text())['missing_from_scopus']
    assert missing[0]['found_in'] == ['openalex', 'semantic_scholar']
    assert missing[0]['scopus_id'] == '84947750305' and missing[0]['seeds_in_scopus_refs'] == []


# ── P1 item 7: same pair, same answer, whatever the ID form ───────────────


def _s2_handler(request):
    path, params = request.url.path, request.url.params
    if path.endswith('DOI:10.2307/25148655'):
        return httpx.Response(404, json={'error': 'not found'})  # S2 files it under 10.5555
    if path.endswith('DOI:10.1080/07421222.2003.11045760'):
        return httpx.Response(200, json={'paperId': 'RS2003', 'title': 'Organizing Visions for IT',
                                         'year': 2003, 'authors': [{'name': 'Neil C. Ramiller'}]})
    if path.endswith('/paper/search/match'):
        assert 'Innovating Mindfully' in params['query']
        return httpx.Response(200, json={'data': [{'paperId': 'SR2004', 'year': 2004,
                                                   'title': 'Innovating Mindfully with Information Technology'}]})
    if path.endswith('SR2004/references'):
        return httpx.Response(200, json={'data': [{'citedPaper': {'paperId': 'RS2003'},
                                                   'contexts': [], 'intents': []}]})
    raise AssertionError(path)


def _real_s2(handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return real(*args, **kwargs)
    return patch.object(s2.httpx, 'AsyncClient', side_effect=factory)


def test_citation_context_same_status_for_doi_and_scopus_ids():
    client = MagicMock()
    client.get_abstract = AsyncMock(side_effect=lambda sid: {'abstracts-retrieval-response': {'coredata': {
        '9744280481': {'dc:title': 'Innovating Mindfully with Information Technology',
                       'prism:coverDate': '2004-01-01', 'prism:doi': '10.2307/25148655'},
        '0041928100': {'dc:title': 'Organizing Visions for IT', 'prism:coverDate': '2003-01-01',
                       'prism:doi': '10.1080/07421222.2003.11045760'}}[sid]}})
    oa = MagicMock()
    oa.get_work = AsyncMock(side_effect=lambda doi: {
        '10.2307/25148655': {'title': 'Innovating Mindfully with Information Technology1',
                             'publication_year': 2004, 'ids': {'mag': None}},
        '10.1080/07421222.2003.11045760': {'title': 'Organizing Visions for IT',
                                           'publication_year': 2003, 'ids': {}}}[doi])
    with _real_s2(_s2_handler):
        by_doi = _call('citation_context', {'citing': '10.2307/25148655', 'fulltext_fallback': False,
                                            'cited': '10.1080/07421222.2003.11045760'}, client, oa)
        by_scopus = _call('citation_context', {'citing': '9744280481', 'cited': '0041928100',
                                               'fulltext_fallback': False}, client, oa)
    for text in (by_doi, by_scopus):
        assert ': contexts_withheld; source: none; intents: none given' in text
        assert '[resolved: citing by title_match, cited by doi]' in text


def test_unresolved_and_absent_edges_are_told_apart():
    def handler(request):
        path = request.url.path
        if path.endswith('DOI:10.1/a'):
            return httpx.Response(200, json={'paperId': 'A', 'title': 'A', 'year': 2010})
        if path.endswith('DOI:10.1/b'):
            return httpx.Response(200, json={'paperId': 'B', 'title': 'B', 'year': 2000})
        if path.endswith('A/references'):
            return httpx.Response(200, json={'data': [{'citedPaper': {'paperId': 'Z'}}]})
        if path.endswith('B/citations'):
            return httpx.Response(200, json={'data': []})
        return httpx.Response(404, json={})
    with _real_s2(handler):
        out = _run(s2.citation_contexts([
            {'citing_ident': {'doi': '10.1/a'}, 'cited_ident': {'doi': '10.1/b'}},
            {'citing_ident': {'doi': '10.1/nope'}, 'cited_ident': {'doi': '10.1/b'}},
            {'citing_ident': {'doi': '10.1/a'}, 'cited_ident': {'doi': '10.1/nope'}}]))
    assert [p['context']['status'] for p in out] == [
        'edge_absent_in_s2', 'citing_paper_unresolved', 'cited_paper_unresolved']


# ── P2 item 8: background jobs ───────────────────────────────────────────


def test_slow_call_becomes_a_job():
    async def scenario():
        async def slow(arguments):
            jobs.progress('reference lists 3/10')
            await asyncio.sleep(0.2)
            return [MagicMock(text='network done')]
        with patch.dict(os.environ, {'SCOPUS_SYNC_BUDGET': '0.05'}):
            first = await jobs.run('citation_network', {}, slow)
        job_id = first[0].text.split('background as job ')[1].split(' ')[0]
        running = jobs.status_text(job_id)
        await asyncio.sleep(0.3)
        return first[0].text, running, jobs.status_text(job_id), jobs.result(job_id)[0].text
    first, running, finished, result = _run(scenario())
    assert 'still running after 0 s' in first and 'job_status(job_id=' in first
    assert 'running for' in running and 'reference lists 3/10' in running
    assert 'finished after' in finished
    assert result == 'network done'


def test_fast_call_is_answered_directly():
    async def fast(arguments):
        return ['done']
    assert _run(jobs.run('resolve_citers', {}, fast)) == ['done']


# ── P2 items 9, 10, 13: inline nodes, paging, key routes, duplicates ─────


def _big_network(tmp_path, n=150, **args):
    client = MagicMock()
    entries = [(str(i), f'Author{i} X.', f'{1990 + i % 35}-01-01', f'10.1/{i}', f'Title {i}',
                'MIS Quarterly') for i in range(n)]
    client.search_all = AsyncMock(side_effect=lambda q, max_results=25, sort=None: _search(
        [e for e in entries if f'EID(2-s2.0-{e[0]})' in q]))
    client.get_references = AsyncMock(side_effect=lambda sid: _refs(
        [str(j) for j in range(max(0, int(sid) - 3), int(sid))][-1:] * 3, reported=40))
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(corpus, 'external_reference_counts',
                      new=AsyncMock(return_value={f'10.1/{i}': (41, 'crossref') for i in range(n)})):
        return _call('citation_network', {'ids': [e[0] for e in entries], **args}, client)


def test_inline_nodes_is_compact(tmp_path):
    text = _big_network(tmp_path, inline='nodes')
    nodes_part = text[text.index('Nodes ('):text.index('Edges (')]
    assert len(nodes_part) < 25000
    node_lines = [ln for ln in text.splitlines() if ' | MISQ | ' in ln]
    assert len(node_lines) == 150
    assert node_lines[5] == '5 | Author5 1995 | MISQ | refs 3/40 | ext 41 crossref | short'


def test_inline_full_is_paged(tmp_path):
    text = _big_network(tmp_path, inline='full', page=2, page_size=60)
    assert 'Corpus JSON, page 2 of 3 (nodes 61-120 of 150; edges and paths on page 1); call again with page=3' in text
    payload = json.loads(text[text.index('{"nodes"'):])
    assert len(payload['nodes']) == 60 and 'edges' not in payload


def test_key_route_line_counts_distinct_routes(tmp_path):
    text = _big_network(tmp_path, n=12, key_routes=10, inline='summary')
    line = next(ln for ln in text.splitlines() if ln.startswith('Key routes ('))
    assert line.startswith('Key routes (SPC, local search): top 10 edges extend into ')
    assert 'distinct routes (' in line


def test_possible_duplicates():
    nodes = {'105026555771': {'title': 'The Organizing Vision of AI for Sustainability', 'year': '2024', 'doi': None},
             '105029252067': {'title': 'The organizing vision of AI for sustainability.', 'year': '2024', 'doi': None},
             '1': {'title': 'Other', 'year': '2024', 'doi': '10.1/x'},
             '2': {'title': 'Different', 'year': '2020', 'doi': '10.1/x'}}
    assert corpus.possible_duplicates(nodes) == [['105026555771', '105029252067'], ['1', '2']]


# ── P2 item 11: cleaned, ranked, bounded contexts ────────────────────────


def test_contexts_are_cleaned_ranked_and_bounded():
    contexts = [
        'MIS Quarterly Vol. 33 No. 4, pp. 647-662/December 2009 647',
        'The literature on IT adoption is large and growing across many fields.',
        'Discourses shift over time (Ramiller et al. 2008), as prior work shows.',
        'Organizing visions change as communities debate them (Ramiller et al. 2008).',
        'See also [36] for a related account of adoption waves in industry.',
        'Organizing visions change as communities debate them (Ramiller et al. 2008).',
    ]
    kept, total, dropped = s2.clean_contexts(contexts, ['Ramiller', 'Swanson'], 2008,
                                             terms=['organizing vision'], limit=2)
    assert total == 6 and dropped == 3
    assert kept == ['Organizing visions change as communities debate them (Ramiller et al. 2008).',
                    'Discourses shift over time (Ramiller et al. 2008), as prior work shows.']


# ── P3: off-network REF refusal ──────────────────────────────────────────


def test_ref_refusal_names_the_network():
    client = _client()
    resp = httpx.Response(401, headers={'X-ELS-Status': 'AUTHORIZATION_ERROR'}, request=_REQ)
    with patch('scopus_mcp.client.httpx.AsyncClient.request', new=AsyncMock(return_value=resp)):
        with pytest.raises(Exception) as err:
            _run(client._request('GET', 'content/abstract/scopus_id/1', {'view': 'REF'}))
    assert 'off the institutional network' in str(err.value)
    assert 'SCOPUS_INSTTOKEN' in str(err.value)
