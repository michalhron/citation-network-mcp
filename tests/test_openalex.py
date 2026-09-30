"""Offline tests for the OpenAlex backend and its wiring into the tools.

HTTP goes through httpx.MockTransport, so request building, paging, caching
and error handling run for real without the network.
"""
import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from scopus_mcp import openalex as oa
from scopus_mcp.openalex import (
    OpenAlexClient,
    OpenAlexError,
    clean_openalex_work,
    indexed_name,
    openalex_work_key,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _work(wid, title='T', refs=(), year=2020, author='Norman P. Hummon', doi=None):
    return {
        'id': f'https://openalex.org/{wid}',
        'doi': f'https://doi.org/{doi}' if doi else None,
        'title': title,
        'publication_year': year,
        'publication_date': f'{year}-01-01',
        'cited_by_count': 5,
        'type': 'article',
        'primary_location': {'source': {'display_name': 'Social Networks'}},
        'authorships': [{'author': {'display_name': author}}],
        'referenced_works': [f'https://openalex.org/{r}' for r in refs],
    }


def _client(handler, key=None):
    """OpenAlexClient on a mock transport with a no-op cache."""
    cache = MagicMock()
    cache.get.return_value = None
    env = {'OPENALEX_API_KEY': key} if key else {}
    with patch.dict(os.environ, env):
        c = OpenAlexClient(cache=cache)
    headers = dict(c.client.headers)
    c.client = httpx.AsyncClient(
        base_url=oa.BASE_URL, headers=headers, transport=httpx.MockTransport(handler),
    )
    return c


def _params(request):
    return {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}


# ---------------------------------------------------------------------------
# Identifiers and record shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('value,expected', [
    ('W2155046806', 'W2155046806'),
    ('w2155046806', 'W2155046806'),
    ('https://openalex.org/W2155046806', 'W2155046806'),
    ('10.1016/0378-8733(89)90017-8', 'doi:10.1016/0378-8733(89)90017-8'),
    ('https://doi.org/10.1016/X', 'doi:10.1016/x'),
    ('doi:10.1016/X', 'doi:10.1016/x'),
    ('85007305299', None),
    ('2-s2.0-85007305299', None),
])
def test_openalex_work_key(value, expected):
    assert openalex_work_key(value) == expected


@pytest.mark.parametrize('name,expected', [
    ('Norman P. Hummon', 'Hummon N.P.'),
    ('Batagelj', 'Batagelj'),
    ('', None),
    (None, None),
])
def test_indexed_name_matches_scopus_creator_form(name, expected):
    assert indexed_name(name) == expected


def test_clean_openalex_work_shape():
    rec = clean_openalex_work(_work('W1', title='Paper', doi='10.1234/ABC'))
    assert rec == {
        'openalex_id': 'W1', 'title': 'Paper', 'creator': 'Hummon N.P.',
        'publication_name': 'Social Networks', 'cover_date': '2020-01-01',
        'year': '2020', 'doi': '10.1234/abc', 'cited_by_count': 5,
        'type': 'article', 'source': 'openalex',
    }


def test_clean_openalex_work_tolerates_missing_fields():
    rec = clean_openalex_work({'id': 'https://openalex.org/W9'})
    assert rec['openalex_id'] == 'W9'
    assert rec['creator'] is None and rec['publication_name'] is None


# ---------------------------------------------------------------------------
# Transport: key header, errors, budget
# ---------------------------------------------------------------------------

def test_key_sent_as_bearer_header_not_query():
    seen = {}

    def handler(request):
        seen['auth'] = request.headers.get('authorization')
        seen['params'] = _params(request)
        return httpx.Response(200, json=_work('W1'))

    c = _client(handler, key='secret-oa-key')
    _run(c.get_work('W1'))
    assert seen['auth'] == 'Bearer secret-oa-key'
    assert 'api_key' not in seen['params']
    _run(c.close())


def test_no_key_no_auth_header():
    seen = {}

    def handler(request):
        seen['auth'] = request.headers.get('authorization')
        return httpx.Response(200, json=_work('W1'))

    c = _client(handler)
    _run(c.get_work('W1'))
    assert seen['auth'] is None
    _run(c.close())


def test_404_returns_none():
    c = _client(lambda r: httpx.Response(404, json={}))
    assert _run(c.get_work('W404')) is None
    _run(c.close())


def test_401_names_the_key():
    c = _client(lambda r: httpx.Response(401, json={'error': 'Invalid or missing API key'}))
    with pytest.raises(OpenAlexError, match='OPENALEX_API_KEY'):
        _run(c.get_work('W1'))
    _run(c.close())


def test_exhausted_budget_fails_fast_with_key_hint():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={'x-ratelimit-remaining-usd': '0',
                                            'x-ratelimit-limit-usd': '0.1',
                                            'x-ratelimit-reset': '3600'})

    c = _client(handler)
    with pytest.raises(OpenAlexError, match='free OpenAlex account key'):
        _run(c.get_work('W1'))
    assert len(calls) == 1  # no retries once the budget is spent
    _run(c.close())


def test_transient_429_retries_then_succeeds():
    responses = [httpx.Response(429, headers={'x-ratelimit-remaining-usd': '0.05'}),
                 httpx.Response(200, json=_work('W1'))]
    c = _client(lambda r: responses.pop(0))
    with patch('scopus_mcp.openalex.asyncio.sleep', new_callable=AsyncMock):
        work = _run(c.get_work('W1'))
    assert work['id'].endswith('W1')
    _run(c.close())


def test_cache_hit_skips_network():
    c = _client(lambda r: pytest.fail('network used despite cache hit'))
    c.cache.get.return_value = _work('W7')
    assert _run(c.get_work('W7'))['id'].endswith('W7')
    _run(c.close())


def test_get_work_rejects_scopus_ids():
    c = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match='not an OpenAlex work ID'):
        _run(c.get_work('85007305299'))
    _run(c.close())


# ---------------------------------------------------------------------------
# Paging, search, citing, references, counts
# ---------------------------------------------------------------------------

def test_list_works_follows_cursor_and_caps():
    pages = {
        '*': {'meta': {'count': 5, 'next_cursor': 'c2'},
              'results': [_work('W1'), _work('W2')]},
        'c2': {'meta': {'count': 5, 'next_cursor': 'c3'},
               'results': [_work('W3'), _work('W4')]},
    }
    seen = []

    def handler(request):
        params = _params(request)
        seen.append(params)
        return httpx.Response(200, json=pages[params['cursor']])

    c = _client(handler)
    works, meta = _run(c.list_works('cites:W9', max_results=3))
    assert [w['id'][-2:] for w in works] == ['W1', 'W2', 'W3']
    assert meta == {'total_available': 5, 'total_fetched': 3, 'truncated': True,
                    'pages_fetched': 2, 'source': 'openalex'}
    assert seen[1]['per-page'] == '1'  # second page only asks for what is left
    _run(c.close())


def test_search_uses_title_abstract_filter_without_commas():
    seen = {}

    def handler(request):
        seen.update(_params(request))
        return httpx.Response(200, json={'meta': {'count': 0, 'next_cursor': None},
                                         'results': []})

    c = _client(handler)
    _run(c.search('organizing vision, IT', max_results=10, sort='citedby'))
    assert seen['filter'] == 'title_and_abstract.search:organizing vision IT'
    assert seen['sort'] == 'cited_by_count:desc'
    _run(c.close())


def test_citing_filter():
    seen = {}

    def handler(request):
        seen.update(_params(request))
        return httpx.Response(200, json={'meta': {'count': 0}, 'results': []})

    c = _client(handler)
    _run(c.citing('W2155046806', max_results=5))
    assert seen['filter'] == 'cites:W2155046806'
    _run(c.close())


def test_references_batches_and_keeps_order():
    ids = [f'W{i}' for i in range(1, 121)]
    batches = []

    def handler(request):
        chunk = _params(request)['filter'].removeprefix('openalex:').split('|')
        batches.append(chunk)
        # Return in reverse, and drop W5, to prove reordering and gaps.
        return httpx.Response(200, json={'results': [
            _work(w) for w in reversed(chunk) if w != 'W5']})

    c = _client(handler)
    refs = _run(c.references(_work('W0', refs=ids)))
    assert [len(b) for b in batches] == [50, 50, 20]
    assert [w['id'].rsplit('/', 1)[-1] for w in refs] == [w for w in ids if w != 'W5']
    _run(c.close())


def test_yearly_counts_groups_and_sorts():
    seen = {}

    def handler(request):
        seen.update(_params(request))
        return httpx.Response(200, json={
            'meta': {'count': 30},
            'group_by': [{'key': '2021', 'count': 20}, {'key': '2019', 'count': 10},
                         {'key': 'unknown', 'count': 3}],
        })

    c = _client(handler)
    counts, total = _run(c.yearly_counts('"organizing vision"', 2015, 2025))
    assert counts == {2019: 10, 2021: 20}
    assert total == 30
    assert seen['group_by'] == 'publication_year'
    assert seen['filter'] == ('title_and_abstract.search:"organizing vision",'
                              'publication_year:2015-2025')
    _run(c.close())


# ---------------------------------------------------------------------------
# Tool wiring (server.handle_call_tool with source='openalex')
# ---------------------------------------------------------------------------

def _call(name, args, openalex_mock, client_mock=None):
    # Importing the server builds a ScopusClient, which needs a key.
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'openalex', openalex_mock), \
         patch.object(server, 'client', client_mock or MagicMock()):
        return _run(server.handle_call_tool(name, args))[0].text


def _oa_mock(works=None, citing=None):
    m = MagicMock()
    works = works or {}
    m.get_work = AsyncMock(side_effect=lambda ident: works.get(ident))
    m.citing = AsyncMock(side_effect=lambda wid, max_results=200, sort=None:
                         ((citing or {}).get(wid, []), {'total_available': 0}))
    return m


def test_unknown_source_rejected():
    text = _call('search_all', {'query': 'x', 'source': 'wos'}, _oa_mock())
    assert "source must be 'scopus' or 'openalex'" in text


def test_search_all_openalex(tmp_path):
    m = _oa_mock()
    m.search = AsyncMock(return_value=([_work('W1', title='Hit')],
                                       {'total_available': 1, 'total_fetched': 1}))
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('search_all', {'query': 'x', 'source': 'openalex'}, m)
    assert text.startswith('Source: OpenAlex (title and abstract search).')
    assert "'openalex_id': 'W1'" in text
    m.search.assert_awaited_once_with('x', max_results=200, sort='relevance')


def test_get_references_openalex_resolves_scopus_id_via_doi():
    seed = _work('W10', refs=['W11'], doi='10.1234/seed')
    m = _oa_mock(works={'10.1234/seed': seed})
    m.references = AsyncMock(return_value=[_work('W11', title='Ref')])
    scopus = MagicMock()
    scopus.get_abstract = AsyncMock(return_value={'abstracts-retrieval-response': {
        'coredata': {'prism:doi': '10.1234/seed', 'dc:title': 'Seed'}}})
    text = _call('get_references', {'scopus_id': '85000000001', 'source': 'openalex'},
                 m, client_mock=scopus)
    assert "'title': 'Ref'" in text
    scopus.get_abstract.assert_awaited_once()
    m.get_work.assert_awaited_once_with('10.1234/seed')


def test_get_citing_papers_openalex_by_doi():
    m = _oa_mock(works={'10.1234/seed': _work('W10')}, citing={'W10': [_work('W20')]})
    text = _call('get_citing_papers',
                 {'scopus_id': '10.1234/seed', 'source': 'openalex', 'count': 5}, m)
    assert "'openalex_id': 'W20'" in text


def test_bibliographic_coupling_openalex(tmp_path):
    works = {
        'W1': _work('W1', title='A', refs=['R1', 'R2', 'R3']),
        'W2': _work('W2', title='B', refs=['R2', 'R3', 'R4']),
    }
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('bibliographic_coupling',
                     {'seed_ids': ['W1', 'W2', 'W404'], 'min_shared': 1,
                      'source': 'openalex'}, _oa_mock(works=works))
    assert '2/3 seeds processed, 1 edges emitted' in text
    assert 'Source: OpenAlex' in text
    assert 'weight=2' in text
    assert "Skipped (no usable references or API error): [\"W404 (OpenAlex has no work" in text
    assert 'bibcoupling-openalex-3seeds' in text


def test_co_citation_openalex(tmp_path):
    works = {'W1': _work('W1', title='A'), 'W2': _work('W2', title='B')}
    citing = {'W1': [_work('C1'), _work('C2')], 'W2': [_work('C2'), _work('C3')]}
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('co_citation', {'seed_ids': ['W1', 'W2'], 'min_shared': 1,
                                     'source': 'openalex'},
                     _oa_mock(works=works, citing=citing))
    assert '2/2 seeds processed, 1 edges emitted' in text
    assert 'weight=1' in text


# ---------------------------------------------------------------------------
# Title fallback for records without a DOI
# ---------------------------------------------------------------------------

def test_find_by_title_requires_exact_normalized_match():
    seen = {}

    def handler(request):
        seen.update(_params(request))
        return httpx.Response(200, json={'results': [
            _work('W1', title='Organizing Visions in the Digital World: A Different Paper'),
            _work('W2', title='Organizing visions in the digital world — the case of the '
                              'blockchain discourse on Twitter', refs=['R1']),
        ]})

    c = _client(handler)
    work = _run(c.find_by_title(
        'Organizing Visions in the Digital World: The Case of the Blockchain '
        'Discourse on Twitter', 2021))
    assert work['id'].endswith('W2')
    assert seen['filter'].endswith('publication_year:2020-2022')
    _run(c.close())


def test_find_by_title_rejects_fuzzy_match():
    c = _client(lambda r: httpx.Response(200, json={'results': [
        _work('W1', title='Organizing Visions for AI in Education')]}))
    assert _run(c.find_by_title('Organizing Visions for Generative AI in Education')) is None
    _run(c.close())


def test_find_by_title_prefers_longest_reference_list():
    c = _client(lambda r: httpx.Response(200, json={'results': [
        _work('W1', title='Same Title'), _work('W2', title='Same Title', refs=['R1', 'R2'])]}))
    assert _run(c.find_by_title('Same title', 2020))['id'].endswith('W2')
    _run(c.close())


def test_scopus_seed_without_doi_resolves_by_title():
    m = _oa_mock()
    m.find_by_title = AsyncMock(return_value=_work('W5', title='Conf Paper'))
    m.references = AsyncMock(return_value=[_work('W6', title='Ref')])
    scopus = MagicMock()
    scopus.get_abstract = AsyncMock(return_value={'abstracts-retrieval-response': {
        'coredata': {'dc:title': 'Conf Paper', 'prism:coverDate': '2021-01-01'}}})
    text = _call('get_references', {'scopus_id': '85151965536', 'source': 'openalex'},
                 m, client_mock=scopus)
    assert "'title': 'Ref'" in text
    m.find_by_title.assert_awaited_once_with('Conf Paper', 2021)
    m.get_work.assert_not_awaited()


def test_scopus_seed_without_doi_or_title_match_explains():
    m = _oa_mock()
    m.find_by_title = AsyncMock(return_value=None)
    scopus = MagicMock()
    scopus.get_abstract = AsyncMock(return_value={'abstracts-retrieval-response': {
        'coredata': {'dc:title': 'Conf Paper', 'prism:coverDate': '2021-01-01'}}})
    text = _call('get_references', {'scopus_id': '85151965536', 'source': 'openalex'},
                 m, client_mock=scopus)
    assert 'no DOI and no exact title match' in text



def test_search_value_strips_wildcards_and_or():
    assert oa._search_value('Should ChatGPT be Banned at Schools?') == \
        'Should ChatGPT be Banned at Schools'
    assert oa._search_value('a*b | c, d') == 'a b c d'


def test_coupling_skip_explains_empty_openalex_reference_list(tmp_path):
    works = {'W1': _work('W1', refs=['R1']), 'W2': _work('W2', refs=[])}
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('bibliographic_coupling',
                     {'seed_ids': ['W1', 'W2'], 'source': 'openalex'}, _oa_mock(works=works))
    assert 'W2 (OpenAlex W2 lists no references)' in text


def test_scopus_coupling_fetches_title_when_ref_view_lacks_it(tmp_path):
    def ref_raw(sid):
        refs = [{'@id': str(i), 'scopus-id': f'R{i}'} for i in (1, 2)]
        return {'abstracts-retrieval-response': {
            'coredata': {}, 'references': {'@total-references': '2', 'reference': refs}}}

    scopus = MagicMock()
    scopus.get_references = AsyncMock(side_effect=ref_raw)
    scopus.get_abstract = AsyncMock(side_effect=lambda sid: {'abstracts-retrieval-response': {
        'coredata': {'dc:title': f'Title {sid}', 'prism:coverDate': '2021-01-01'}}})
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call('bibliographic_coupling', {'seed_ids': ['111', '222'], 'min_shared': 1},
                     _oa_mock(), client_mock=scopus)
    assert "'Title 111' → 'Title 222'" in text
