"""Category percentiles, find_journals and topic_landscape."""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scopus_mcp.client import ScopusClient
from scopus_mcp.journals import (
    venue_type,
    category_names,
    clean_serial_entry,
    quartile,
    resolve_categories,
    srcid_queries,
    subject_ranks,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _year(year, status, ranks, citescore='12.5'):
    return {'@year': str(year), '@status': status, 'citeScoreInformationList': [{
        'citeScoreInfo': [{'docType': 'all', 'citeScore': citescore,
                           'citeScoreSubjectRank': [
                               {'subjectCode': c, 'rank': str(r), 'percentile': str(p)}
                               for c, r, p in ranks]}]}]}


def _journal(title, sid, issn, ranks_by_year, subjects=(), agg='Journal'):
    return {'dc:title': title, 'source-id': sid, 'prism:issn': issn, 'prism:eIssn': None,
            'dc:publisher': 'Pub', 'prism:aggregationType': agg,
            'subject-area': [{'@code': c, '$': n} for c, n in subjects],
            'citeScoreYearInfoList': {'citeScoreYearInfo': ranks_by_year}}


MISQ = _journal('MIS Quarterly', '12402', '0276-7783', [
    _year(2026, 'In-Progress', [('1710', 1, 99)]),
    _year(2025, 'Complete', [('1802', 14, 91), ('1710', 42, 90), ('1404', 16, 87)]),
    _year(2024, 'Complete', [('1710', 30, 93)]),
], subjects=[('1710', 'Information Systems'), ('1802', 'Information Systems and Management')])

ASJC = [
    {'code': '1404', 'description': 'Business, Management and Accounting', 'detail': 'Management Information Systems'},
    {'code': '1710', 'description': 'Computer Science', 'detail': 'Information Systems'},
    {'code': '1802', 'description': 'Decision Sciences', 'detail': 'Information Systems and Management'},
    {'code': '3309', 'description': 'Social Sciences', 'detail': 'Library and Information Sciences'},
]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('pct,q', [(99, 'Q1'), (75, 'Q1'), (74, 'Q2'), (50, 'Q2'),
                                   (49, 'Q3'), (25, 'Q3'), (24, 'Q4'), (0, 'Q4'), (None, None)])
def test_quartile(pct, q):
    assert quartile(pct) == q


def test_subject_ranks_uses_latest_complete_year():
    out = subject_ranks(MISQ, category_names(ASJC))
    assert out['year'] == 2025  # 2026 is In-Progress
    assert [(r['code'], r['percentile'], r['quartile']) for r in out['ranks']] == [
        ('1802', 91, 'Q1'), ('1710', 90, 'Q1'), ('1404', 87, 'Q1')]
    # Name from the entry itself first, then from the ASJC list.
    assert out['ranks'][2]['category'] == 'Management Information Systems'


def test_subject_ranks_single_dict_shapes_and_no_complete_year():
    one = {'citeScoreYearInfoList': {'citeScoreYearInfo': {
        '@year': '2024', '@status': 'Complete', 'citeScoreInformationList': {
            'citeScoreInfo': {'docType': 'all', 'citeScore': '3.0',
                              'citeScoreSubjectRank': {'subjectCode': '1710', 'rank': '9', 'percentile': '40'}}}}}}
    out = subject_ranks(one)
    assert out['year'] == 2024 and out['ranks'][0]['quartile'] == 'Q3'
    fresh = {'citeScoreYearInfoList': {'citeScoreYearInfo': [_year(2026, 'In-Progress', [('1710', 1, 99)])]}}
    assert subject_ranks(fresh) == {'year': None, 'citescore': None, 'ranks': []}


def test_clean_serial_entry_carries_percentiles():
    rec = clean_serial_entry(MISQ)
    assert rec['percentile_year'] == 2025 and rec['best_quartile'] == 'Q1'
    assert rec['category_percentiles'][0]['code'] == '1802'


def test_resolve_categories():
    assert resolve_categories(['1710'], ASJC)[0]['category'] == 'Information Systems'
    # An exact name wins over the longer names that contain it.
    assert resolve_categories(['information systems'], ASJC)[0]['code'] == '1710'
    assert resolve_categories(['Library'], ASJC)[0]['code'] == '3309'


def test_resolve_categories_ambiguous_and_unknown():
    with pytest.raises(ValueError, match='several categories.*1802.*9999'):
        resolve_categories(['Information Systems and'], ASJC + [
            {'code': '9999', 'description': 'X', 'detail': 'Information Systems and More'}])
    with pytest.raises(ValueError, match='several categories'):
        resolve_categories(['Management'], ASJC)
    with pytest.raises(ValueError, match='No subject category'):
        resolve_categories(['Astrology'], ASJC)


def test_srcid_queries_chunks():
    ids = [str(i) for i in range(250)]
    parts = srcid_queries(ids)
    assert len(parts) == 3 and parts[0].startswith('SRCID(0 OR 1 OR')
    assert parts[-1].count(' OR ') == 49
    assert srcid_queries([]) == []


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _make_client():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}), \
         patch('scopus_mcp.client.CacheManager') as MockCache:
        MockCache.return_value.get.return_value = None
        return ScopusClient()


def test_journals_in_category_pages_until_short_page():
    client = _make_client()
    starts = []

    def side_effect(method, endpoint, params=None, **kw):
        starts.append(params['start'])
        n = 200 if params['start'] < 400 else 46
        return {'serial-metadata-response': {'entry': [{'dc:title': f"J{i}"} for i in range(n)]}}

    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect) as req:
        entries = _run(client.journals_in_category('1710'))
    assert len(entries) == 446 and starts == [0, 200, 400]
    assert req.await_args.args[2]['view'] == 'CITESCORE'
    _run(client.close())


def test_serial_titles_requests_citescore_view():
    client = _make_client()
    with patch.object(client, '_request', new_callable=AsyncMock, return_value={}) as req:
        _run(client.serial_titles(['02767783']))
    assert req.await_args.args[2] == {'issn': '02767783', 'view': 'CITESCORE'}
    _run(client.close())


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _call(name, args, scopus):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', scopus):
        return json.loads(_run(server.handle_call_tool(name, args))[0].text)


def test_find_journals_filters_by_percentile_in_that_category(tmp_path):
    other = _journal('Other', '222', '1234-5679', [_year(2025, 'Complete', [('1710', 300, 40)])])
    proceedings = _journal('Conf', '333', '2345-6780', [_year(2025, 'Complete', [('1710', 1, 99)])],
                           agg='Conference Proceeding')
    unranked = _journal('New', '444', '3456-7891', [])
    scopus = MagicMock()
    scopus.asjc_categories = AsyncMock(return_value=ASJC)
    scopus.journals_in_category = AsyncMock(return_value=[MISQ, other, proceedings, unranked])
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        out = _call('find_journals', {'categories': ['Information Systems'], 'min_percentile': 90}, scopus)
    cat = out['categories'][0]
    assert cat['code'] == '1710' and cat['titles_listed'] == 3 and cat['without_rank'] == 1
    assert [j['title'] for j in out['journals']] == ['MIS Quarterly']
    assert out['journals'][0]['percentile'] == 90 and out['journals'][0]['issn'] == '0276-7783'
    assert out['scopus_query_fragments'] == ['SRCID(12402)']
    assert out['csv_path'].endswith('.csv')


def test_find_journals_can_include_non_journals():
    proceedings = _journal('Conf', '333', '2345-6780', [_year(2025, 'Complete', [('1710', 1, 99)])],
                           agg='Conference Proceeding')
    scopus = MagicMock()
    scopus.asjc_categories = AsyncMock(return_value=ASJC)
    scopus.journals_in_category = AsyncMock(return_value=[proceedings])
    out = _call('find_journals', {'categories': ['1710'], 'journals_only': False}, scopus)
    assert out['unique_journals'] == 1


def test_find_journals_rejects_bad_percentile():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', MagicMock()):
        text = _run(server.handle_call_tool('find_journals', {'categories': ['1710'],
                                                              'min_percentile': 100}))[0].text
    assert 'between 0 and 99' in text


def _paper(sid, issn, venue):
    return {'dc:identifier': f'SCOPUS_ID:{sid}{venue}', 'source-id': sid, 'prism:issn': issn,
            'prism:eIssn': None, 'prism:publicationName': venue}


def test_topic_landscape_counts_papers_per_category_quartile(tmp_path):
    q3_journal = _journal('Weak J', '555', '5555-5555',
                          [_year(2025, 'Complete', [('1710', 400, 30), ('1404', 10, 80)])])
    scopus = MagicMock()
    scopus.search_facets = AsyncMock(return_value={'search-results': {
        'opensearch:totalResults': '4',
        'facet': {'name': 'subjarea', 'category': [
            {'label': 'Computer Science (all)', 'hitCount': '3'}]}}})
    scopus.search_all = AsyncMock(return_value={'search-results': {'entry': [
        _paper('12402', '02767783', 'MIS Quarterly'),
        _paper('12402', '02767783', 'MIS Quarterly'),
        _paper('555', '55555555', 'Weak J'),
        _paper('999', None, 'AMCIS 2013'),
    ]}})
    scopus.asjc_categories = AsyncMock(return_value=ASJC)
    scopus.serial_titles = AsyncMock(return_value=[MISQ, q3_journal])
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        out = _call('topic_landscape', {'query': 'TITLE-ABS-KEY("x")', 'from_year': 2015,
                                        'to_year': 2024}, scopus)
    assert out['query'] == '(TITLE-ABS-KEY("x")) AND PUBYEAR > 2014 AND PUBYEAR < 2025'
    assert out['coverage'] == 'complete'
    assert out['broad_areas_all_results'] == [{'area': 'Computer Science', 'papers': 3}]
    by_code = {c['code']: c for c in out['categories']}
    # 1710: two MISQ papers in Q1 (90th), one Weak J paper in Q3 (30th).
    assert (by_code['1710']['papers'], by_code['1710']['Q1'], by_code['1710']['Q3']) == (3, 2, 1)
    # The same Weak J paper is Q1 in 1404 (80th): quartiles are per category.
    assert by_code['1404']['Q1'] == 3
    assert by_code['1710']['top_journals'][0] == {
        'journal': 'MIS Quarterly', 'type': 'journal', 'quartile': 'Q1', 'percentile': 90, 'papers': 2}
    assert out['unranked'] == {'papers': 1, 'share': 0.25,
                               'top_venues': [{'venue': 'AMCIS 2013', 'type': 'other', 'papers': 1}]}
    assert out['csv_path'].endswith('.csv')


def test_topic_landscape_reports_sample_coverage():
    scopus = MagicMock()
    scopus.search_facets = AsyncMock(return_value={'search-results': {'opensearch:totalResults': '1000'}})
    scopus.search_all = AsyncMock(return_value={'search-results': {'entry': [
        _paper('999', None, 'Conf') for _ in range(10)]}})
    scopus.asjc_categories = AsyncMock(return_value=ASJC)
    scopus.serial_titles = AsyncMock(return_value=[])
    out = _call('topic_landscape', {'query': 'x', 'max_papers': 10}, scopus)
    assert out['coverage'] == 'most recent 10 of 1000 (1%)'
    assert scopus.search_all.await_args.kwargs['max_results'] == 10



# ---------------------------------------------------------------------------
# Venue types: ranked proceedings series are not journals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('raw,expected', [
    ('journal', 'journal'), ('Journal', 'journal'),
    ('conferenceproceeding', 'conference proceedings'), ('Conference Proceeding', 'conference proceedings'),
    ('bookseries', 'book series'), ('Book Series', 'book series'), ('Book', 'book'),
    ('Trade Journal', 'trade journal'), ('Magazine', 'other'), (None, 'other'), ('', 'other'),
])
def test_venue_type(raw, expected):
    assert venue_type(raw) == expected


def test_clean_serial_entry_reports_venue_type():
    assert clean_serial_entry(MISQ)['venue_type'] == 'journal'


def _landscape_with_ifac(journals_only):
    ifac = _journal('IFAC-PapersOnLine', '777', '2405-8963',
                    [_year(2025, 'Complete', [('2207', 150, 40)])], agg='conferenceproceeding')
    scopus = MagicMock()
    scopus.search_facets = AsyncMock(return_value={'search-results': {'opensearch:totalResults': '3'}})
    scopus.search_all = AsyncMock(return_value={'search-results': {'entry': [
        dict(_paper('777', '24058963', 'IFAC Papersonline'), **{'prism:aggregationType': 'Conference Proceeding'}),
        dict(_paper('777', '24058963', 'IFAC Papersonline'), **{'prism:aggregationType': 'Conference Proceeding'}),
        dict(_paper('12402', '02767783', 'MIS Quarterly'), **{'prism:aggregationType': 'Journal'}),
    ]}})
    scopus.asjc_categories = AsyncMock(return_value=ASJC)
    scopus.serial_titles = AsyncMock(return_value=[MISQ, ifac])
    args = {'query': 'x'} if journals_only is None else {'query': 'x', 'journals_only': journals_only}
    return _call('topic_landscape', args, scopus)


def test_topic_landscape_journals_only_by_default():
    out = _landscape_with_ifac(None)
    ifac_cat = next(c for c in out['categories'] if c['code'] == '2207')
    assert (ifac_cat['papers'], ifac_cat['Q3'], ifac_cat['ranked_non_journal']) == (0, 0, 2)
    assert ifac_cat['top_non_journal'] == [{'venue': 'IFAC Papersonline',
                                            'type': 'conference proceedings', 'quartile': 'Q3', 'papers': 2}]
    assert out['quartiles_count'] == 'journal papers only'
    assert out['venue_mix'] == {'conference proceedings': {'papers': 2, 'share': 0.67},
                                'journal': {'papers': 1, 'share': 0.33}}


def test_topic_landscape_can_count_all_ranked_venues():
    out = _landscape_with_ifac(False)
    ifac_cat = next(c for c in out['categories'] if c['code'] == '2207')
    assert (ifac_cat['papers'], ifac_cat['Q3'], ifac_cat['ranked_non_journal']) == (2, 2, 0)
    assert ifac_cat['top_journals'][0]['type'] == 'conference proceedings'
    assert 'top_non_journal' not in ifac_cat
    assert out['quartiles_count'] == 'all ranked venues'


def test_topic_landscape_marks_unranked_journals_in_mix():
    scopus = MagicMock()
    scopus.search_facets = AsyncMock(return_value={'search-results': {'opensearch:totalResults': '1'}})
    scopus.search_all = AsyncMock(return_value={'search-results': {'entry': [
        dict(_paper('888', '11112222', 'Brand New Journal'), **{'prism:aggregationType': 'Journal'})]}})
    scopus.asjc_categories = AsyncMock(return_value=ASJC)
    scopus.serial_titles = AsyncMock(return_value=[])
    out = _call('topic_landscape', {'query': 'x'}, scopus)
    assert out['venue_mix'] == {'journal (unranked)': {'papers': 1, 'share': 1.0}}
