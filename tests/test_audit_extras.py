"""Coding agreement, retraction flags and edge contexts (offline)."""
import asyncio
import csv
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scopus_mcp import retractions
from scopus_mcp.agreement import analyse, cohen_kappa, interpret, norm_label, read_sheet


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_kappa_matches_the_textbook_example():
    a = ['y'] * 25 + ['n'] * 25
    b = ['y'] * 20 + ['n'] * 5 + ['y'] * 10 + ['n'] * 15
    k = cohen_kappa(a, b)
    assert (k['kappa'], k['observed'], k['expected']) == (0.4, 0.7, 0.5)
    assert k['ci95'][0] < 0.4 < k['ci95'][1]
    assert interpret(0.4) == 'fair' and interpret(0.67) == 'substantial' and interpret(-0.1) == 'worse than chance'
    assert norm_label(' Construct_Shifted ') == 'construct shifted'


def test_semicolon_sheet_with_quoted_citations(tmp_path):
    path = tmp_path / 'sheet.csv'
    rows = [
        {'edge': '1', 'contexts': 'fashions (Wang, 2010); and "organizing visions" (Ramiller; 2003)',
         'draft_label': 'hollow', 'coder_1_label': 'hollow', 'coder_2_label': 'Hollow'},
        {'edge': '2', 'contexts': 'We build on [36]; it', 'draft_label': 'substantive',
         'coder_1_label': 'substantive', 'coder_2_label': 'construct-shifted'},
        {'edge': '3', 'contexts': '', 'draft_label': 'unresolved', 'coder_1_label': '', 'coder_2_label': 'hollow'},
    ]
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=';')
        w.writeheader()
        w.writerows(rows)
    res = analyse(read_sheet(str(path)), 'coder_1_label', 'coder_2_label')
    assert res['coded'] == 2 and res['rows'] == 3
    assert res['disagreements'] == [{'id': '2', 'a': 'substantive', 'b': 'construct shifted',
                                     'citing': None, 'cited': None}]
    assert res['reference']['vs_coder_a']['observed'] == 1.0
    assert res['per_label']['hollow']['both'] == 1


def test_missing_column_is_named(tmp_path):
    path = tmp_path / 's.csv'
    path.write_text('edge,coder_a\n1,hollow\n')
    with pytest.raises(ValueError, match='coder_2_label'):
        analyse(read_sheet(str(path)), 'coder_1_label', 'coder_2_label')


def test_retraction_notices_from_crossref():
    def handler(request):
        assert 'updates:10.1/x,updates:10.1/y' in request.url.params['filter']
        return httpx.Response(200, json={'message': {'items': [
            {'DOI': '10.1/notice', 'update-to': [{'DOI': '10.1/X', 'type': 'retraction', 'label': 'Retraction',
                                                  'source': 'retraction-watch',
                                                  'updated': {'date-parts': [[2010, 2, 6]]}}]},
            {'DOI': '10.1/erratum', 'update-to': [{'DOI': '10.1/y', 'type': 'correction', 'label': 'Correction'}]}]}})
    real = httpx.AsyncClient
    with patch.object(retractions.httpx, 'AsyncClient',
                      side_effect=lambda *a, **k: real(*a, **{**k, 'transport': httpx.MockTransport(handler)})):
        out = _run(retractions.check_dois(['10.1/X', '10.1/y']))
    assert retractions.status_of(out['10.1/x']) == 'retracted'
    assert out['10.1/x'][0]['date'] == '2010-2-6' and out['10.1/x'][0]['notice_doi'] == '10.1/notice'
    assert retractions.status_of(out['10.1/y']) == 'corrected'
    assert retractions.status_of([]) == 'ok'


def _call(name, args, client_mock=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'client', client_mock or MagicMock()), \
         patch.object(server, 'openalex', MagicMock()):
        return _run(server.handle_call_tool(name, args))[0].text


def _network_client():
    client = MagicMock()
    client.search_all = AsyncMock(return_value={'search-results': {'entry': [
        {'dc:identifier': 'SCOPUS_ID:1', 'dc:creator': 'Swanson E.B.', 'prism:coverDate': '1997-01-01', 'prism:doi': '10.1/a'},
        {'dc:identifier': 'SCOPUS_ID:2', 'dc:creator': 'Ramiller N.C.', 'prism:coverDate': '2003-01-01', 'prism:doi': '10.1/b'}]}})
    client.get_references = AsyncMock(side_effect=lambda sid: {'abstracts-retrieval-response': {'references': {
        '@total-references': '1', 'reference': [{'@id': '1', 'scopus-id': '1' if sid == '2' else '9'}]}}})
    return client


def test_network_flags_retracted_main_path_paper(tmp_path):
    from scopus_mcp.tools import corpus

    async def flagged(dois):
        return {d: ([{'type': 'retraction', 'label': 'Retraction', 'date': '2020-1-1', 'notice_doi': '10.1/n'}]
                    if d == '10.1/b' else []) for d in dois}
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(corpus, 'check_dois', new=flagged):
        text = _call('citation_network', {'ids': ['1', '2'], 'check_completeness': False}, _network_client())
    assert 'RETRACTED or withdrawn (1): Ramiller 2003 [ON THE MAIN PATH] (Retraction 2020-1-1; notice 10.1/n)' in text


def test_edge_contexts_write_a_coding_sheet(tmp_path):
    from scopus_mcp.tools import corpus

    async def fake_gather(pairs, terms, max_contexts, fulltext=True):
        assert fulltext is False
        return [dict(p, context={'status': 'found', 'context_source': 'semantic_scholar', 'intents': ['methodology'],
                                 'is_influential': True,
                                 'contexts': ['We build on the organizing vision of Swanson and Ramiller (1997).']},
                     cited_ident={'surnames': ['Swanson', 'Ramiller'], 'year': 1997}) for p in pairs]
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(corpus, 'gather_contexts', new=fake_gather):
        text = _call('citation_network', {'ids': ['1', '2'], 'check_completeness': False, 'edge_contexts': True,
                                          'construct_terms': ['organizing vision']}, _network_client())
    assert ('Edge contexts for 1 edges (draft labels, a heuristic for coders): 1 substantive, 0 construct-shifted, '
            '0 hollow, 0 unresolved; on the main path: 1 substantive') in text
    sheet = next(tmp_path.glob('*-edge-coding.csv'))
    row = next(csv.DictReader(sheet.open(encoding='utf-8-sig')))
    assert row['on_main_path'] == 'True' and row['draft_label'] == 'substantive' and row['coder_1_label'] == ''
