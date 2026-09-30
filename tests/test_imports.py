"""Export-file import (offline)."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scopus_mcp.importers import apply_corpus_file, deduplicate, read_export

FX = Path(__file__).parent / 'fixtures' / 'exports'


def test_scopus_csv():
    recs = read_export(str(FX / 'scopus.csv'))
    assert [r['scopus_id'] for r in recs] == ['0031512927', '77649171480']
    assert recs[0]['doi'] == '10.1287/orsc.8.5.458' and recs[0]['year'] == '1997'
    assert recs[0]['author_keywords'] == ['Organizing vision', 'Innovation']
    assert recs[1]['format'] == 'scopus_csv'


def test_wos_plain_with_continuation_lines():
    recs = read_export(str(FX / 'wos.txt'))
    assert len(recs) == 2
    assert recs[0]['title'] == 'Chasing the hottest IT: Effects of information technology fashion on organizations'
    assert recs[0]['wos_id'] == '000275223300004' and recs[0]['doi'] == '10.2307/20721415'
    assert recs[1]['authors'] == ['Kohli, R', 'Melville, NP']
    assert recs[0]['author_keywords'] == ['information technology fashion', 'management fashion', 'diffusion']


def test_ris_and_bibtex():
    ris = read_export(str(FX / 'scopus.ris'))[0]
    assert ris['scopus_id'] == '0041928100' and ris['authors'] == ['Ramiller, N.C.', 'Swanson, E.B.']
    assert ris['author_keywords'] == ['Organizing vision', 'Executive response']
    bib = read_export(str(FX / 'scopus.bib'))[0]
    assert bib['scopus_id'] == '9744280481' and bib['doi'] == '10.2307/25148655'
    assert bib['authors'] == ['Swanson, E. Burton', 'Ramiller, Neil C.']
    assert bib['author_keywords'] == ['Innovation', 'Mindfulness', 'Organizing vision']


def test_dedup_across_scopus_and_wos():
    recs = read_export(str(FX / 'scopus.csv')) + read_export(str(FX / 'wos.txt'))
    unique, dups = deduplicate(recs)
    assert dups == 1 and len(unique) == 3
    wang = next(r for r in unique if r.get('doi') == '10.2307/20721415')
    assert wang['scopus_id'] == '77649171480' and wang['wos_id'] == '000275223300004'
    assert len(wang['author_keywords']) == 3          # the longer keyword list wins


def test_refuses_other_files(tmp_path):
    bad = tmp_path / 'secrets.json'
    bad.write_text('{}')
    with pytest.raises(ValueError, match='only'):
        read_export(str(bad))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_import_tool_resolves_wos_records_and_feeds_other_tools(tmp_path):
    client = MagicMock()

    async def search_all(query, max_results=25, sort=None):
        assert query == 'DOI("10.1111/isj.12193")'
        return {'search-results': {'entry': [{'dc:identifier': 'SCOPUS_ID:85058072468',
                                               'prism:doi': '10.1111/isj.12193', 'dc:title': 'Digital innovation'}]}}
    client.search_all = AsyncMock(side_effect=search_all)
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy', 'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        from scopus_mcp import server
        with patch.object(server, 'client', client):
            text = _run(server.handle_call_tool('import_records', {
                'paths': [str(FX / 'scopus.csv'), str(FX / 'wos.txt')]}))[0].text
    assert 'Imported 4 records from 2 file(s): 3 unique (1 duplicates merged).' in text
    assert 'Scopus IDs: 3 of 3 (from the export 2, by DOI 1, by title 0); DOIs: 3.' in text
    corpus = next(tmp_path.glob('scopus-corpus-*.json'))
    args = apply_corpus_file({'corpus_file': str(corpus)}, 'scopus')
    assert args['ids'] == ['0031512927', '77649171480', '85058072468']
    assert apply_corpus_file(args, 'scopus') == args       # idempotent
    with pytest.raises(ValueError, match='not several'):
        apply_corpus_file({'corpus_file': str(corpus), 'query': 'x'}, 'scopus')


def test_thematic_evolution_from_file_needs_no_api(tmp_path):
    corpus = tmp_path / 'c.json'
    records = [{'year': str(y), 'author_keywords': ['organizing vision', kw], 'title': 't'}
               for y, kw in [(1997, 'discourse'), (1998, 'discourse'), (2010, 'it fashion'), (2011, 'it fashion')]]
    corpus.write_text(json.dumps({'kind': 'scopus-plus-mcp corpus', 'records': records}))
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy', 'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        from scopus_mcp import server
        with patch.object(server, 'client', MagicMock()) as client:
            text = _run(server.handle_call_tool('thematic_evolution', {
                'corpus_file': str(corpus), 'cut_years': [2005], 'construct_terms': ['organizing vision']}))[0].text
            client.get_abstract.assert_not_called()
    assert text.startswith('Thematic evolution of 4 papers (corpus file; terms: author_keywords; author 4).')
    assert 'co-occurs with: discourse' in text and 'co-occurs with: it fashion' in text
