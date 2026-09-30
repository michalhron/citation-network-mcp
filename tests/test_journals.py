"""get_journal_metrics: Serial Title parsing, ISSN/source-ID matching, OpenAlex."""
import asyncio
import csv
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scopus_mcp.client import ScopusClient
from scopus_mcp.journals import (
    clean_openalex_source,
    clean_serial_entry,
    format_issn,
    normalize_issn,
    serial_entry_issns,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _serial(title, issn, eissn, srcid, sjr=(('2025', '4.061'),)):
    return {
        'dc:title': title, 'dc:publisher': 'Pub', 'prism:issn': issn, 'prism:eIssn': eissn,
        'source-id': srcid,
        'SJRList': {'SJR': [{'@year': y, '$': v} for y, v in sjr]},
        'SNIPList': {'SNIP': [{'@year': '2025', '$': '2.391'}]},
        'citeScoreYearInfoList': {'citeScoreCurrentMetric': '12.5',
                                  'citeScoreCurrentMetricYear': '2025',
                                  'citeScoreTracker': '12.6',
                                  'citeScoreTrackerYear': '2026'},
        'subject-area': [{'@code': '1710', '$': 'Information Systems'},
                         {'@code': '1802', '$': 'Information Systems and Management'}],
        'coverageStartYear': '1977', 'coverageEndYear': '2026',
    }


MISQ = _serial('MIS Quarterly', '0276-7783', '2162-9730', '12402',
               sjr=(('2024', '3.9'), ('2025', '4.061')))
# Journal of Information Technology: Serial Title knows 2251-919X / 1466-4437,
# while Scopus search records carry print ISSN 0268-3962.
JIT = _serial('Journal of Information Technology', '2251-919X', '1466-4437', '12815')


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('raw,expected', [
    ('0276-7783', '02767783'), ('02767783', '02767783'), (' 0960-085x ', '0960085X'),
    ('2251-919X', '2251919X'), ('12402', None), ('', None), (None, None),
])
def test_normalize_issn(raw, expected):
    assert normalize_issn(raw) == expected


def test_format_issn():
    assert format_issn('0960085X') == '0960-085X'
    assert format_issn(None) is None


def test_clean_serial_entry_takes_latest_year():
    rec = clean_serial_entry(MISQ)
    assert rec['sjr'] == {'year': 2025, 'value': 4.061}
    assert rec['snip'] == {'year': 2025, 'value': 2.391}
    assert rec['citescore'] == {'year': 2025, 'value': 12.5}
    assert rec['citescore_tracker'] == {'year': 2026, 'value': 12.6}
    assert rec['subject_areas'] == ['Information Systems', 'Information Systems and Management']
    assert rec['coverage'] == '1977-2026' and rec['source_id'] == '12402'


def test_clean_serial_entry_tolerates_missing_metrics():
    rec = clean_serial_entry({'dc:title': 'New Journal', 'SJRList': {'SJR': {'@year': 'x'}}})
    assert rec['sjr'] is None and rec['citescore'] is None and rec['subject_areas'] == []


def test_serial_entry_issns():
    assert serial_entry_issns(JIT) == ['2251919X', '14664437']


def test_clean_openalex_source():
    rec = clean_openalex_source({
        'id': 'https://openalex.org/S9', 'display_name': 'MIS Quarterly', 'issn_l': '0276-7783',
        'issn': ['0276-7783', '2162-9730'], 'works_count': 10, 'cited_by_count': 99,
        'summary_stats': {'2yr_mean_citedness': 7.6, 'h_index': 359, 'i10_index': 1846},
    })
    assert rec['openalex_id'] == 'S9' and rec['h_index'] == 359
    assert rec['two_year_mean_citedness'] == 7.6 and rec['source'] == 'openalex'


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _make_client():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}), \
         patch('scopus_mcp.client.CacheManager') as MockCache:
        MockCache.return_value.get.return_value = None
        return ScopusClient()


def test_serial_titles_batches_by_25():
    client = _make_client()
    calls = []

    def side_effect(method, endpoint, params=None, **kwargs):
        calls.append(params['issn'].split(','))
        return {'serial-metadata-response': {'entry': [MISQ]}} if len(calls) == 1 else {}

    issns = [f'{i:08d}' for i in range(26)]
    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        entries = _run(client.serial_titles(issns))
    assert [len(c) for c in calls] == [25, 1]
    assert entries == [MISQ]  # the {} (404) batch contributes nothing
    _run(client.close())


def test_source_id_issns():
    client = _make_client()
    data = {'search-results': {'entry': [{'prism:issn': '02683962', 'prism:eIssn': '14664437',
                                          'prism:publicationName': 'JIT'}]}}
    with patch.object(client, '_request', new_callable=AsyncMock, return_value=data) as req:
        assert _run(client.source_id_issns('12815')) == {
            'issn': '02683962', 'eissn': '14664437', 'name': 'JIT'}
    assert req.await_args.args[2]['query'] == 'SRCID(12815)'
    with patch.object(client, '_request', new_callable=AsyncMock,
                      return_value={'search-results': {'entry': [{'error': 'Result set was empty'}]}}):
        assert _run(client.source_id_issns('0')) is None
    _run(client.close())


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

def _call(args, scopus=None, openalex=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', scopus or MagicMock()), \
         patch.object(server, 'openalex', openalex or MagicMock()):
        return _run(server.handle_call_tool('get_journal_metrics', args))[0].text


def test_scopus_issns_and_source_ids(tmp_path):
    scopus = MagicMock()
    scopus.source_id_issns = AsyncMock(side_effect=lambda sid: {
        '12815': {'issn': '02683962', 'eissn': '14664437', 'name': 'JIT'},
        '99': None,
    }[sid])
    scopus.serial_titles = AsyncMock(return_value=[MISQ, JIT])
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        out = json.loads(_call({'issns': ['0276-7783', '0268-3962', 'banana'],
                                'source_ids': ['12815', '99']}, scopus=scopus))
    by_input = {r['input']: r for r in out['journals']}
    assert by_input['0276-7783']['title'] == 'MIS Quarterly'
    # JIT resolves through its source ID even though 0268-3962 is unknown to Serial Title.
    assert by_input['source_id 12815']['title'] == 'Journal of Information Technology'
    assert any(n.startswith("0268-3962: not in Serial Title") for n in out['not_found'])
    assert "banana: not a valid ISSN" in out['not_found']
    assert "source_id 99: no Scopus records" in out['not_found']
    # Both ISSNs of the mapped source ID were requested alongside the direct ones.
    requested = scopus.serial_titles.await_args.args[0]
    assert {'02767783', '02683962', '14664437'} <= set(requested)
    with open(out['csv_path'], newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    assert rows[0]['sjr'] == '4.061' and rows[0]['sjr_year'] == '2025'


def test_openalex_source(tmp_path):
    oa = MagicMock()
    oa.get_source_by_issn = AsyncMock(side_effect=lambda issn: {
        '0276-7783': {'id': 'https://openalex.org/S9', 'display_name': 'MIS Quarterly',
                      'summary_stats': {'h_index': 359}},
    }.get(issn))
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        out = json.loads(_call({'issns': ['02767783', '1234-5679'], 'source': 'openalex'},
                               openalex=oa))
    assert out['journals'][0]['h_index'] == 359
    assert out['not_found'] == ['1234-5679: not in OpenAlex']
    assert 'not comparable' in out['note']


def test_openalex_rejects_source_ids():
    assert 'OpenAlex has no Scopus source IDs' in _call(
        {'source_ids': ['12402'], 'source': 'openalex'})


def test_requires_some_input():
    assert 'Give issns and/or source_ids' in _call({})
