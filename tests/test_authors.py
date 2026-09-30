"""search_authors: name parsing, Scopus query building, result cleaning, tool."""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scopus_mcp.authors import (
    clean_openalex_author,
    clean_scopus_author,
    parse_author_name,
    scopus_author_query,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.mark.parametrize('name,expected', [
    ('Swanson, E. Burton', ('Swanson', 'E. Burton')),
    ('E. Burton Swanson', ('Swanson', 'E. Burton')),
    ('  Swanson  ', ('Swanson', None)),
    ('Swanson,', ('Swanson', None)),
])
def test_parse_author_name(name, expected):
    assert parse_author_name(name) == expected


def test_scopus_author_query():
    assert scopus_author_query('Swanson, E. Burton', 'Los Angeles') == (
        'AUTHLASTNAME(Swanson) AND AUTHFIRST(E. Burton) AND AFFIL(Los Angeles)')
    assert scopus_author_query('Swanson') == 'AUTHLASTNAME(Swanson)'


def test_scopus_author_query_strips_syntax_characters():
    assert scopus_author_query('Smith, "J" (John)', 'MIT (Sloan)') == (
        'AUTHLASTNAME(Smith) AND AUTHFIRST(J John) AND AFFIL(MIT Sloan)')


def test_scopus_author_query_needs_a_name():
    with pytest.raises(ValueError):
        scopus_author_query('   ')


SCOPUS_ENTRY = {
    'dc:identifier': 'AUTHOR_ID:7101660605',
    'preferred-name': {'surname': 'Swanson', 'given-name': 'E. Burton', 'initials': 'E.B.'},
    'name-variant': [{'surname': 'Swanson', 'given-name': 'E. B.'},
                     {'surname': 'Swanson', 'given-name': 'E. B.'}],
    'document-count': '94',
    'affiliation-current': {'affiliation-name': 'University of California, Los Angeles',
                            'affiliation-city': 'Los Angeles',
                            'affiliation-country': 'United States'},
    'subject-area': [{'$': 'Computer Science (all)'}, {'$': 'Computer Science (all)'},
                     {'$': 'Business, Management and Accounting (all)'}],
}


def test_clean_scopus_author():
    assert clean_scopus_author(SCOPUS_ENTRY) == {
        'author_id': '7101660605', 'name': 'Swanson, E. Burton', 'document_count': 94,
        'affiliation': 'University of California, Los Angeles', 'city': 'Los Angeles',
        'country': 'United States',
        'subject_areas': ['Computer Science (all)', 'Business, Management and Accounting (all)'],
        'name_variants': ['Swanson, E. B.'], 'source': 'scopus',
    }


def test_clean_scopus_author_single_subject_as_dict():
    rec = clean_scopus_author({'dc:identifier': 'AUTHOR_ID:1',
                               'subject-area': {'$': 'Medicine (all)'}})
    assert rec['subject_areas'] == ['Medicine (all)'] and rec['document_count'] is None


OA_AUTHOR = {
    'id': 'https://openalex.org/A5102983203', 'display_name': 'E. Burton Swanson',
    'orcid': 'https://orcid.org/0000-0002-1602-407X', 'works_count': 132,
    'cited_by_count': 9000, 'summary_stats': {'h_index': 35},
    'last_known_institutions': [{'display_name': 'University of California, Los Angeles',
                                 'country_code': 'US'}],
    'topics': [{'display_name': f'T{i}'} for i in range(5)],
}


def test_clean_openalex_author():
    rec = clean_openalex_author(OA_AUTHOR)
    assert rec['openalex_id'] == 'A5102983203' and rec['orcid'] == '0000-0002-1602-407X'
    assert rec['h_index'] == 35 and rec['topics'] == ['T0', 'T1', 'T2']
    assert rec['affiliation'].startswith('University of California')


def _call(args, scopus=None, openalex=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', scopus or MagicMock()), \
         patch.object(server, 'openalex', openalex or MagicMock()):
        return _run(server.handle_call_tool('search_authors', args))[0].text


def test_tool_scopus():
    scopus = MagicMock()
    scopus.search_authors = AsyncMock(return_value={'search-results': {'entry': [
        SCOPUS_ENTRY, {'error': 'Result set was empty'}]}})
    out = json.loads(_call({'name': 'E. Burton Swanson', 'affiliation': 'Los Angeles',
                            'count': 99}, scopus=scopus))
    assert out['query'] == 'AUTHLASTNAME(Swanson) AND AUTHFIRST(E. Burton) AND AFFIL(Los Angeles)'
    assert [a['author_id'] for a in out['authors']] == ['7101660605']
    assert scopus.search_authors.await_args.kwargs['count'] == 25  # capped


def test_tool_openalex_filters_by_affiliation():
    other = dict(OA_AUTHOR, id='https://openalex.org/A2', last_known_institutions=[
        {'display_name': 'Harvard University', 'country_code': 'US'}])
    oa = MagicMock()
    oa.search_authors = AsyncMock(return_value=[other, OA_AUTHOR])
    out = json.loads(_call({'name': 'Swanson', 'affiliation': 'los angeles',
                            'source': 'openalex'}, openalex=oa))
    assert [a['openalex_id'] for a in out['authors']] == ['A5102983203']
    assert oa.search_authors.await_args.kwargs['count'] == 25  # full page when filtering


def test_tool_no_match_note():
    oa = MagicMock()
    oa.search_authors = AsyncMock(return_value=[])
    out = json.loads(_call({'name': 'Nobody', 'source': 'openalex'}, openalex=oa))
    assert out['authors'] == [] and 'No matching authors' in out['note']


def test_tool_requires_name():
    assert 'name is required' in _call({'name': '  '})


def test_tool_openalex_affiliation_miss_explains_limit():
    oa = MagicMock()
    oa.search_authors = AsyncMock(return_value=[OA_AUTHOR])
    out = json.loads(_call({'name': 'Swanson', 'affiliation': 'Harvard',
                            'source': 'openalex'}, openalex=oa))
    assert 'top 25 OpenAlex matches' in out['note'] and 'Add given names' in out['note']
