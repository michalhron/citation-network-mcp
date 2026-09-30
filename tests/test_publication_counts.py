"""publication_counts: per-year hit counts from Scopus or OpenAlex."""
import asyncio
import datetime
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scopus_mcp.client import ScopusClient


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_client():
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}), \
         patch('scopus_mcp.client.CacheManager') as MockCache:
        MockCache.return_value.get.return_value = None
        return ScopusClient()


def _call(args, scopus=None, openalex=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', scopus or MagicMock()), \
         patch.object(server, 'openalex', openalex or MagicMock()):
        return _run(server.handle_call_tool('publication_counts', args))[0].text


# ---------------------------------------------------------------------------
# ScopusClient.yearly_counts
# ---------------------------------------------------------------------------

def test_scopus_yearly_counts_one_small_request_per_year():
    client = _make_client()
    seen = []

    def side_effect(method, endpoint, params=None, **kwargs):
        seen.append(params)
        year = int(params['query'].rsplit('=', 1)[1])
        return {'search-results': {'opensearch:totalResults': str(year - 2000)}}

    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        counts = _run(client.yearly_counts('TITLE-ABS-KEY("organizing vision")', 2019, 2021))
    assert counts == {2019: 19, 2020: 20, 2021: 21}
    assert sorted(p['query'] for p in seen) == [
        f'(TITLE-ABS-KEY("organizing vision")) AND PUBYEAR = {y}' for y in (2019, 2020, 2021)]
    assert all(p['count'] == 1 and p['field'] == 'dc:identifier' for p in seen)
    _run(client.close())


def test_scopus_yearly_counts_missing_total_is_zero():
    client = _make_client()
    with patch.object(client, '_request', new_callable=AsyncMock,
                      return_value={'search-results': {}}):
        assert _run(client.yearly_counts('x', 2020, 2020)) == {2020: 0}
    _run(client.close())


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

def test_scopus_tool_output():
    scopus = MagicMock()
    scopus.yearly_counts = AsyncMock(return_value={2019: 3, 2020: 7, 2021: 6})
    out = json.loads(_call({'query': 'q', 'from_year': 2019, 'to_year': 2021}, scopus=scopus))
    assert out == {
        'source': 'scopus', 'query': 'q',
        'counts': {'2019': 3, '2020': 7, '2021': 6},
        'total': 16, 'peak_year': 2020,
        'note': 'Scopus search hits per PUBYEAR for the query as written.',
    }


def test_scopus_requires_year_range():
    text = _call({'query': 'q', 'from_year': 2019})
    assert 'needs from_year and to_year' in text


def test_scopus_span_is_capped():
    text = _call({'query': 'q', 'from_year': 1900, 'to_year': 2020})
    assert 'spans at most 60 years' in text


def test_reversed_range_rejected():
    text = _call({'query': 'q', 'from_year': 2021, 'to_year': 2019, 'source': 'openalex'})
    assert 'is after to_year' in text


def test_openalex_zero_fills_requested_range():
    oa = MagicMock()
    oa.yearly_counts = AsyncMock(return_value=({2016: 2, 2018: 5}, 7))
    out = json.loads(_call({'query': '"organizing vision"', 'from_year': 2015,
                            'to_year': 2018, 'source': 'openalex'}, openalex=oa))
    assert out['counts'] == {'2015': 0, '2016': 2, '2017': 0, '2018': 5}
    assert out['total'] == 7 and out['peak_year'] == 2018
    oa.yearly_counts.assert_awaited_once_with('"organizing vision"', 2015, 2018)


def test_openalex_without_range_spans_observed_years():
    oa = MagicMock()
    oa.yearly_counts = AsyncMock(return_value=({1997: 1, 1999: 4}, 5))
    out = json.loads(_call({'query': 'x', 'source': 'openalex'}, openalex=oa))
    assert out['counts'] == {'1997': 1, '1998': 0, '1999': 4}


def test_no_hits_has_no_peak():
    oa = MagicMock()
    oa.yearly_counts = AsyncMock(return_value=({}, 0))
    out = json.loads(_call({'query': 'x', 'source': 'openalex'}, openalex=oa))
    assert out['counts'] == {} and out['peak_year'] is None


def test_current_year_flagged_incomplete():
    year = datetime.date.today().year
    oa = MagicMock()
    oa.yearly_counts = AsyncMock(return_value=({year: 1}, 1))
    out = json.loads(_call({'query': 'x', 'from_year': year - 1, 'to_year': year,
                            'source': 'openalex'}, openalex=oa))
    assert f'{year} is incomplete' in out['note']
