"""get_bibtex: DOI content negotiation plus generated entries for DOI-less records."""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scopus_mcp import bibtex
from scopus_mcp.bibtex import (
    entry_key,
    fetch_bibtex,
    generated_entry,
    make_keys_unique,
    normalize_entry,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


SWANSON = (" @article{Swanson_1997, title={The Organizing Vision in Information Systems "
           "Innovation}, DOI={10.1287/orsc.8.5.458}, journal={Organization Science}, "
           "author={Swanson, E. Burton and Ramiller, Neil C.}, year={1997}, pages={458–474} }")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_normalize_entry_trims_and_fixes_page_dash():
    out = normalize_entry(SWANSON)
    assert out.startswith('@article{Swanson_1997,')
    assert 'pages={458--474}' in out


def test_entry_key():
    assert entry_key('@article{Swanson_1997, title={x}}') == 'Swanson_1997'
    assert entry_key('not bibtex') is None


def test_make_keys_unique_suffixes_repeats_only():
    entries = ['@article{Smith_2020, title={A}}', '@article{Jones_2019, title={B}}',
               '@inproceedings{Smith_2020, title={C}}']
    out = make_keys_unique(entries)
    assert [entry_key(e) for e in out] == ['Smith_2020a', 'Jones_2019', 'Smith_2020b']
    assert out[2].startswith('@inproceedings{Smith_2020b,')


def test_generated_entry_conference_paper():
    entry = generated_entry({
        'title': 'Organizing Visions in the Digital World',
        'authors': ['Miranda, S.M.', 'Kim, I.'],
        'year': '2021',
        'venue': '42nd International Conference on Information Systems, ICIS 2021',
    }, 'Scopus')
    assert entry.startswith('@inproceedings{Miranda_2021,')
    assert 'author = {Miranda, S.M. and Kim, I.}' in entry
    assert 'booktitle = {42nd International Conference' in entry
    assert 'note = {Generated from Scopus metadata; no DOI}' in entry


def test_generated_entry_journal_and_missing_fields():
    entry = generated_entry({'title': 'A {braced} title', 'venue': 'MIS Quarterly'}, 'OpenAlex')
    assert entry.startswith('@article{Anon_nd,')
    assert 'title = {A braced title}' in entry
    assert 'journal = {MIS Quarterly}' in entry


def test_generated_entry_needs_title():
    assert generated_entry({'authors': ['X, Y.']}, 'Scopus') is None


# ---------------------------------------------------------------------------
# fetch_bibtex over a mock transport
# ---------------------------------------------------------------------------

def _with_transport(handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return real(*args, **kwargs)
    return patch.object(bibtex.httpx, 'AsyncClient', side_effect=factory)


def test_fetch_bibtex_outcomes():
    def handler(request):
        assert request.headers['accept'].startswith('application/x-bibtex')
        path = request.url.path
        if path.endswith('orsc.8.5.458'):
            return httpx.Response(200, text=SWANSON,
                                  headers={'content-type': 'application/x-bibtex'})
        if path.endswith('html-200'):
            return httpx.Response(200, text='<!DOCTYPE html>',
                                  headers={'content-type': 'text/html'})
        if path.endswith('boom'):
            raise httpx.ConnectError('down')
        return httpx.Response(404, text='<html>', headers={'content-type': 'text/html'})

    with _with_transport(handler):
        out = _run(fetch_bibtex(['10.1287/orsc.8.5.458', '10.9999/missing',
                                 '10.9999/html-200', '10.9999/boom']))
    entry, err = out['10.1287/orsc.8.5.458']
    assert err is None and 'pages={458--474}' in entry
    assert out['10.9999/missing'] == (None, 'no BibTeX (HTTP 404)')
    assert out['10.9999/html-200'] == (None, 'no BibTeX (HTTP 200)')
    assert out['10.9999/boom'] == (None, 'network error: ConnectError')


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

def _call(args, scopus=None, openalex=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', scopus or MagicMock()), \
         patch.object(server, 'openalex', openalex or MagicMock()):
        return _run(server.handle_call_tool('get_bibtex', args))[0].text


def _scopus_abstract(doi=None, title='T', venue='Journal', year='2020'):
    coredata = {'dc:title': title, 'prism:publicationName': venue,
                'prism:coverDate': f'{year}-01-01'}
    if doi:
        coredata['prism:doi'] = doi
    return {'abstracts-retrieval-response': {
        'coredata': coredata,
        'authors': {'author': [{'ce:surname': 'Miranda', 'ce:initials': 'S.M.',
                                'ce:indexed-name': 'Miranda S.M.'}]},
    }}


def test_tool_mixed_identifiers(tmp_path):
    scopus = MagicMock()
    scopus.get_abstract = AsyncMock(side_effect=lambda sid: {
        '0031512927': _scopus_abstract(doi='10.1287/ORSC.8.5.458'),
        '85151965536': _scopus_abstract(title='Organizing Visions in the Digital World',
                                        venue='ICIS 2021 International Conference', year='2021'),
        '99999999999': {},
    }[sid])
    oa = MagicMock()
    oa.get_work = AsyncMock(return_value={
        'id': 'https://openalex.org/W5', 'title': 'OA Paper', 'doi': None,
        'publication_year': 2019,
        'primary_location': {'source': {'display_name': 'Some Journal'}},
        'authorships': [{'author': {'display_name': 'Norman P. Hummon'}}],
    })
    fetched = {'10.1287/orsc.8.5.458': (normalize_entry(SWANSON), None)}
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'fetch_bibtex', new_callable=AsyncMock,
                      return_value=fetched) as fetch, \
         patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        text = _call({'identifiers': ['10.1287/orsc.8.5.458', '0031512927',
                                      '85151965536', 'W5', '99999999999']},
                     scopus=scopus, openalex=oa)

    # DOI and Scopus ID of the same paper: fetched once, listed once.
    fetch.assert_awaited_once_with(['10.1287/orsc.8.5.458'])
    assert text.count('@article{Swanson_1997') == 1
    assert '3 BibTeX entries (2 generated from metadata; check them)' in text
    assert '@inproceedings{Miranda_2021,' in text
    assert 'author = {Hummon, Norman P.}' in text
    assert 'Generated from OpenAlex metadata' in text
    assert '99999999999: not found in Scopus' in text
    bib = next(tmp_path.glob('bibtex-3-*.bib')).read_text()
    assert bib.count('@') == 3


def test_tool_requires_identifiers():
    assert 'identifiers is required' in _call({'identifiers': []})


def test_tool_caps_identifier_count():
    assert 'At most 200 identifiers' in _call({'identifiers': [f'10.1234/{i}' for i in range(201)]})
