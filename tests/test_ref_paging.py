"""REF-view paging: get_references must return every reference, not the
first 40 (the REF view's fixed page size)."""
import asyncio
import os
from unittest.mock import AsyncMock, patch

from scopus_mcp import client as client_mod
from scopus_mcp.client import ScopusClient
from scopus_mcp.utils import clean_references


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


def _ref(i):
    return {'@id': str(i), 'scopus-id': f'9{i:04d}', 'title': f'Ref {i}'}


def _page(start, count, total):
    refs = [_ref(i) for i in range(start, min(start + count, total + 1))]
    return {'abstracts-retrieval-response': {
        'coredata': {'dc:title': 'Seed'},
        'references': {'@total-references': str(total), 'reference': refs},
    }}


def _fake_ref_view(total, page=40, empty_after=None):
    calls = []

    def side_effect(method, endpoint, params=None, **kwargs):
        start = int(params.get('startref', 1))
        if 'refcount' in params:
            # The live API 400s when refcount runs past the end of the list.
            assert start - 1 + int(params['refcount']) <= total, params
        calls.append(start)
        if empty_after is not None and start > empty_after:
            return _page(start, 0, total)
        return _page(start, page, total)
    return side_effect, calls


def test_pages_until_total_and_keeps_order():
    client = _make_client()
    side_effect, calls = _fake_ref_view(total=95)
    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        data = _run(client.get_references('85216793920'))
    refs = clean_references(data)
    assert len(refs) == 95
    assert [r['position'] for r in refs[:3]] == ['1', '2', '3']
    assert refs[-1]['position'] == '95'
    assert calls == [1, 41, 81]
    _run(client.close())


def test_short_list_needs_one_request():
    client = _make_client()
    side_effect, calls = _fake_ref_view(total=12)
    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        refs = clean_references(_run(client.get_references('1')))
    assert len(refs) == 12
    assert calls == [1]
    _run(client.close())


def test_empty_page_stops_paging():
    client = _make_client()
    side_effect, calls = _fake_ref_view(total=172, empty_after=41)
    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        refs = clean_references(_run(client.get_references('1')))
    assert len(refs) == 80
    assert calls == [1, 41, 81]
    _run(client.close())


def test_single_reference_object_is_listified():
    client = _make_client()
    data = {'abstracts-retrieval-response': {'references': {
        '@total-references': '1', 'reference': _ref(1)}}}
    with patch.object(client, '_request', new_callable=AsyncMock, return_value=data):
        refs = clean_references(_run(client.get_references('1')))
    assert [r['position'] for r in refs] == ['1']
    _run(client.close())


def test_missing_references_block_returned_unchanged():
    client = _make_client()
    data = {'abstracts-retrieval-response': {'coredata': {}}}
    with patch.object(client, '_request', new_callable=AsyncMock, return_value=data) as req:
        assert _run(client.get_references('1')) == data
    assert req.await_count == 1
    _run(client.close())


def test_page_cap_bounds_requests(monkeypatch):
    monkeypatch.setattr(client_mod, 'REF_MAX_PAGES', 3)
    client = _make_client()
    side_effect, calls = _fake_ref_view(total=10_000)
    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        refs = clean_references(_run(client.get_references('1')))
    assert len(calls) == 3
    assert len(refs) == 120
    _run(client.close())


RANGE_400 = Exception(
    "Scopus API error 400 for x (query=None): {\"service-error\":{\"status\":"
    "{\"statusCode\":\"INVALID_INPUT\",\"statusText\":\"'startref' or "
    "'refcount' parameter missing or invalid\"}}}"
)


def test_phantom_last_reference_is_skipped_after_one_retry():
    """Live behaviour (2026-09-30): total says 172, entry 172 does not exist."""
    client = _make_client()
    seen = []

    def side_effect(method, endpoint, params=None, **kwargs):
        seen.append(dict(params))
        start = int(params.get('startref', 1))
        count = int(params.get('refcount', 40))
        if start - 1 + count > 171:
            raise RANGE_400
        return _page(start, count, 172)

    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        refs = clean_references(_run(client.get_references('1')))
    assert len(refs) == 171
    assert seen[-2:] == [{'view': 'REF', 'startref': 161, 'refcount': 12},
                         {'view': 'REF', 'startref': 161, 'refcount': 11}]
    _run(client.close())


def test_range_error_twice_is_raised():
    client = _make_client()

    def side_effect(method, endpoint, params=None, **kwargs):
        if 'startref' in params:
            raise RANGE_400
        return _page(1, 40, 172)

    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        try:
            _run(client.get_references('1'))
        except Exception as exc:
            assert 'startref' in str(exc)
        else:
            raise AssertionError('expected the second range error to surface')
    _run(client.close())


def test_other_errors_are_not_swallowed():
    client = _make_client()

    def side_effect(method, endpoint, params=None, **kwargs):
        if 'startref' in params:
            raise Exception('Scopus API server error 503 for x after 3 attempt(s)')
        return _page(1, 40, 172)

    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        try:
            _run(client.get_references('1'))
        except Exception as exc:
            assert '503' in str(exc)
        else:
            raise AssertionError('expected the 503 to surface')
    _run(client.close())


def test_last_page_requests_only_the_remainder():
    client = _make_client()
    seen = []

    def side_effect(method, endpoint, params=None, **kwargs):
        seen.append(dict(params))
        start = int(params.get('startref', 1))
        return _page(start, int(params.get('refcount', 40)), 172)

    with patch.object(client, '_request', new_callable=AsyncMock, side_effect=side_effect):
        refs = clean_references(_run(client.get_references('1')))
    assert len(refs) == 172
    assert seen[-1] == {'view': 'REF', 'startref': 161, 'refcount': 12}
    _run(client.close())
