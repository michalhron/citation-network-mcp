"""search_fulltext: ScienceDirect request building, cleaning, mention analysis, tool."""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

from scopus_mcp.fulltext_search import (
    analyze_mentions,
    build_request,
    clean_result,
    query_terms,
    split_body,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Request and records
# ---------------------------------------------------------------------------

def test_build_request_defaults():
    assert build_request('"organizing vision"') == {
        'qs': '"organizing vision"', 'display': {'offset': 0, 'show': 100, 'sortBy': 'relevance'}}


def test_build_request_filters():
    body = build_request('x', journal='Information and Organization', from_year=2020,
                         open_access_only=True, offset=100, show=50, sort='date')
    assert body['pub'] == 'Information and Organization'
    assert body['date'] == '2020-2100'
    assert body['filters'] == {'openAccess': True}
    assert body['display'] == {'offset': 100, 'show': 50, 'sortBy': 'date'}
    assert build_request('x', to_year=1999)['date'] == '1823-1999'


def test_clean_result():
    rec = clean_result({'title': 'T', 'doi': '10.1016/x', 'pii': 'S1', 'sourceTitle': 'IO',
                        'publicationDate': '2024-06-30', 'openAccess': True, 'uri': 'u',
                        'authors': [{'order': 1, 'name': 'E. Burton Swanson'}, {'name': 'N. Ramiller'}]})
    assert rec['creator'] == 'E. Burton Swanson' and rec['authors'][1] == 'N. Ramiller'
    assert rec['publication_name'] == 'IO' and rec['open_access'] is True
    assert rec['source'] == 'sciencedirect'


def test_query_terms():
    assert query_terms('"organizing vision" AND "hype"') == (['organizing vision', 'hype'], True)
    assert query_terms('digital AND transformation OR ai') == (['digital', 'transformation'], False)
    assert query_terms('visio*') == (['visio'], False)


# ---------------------------------------------------------------------------
# Mention analysis
# ---------------------------------------------------------------------------

FRONT = "serial JL 123 Information and Organization Contents 1 Introduction References SWANSON 1997 ORGANIZINGVISIONINISINNOVATION "
BODY = (
    "An organizing vision helps a community make sense of an innovation. "
    "Filler sentence about something else entirely here. " * 3
    + "We extend the Organizing  Vision concept to platforms. "
    + "More filler that talks about methods and data collection. " * 3
    + "In closing, the organizing vision stays useful. "
)
REFS = "References Swanson, E.B., Ramiller, N.C., 1997. The organizing vision in information systems innovation. Organ. Sci. "
TEXT = FRONT + BODY + REFS


def test_split_body_uses_last_references_heading_in_second_half():
    body, refs = split_body(TEXT)
    assert refs.startswith('References Swanson')
    assert body.startswith('serial JL') and 'Contents 1 Introduction References' in body


def test_split_body_without_reference_list():
    assert split_body('Only body text.') == ('Only body text.', '')


def test_analyze_mentions_counts_body_and_references_separately():
    out = analyze_mentions(TEXT, '"organizing vision"')
    # Run-together capitals in the front index do not match; three body uses do.
    assert out['body_mentions'] == 5  # sentence 1 appears 3 times via the repeated filler block
    assert out['reference_list_mentions'] == 1
    assert out['positions_pct'] == sorted(out['positions_pct'])
    assert 0 <= out['positions_pct'][0] < out['positions_pct'][-1] <= 100


def test_analyze_mentions_snippets_spread_first_middle_last():
    text = ("Opening line mentions the organizing vision early on. "
            + "Filler text without the phrase goes here. " * 20
            + "A middle line uses the organizing vision too. "
            + "Filler text without the phrase goes here. " * 20
            + "The final line returns to the organizing vision. ")
    snippets = analyze_mentions(text, '"organizing vision"')['snippets']
    assert [s['text'].split()[0] for s in snippets] == ['Opening', 'A', 'The']
    assert snippets[0]['position_pct'] < snippets[1]['position_pct'] < snippets[2]['position_pct']


def test_analyze_mentions_unquoted_needs_all_words_in_a_sentence():
    text = "Digital work changes. Transformation happens slowly. Digital transformation is here. "
    out = analyze_mentions(text, 'digital transformation')
    assert out['body_mentions'] == 1
    assert out['snippets'][0]['text'] == 'Digital transformation is here.'


def test_analyze_mentions_long_sentence_is_trimmed_around_match():
    text = ("Words " * 200) + "the organizing vision appears here " + ("more " * 200) + "."
    snippet = analyze_mentions(text, '"organizing vision"')['snippets'][0]['text']
    assert 'organizing vision' in snippet and len(snippet) <= 362


def test_analyze_mentions_no_terms_or_text():
    empty = {'body_mentions': 0, 'reference_list_mentions': 0, 'positions_pct': [], 'snippets': []}
    assert analyze_mentions('', '"x"') == empty
    assert analyze_mentions('Some text.', 'AND OR') == empty


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

def _call(args, scopus):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', scopus):
        return _run(server.handle_call_tool('search_fulltext', args))[0].text


def _page(start, n, total):
    return {'resultsFound': total, 'results': [
        {'title': f'Paper {i}', 'doi': f'10.1016/p{i}', 'sourceTitle': 'IO',
         'publicationDate': '2024-01-01', 'authors': [{'name': f'A{i}'}]}
        for i in range(start, min(start + n, total))]}


def test_tool_pages_past_100(tmp_path):
    scopus = MagicMock()
    scopus.search_sciencedirect = AsyncMock(side_effect=lambda body: _page(
        body['display']['offset'], body['display']['show'], 121))
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        out = json.loads(_call({'query': '"organizing vision"', 'max_results': 150}, scopus))
    assert out['total_available'] == 121 and out['fetched'] == 121
    offsets = [c.args[0]['display']['offset'] for c in scopus.search_sciencedirect.await_args_list]
    assert offsets == [0, 100, 121]  # third page comes back empty and stops paging
    assert out['csv_path'].endswith('.csv') and len(out['sample']) == 10


def test_tool_context_analyses_top_results_and_skips_unavailable():
    scopus = MagicMock()
    scopus.search_sciencedirect = AsyncMock(side_effect=lambda body: _page(
        body['display']['offset'], body['display']['show'], 3))
    full = {'full-text-retrieval-response': {'originalText': TEXT + ' pad' * 2000}}
    scopus.get_sciencedirect_fulltext = AsyncMock(side_effect=[full, None])
    out = json.loads(_call({'query': '"organizing vision"', 'context': True,
                            'max_context': 2}, scopus))
    assert len(out['context']) == 2
    assert out['context'][0]['body_mentions'] >= 1
    assert out['context'][1]['error'] == 'full text not available with this access'
    assert out['results'][0]['source'] == 'sciencedirect'


def test_tool_rejects_bad_sort():
    assert "sort must be 'relevance' or 'date'" in _call({'query': 'x', 'sort': 'citations'}, MagicMock())
