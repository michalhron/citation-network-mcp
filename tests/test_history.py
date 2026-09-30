"""RPYS and historiograph (offline)."""
from scopus_mcp.history import _surname, cited_work_label, historiograph, spectrogram


def _ref(year, sid, author='Swanson E.B.', title='T'):
    return {'year': str(year), 'scopus_id': sid, 'authors': [author], 'title': title}


def test_spectrogram_finds_the_root_year_and_its_work():
    refs = [_ref(y, f'bg{y}-{i}') for y in range(1990, 2001) for i in range(3)]
    refs += [_ref(1997, 'OV', title='The organizing vision in IS innovation') for _ in range(10)]
    refs += [{'year': None, 'title': 'undated'}]
    spec = spectrogram(refs, 1990, 2000)
    assert spec['undated'] == 1 and spec['n_references'] == 43
    top = spec['peaks'][0]
    assert top['year'] == 1997 and top['n'] == 13 and top['deviation'] == 10
    assert top['works'][0] == {'label': 'Swanson: The organizing vision in IS innovation', 'citations': 10}
    assert top['share_top_work'] == 0.77
    row = next(r for r in spec['rows'] if r['year'] == 1996)
    assert row['median'] == 3 and row['deviation'] == 0


def test_labels_keep_compound_surnames():
    assert _surname('Di Maggio P.J.') == 'Di Maggio'
    assert _surname('van de Ven A.H.') == 'van de Ven'
    assert cited_work_label({'authors': ['DiMaggio P.J.', 'Powell W.W.'], 'title': 'The iron cage revisited'}) == \
        'DiMaggio & Powell: The iron cage revisited'


def test_historiograph_ranks_by_local_citations():
    nodes = {
        'A': {'year': '1997', 'creator': 'Swanson E.B.', 'parents': [], 'cited_by_count': '600'},
        'B': {'year': '2003', 'creator': 'Ramiller N.C.', 'parents': ['A'], 'cited_by_count': '120'},
        'C': {'year': '2010', 'creator': 'Wang P.', 'parents': ['A', 'B'], 'cited_by_count': '270'},
        'D': {'year': '2019', 'creator': 'Kohli R.', 'parents': ['C'], 'cited_by_count': '800'},
    }
    hist = historiograph(nodes, top=3)
    assert [n['id'] for n in hist['nodes']] == ['A', 'B', 'C']      # D has no local citations
    assert {n['id']: n['lcs'] for n in hist['nodes']} == {'A': 2, 'B': 1, 'C': 1}
    assert sorted(hist['edges']) == [('A', 'B'), ('A', 'C'), ('B', 'C')]


def test_rpys_tool_prefers_full_view_years(tmp_path):
    import asyncio
    import os
    from unittest.mock import AsyncMock, MagicMock, patch
    client = MagicMock()
    client.search_all = AsyncMock(return_value={'search-results': {'entry': [
        {'dc:identifier': 'SCOPUS_ID:1', 'dc:title': 'a', 'prism:coverDate': '2010-01-01'},
        {'dc:identifier': 'SCOPUS_ID:2', 'dc:title': 'b', 'prism:coverDate': '2012-01-01'}]}})
    undated = {'abstracts-retrieval-response': {'references': {'@total-references': '1', 'reference': [
        {'@id': '1', 'scopus-id': 'OV', 'title': 'OV'}]}}}
    client.get_references = AsyncMock(return_value=undated)       # REF view: no years
    client.get_bibliography = AsyncMock(side_effect=lambda sid: [
        {'@id': '1', 'scopus-id': 'OV', 'title': 'OV', 'prism:coverDate': '1997-01-01',
         'author-list': {'author': [{'ce:indexed-name': 'Swanson E.B.'}]}}] if sid == '1' else None)
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy', 'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}):
        from scopus_mcp import server
        with patch.object(server, 'client', client):
            loop = asyncio.new_event_loop()
            text = loop.run_until_complete(server.handle_call_tool(
                'rpys', {'ids': ['1', '2'], 'from_year': 1990, 'to_year': 2000}))[0].text
            loop.close()
    # Paper 1's reference is dated from the FULL view; paper 2 falls back to
    # the undated REF-view entry.
    assert 'RPYS of 2 papers (scopus): 1 cited references dated 1990-2000; 1 without a year.' in text
    assert '  1997: n=1, +1 — Swanson: OV (1)' in text
