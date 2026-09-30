"""Offline tests for the 0.18.0 transmission audit: contexts from full text,
list-citation detection, and the draft edge labels."""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from scopus_mcp.fulltext_contexts import (
    citing_sentences,
    contexts_from_text,
    find_reference_entry,
    is_list_citation,
    split_references,
)
from scopus_mcp.tools import corpus

AUTHOR_YEAR = """Research directions in information systems

Discourses about information technology are not stable. In many cases,
developmental shifts will also be observed over time (Swanson and Ramiller
2004). Other work examines vendors (Wang 2009; Swanson and Ramiller 1997,
2004; Ramiller 2005). Fads are common (Abrahamson 1996).

References

Abrahamson, E. 1996. "Management Fashion," Academy of Management Review (21:1), pp. 254-285.
Swanson, E. B., and Ramiller, N. C. 1997. "The Organizing Vision in Information Systems
Innovation," Organization Science (8:5), pp. 458-474.
Swanson, E. B., and Ramiller, N. C. 2004. "Innovating Mindfully with Information
Technology," MIS Quarterly (28:4), pp. 553-583.
Wang, P. 2009. "Popular Concepts beyond Organizations," JAIS (10:1), pp. 1-30.
"""

NUMERIC = """Introduction
We build specifically on the work of Swanson and Ramiller [36], who point to
the importance of grand ideas. Such ideas travel [12, 36]. Unrelated [3].

REFERENCES
[3] Abrahamson, E. Management fashion. AMR, 1996.
[36] Swanson, E.B., and Ramiller, N.C. The organizing vision in information systems innovation. Organization Science, 8, 5 (1997), 458-474.
"""


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_author_year_contexts():
    body, refs = split_references(AUTHOR_YEAR)
    assert refs.lstrip().startswith('References')
    entry, number = find_reference_entry(refs, ['Swanson', 'Ramiller'], 2004, 'Innovating mindfully with IT')
    assert entry.startswith('Swanson, E. B., and Ramiller, N. C. 2004.') and number is None
    sentences = citing_sentences(body, ['Swanson', 'Ramiller'], 2004)
    assert sentences == [
        'In many cases, developmental shifts will also be observed over time (Swanson and Ramiller 2004).',
        'Other work examines vendors (Wang 2009; Swanson and Ramiller 1997, 2004; Ramiller 2005).',
    ]
    assert not is_list_citation(sentences[0], ['Swanson'], 2004)
    assert is_list_citation(sentences[1], ['Swanson'], 2004)


def test_numeric_contexts():
    out = contexts_from_text(NUMERIC, {'surnames': ['Swanson', 'Ramiller'], 'year': 1997,
                                       'title': 'The organizing vision in information systems innovation'})
    assert out['reference_number'] == '36' and out['reason'] is None
    assert out['contexts'] == [
        'We build specifically on the work of Swanson and Ramiller [36], who point to the importance of grand ideas.',
        'Such ideas travel [12, 36].']


def test_leaked_bibliography_is_not_a_context():
    body = ("As theorized by Swanson and Ramiller (1997), an organizing vision is discursively produced. "
            "Wynn IT Innovation for Adaptability and Competitiveness 2004 Kluwer Academic Publishers "
            "267 287 Swanson and Ramiller, 1997 E.B. Swanson N.C. Ramiller The organizing vision.")
    assert citing_sentences(body, ['Swanson', 'Ramiller'], 1997) == [
        'As theorized by Swanson and Ramiller (1997), an organizing vision is discursively produced.']


def test_missing_reference_is_explained():
    out = contexts_from_text(NUMERIC, {'surnames': ['Zmud'], 'year': 1984, 'title': 'Push pull'})
    assert out['contexts'] == [] and out['reason'] == 'cited work not found in the reference list'


def test_fulltext_fallback_when_s2_has_nothing():
    async def s2_nothing(pairs, terms, max_contexts):
        for p in pairs:
            p['context'] = {'status': 'edge_absent_in_s2', 'cited_surnames': []}
        return pairs
    srv = MagicMock()
    srv.client.get_sciencedirect_fulltext = AsyncMock(return_value=None)
    srv.fetch_oa_fulltext = AsyncMock(return_value={'text': AUTHOR_YEAR})
    srv.openalex.get_work = AsyncMock(side_effect=lambda doi: {
        '10.17705/1jais.00150': {'title': 'Research directions', 'publication_year': 2008,
                                 'authorships': []},
        '10.2307/25148655': {'title': 'Innovating Mindfully with Information Technology',
                             'publication_year': 2004,
                             'authorships': [{'author': {'display_name': 'E. Burton Swanson'}},
                                             {'author': {'display_name': 'Neil C. Ramiller'}}]}}[doi])
    with patch.object(corpus, 'server_module', return_value=srv), \
         patch.object(corpus, 'citation_contexts', new=s2_nothing):
        out = _run(corpus.gather_contexts([{'citing': '10.17705/1jais.00150',
                                            'cited': '10.2307/25148655'}], ['organizing vision'], 3))
    c = out[0]['context']
    assert c['status'] == 'found' and c['context_source'] == 'fulltext_oa'
    assert c['s2_status'] == 'edge_absent_in_s2'
    assert c['contexts'][0].endswith('(Swanson and Ramiller 2004).')


def test_construct_must_belong_to_the_cited_work():
    from scopus_mcp.fulltext_contexts import construct_near_marker
    kohli = ("For example, environmental factors have been examined from various perspectives, "
             "including institutional factors (King et al., 1994), standards (Yoo et al., 2005), "
             "fashions (Wang, 2010), and organizing visions (Ramiller & Swanson, 2003).")
    assert not construct_near_marker(kohli, ['Wang'], 2010, ['organizing vision'])
    assert construct_near_marker(kohli, ['Ramiller', 'Swanson'], 2003, ['organizing vision'])
    assert is_list_citation(kohli, ['Wang'], 2010)          # an enumeration of four works
    narrative = ("As theorized by Swanson and Ramiller (1997), an organizing vision is discursively "
                 "produced within a broader institutional community (DiMaggio and Powell, 1983).")
    assert construct_near_marker(narrative, ['Swanson', 'Ramiller'], 1997, ['organizing vision'])
    numeric = "We build on Swanson and Ramiller [36], who point to organizing visions in IS."
    assert construct_near_marker(numeric, ['Swanson'], 1997, ['organizing vision'], number='36')
    assert construct_near_marker(numeric, ['Swanson'], 1997, ['organizing vision'])  # no number known


def test_draft_labels():
    from scopus_mcp.tools.transmission import draft_label
    terms = ['organizing vision']
    cited = {'surnames': ['Wang'], 'year': 2010}
    kohli = {'contexts': [
        "including institutional factors (King et al., 1994), standards (Yoo et al., 2005), "
        "fashions (Wang, 2010), and organizing visions (Ramiller & Swanson, 2003).",
        "Moreover, this focus represents a form of inoculation against following IT fashions (Wang, 2010)."],
        'intents': ['background'], 'is_influential': False}
    assert draft_label(kohli, terms, cited)['label'] == 'hollow'
    engaged = {'contexts': ["We build on the organizing vision of Wang (2010) to code our cases."],
               'intents': ['methodology'], 'is_influential': True}
    assert draft_label(engaged, terms, cited)['label'] == 'substantive'
    shifted = {'contexts': ["IT fashion shapes adoption (Wang, 2010).", "Fashion waves recur (Wang 2010)."],
               'intents': ['background'], 'is_influential': True}
    assert draft_label(shifted, terms, cited)['label'] == 'construct-shifted'
    assert draft_label({'contexts': [], 'status': 'edge_absent_in_s2'}, terms, cited)['label'] == 'unresolved'


def _call(name, args, client_mock=None, openalex_mock=None):
    with patch.dict(os.environ, {'SCOPUS_API_KEY': 'dummy'}):
        from scopus_mcp import server
    with patch.object(server, 'openalex', openalex_mock or MagicMock()), \
         patch.object(server, 'client', client_mock or MagicMock()):
        return _run(server.handle_call_tool(name, args))[0].text


def test_path_transmission_labels_edges_and_writes_coding_sheet(tmp_path):
    import csv
    import json
    from scopus_mcp.tools import transmission

    async def fake_gather(pairs, terms, max_contexts):
        ctx = {
            ('2', '1'): {'status': 'found', 'context_source': 'semantic_scholar',
                         'intents': ['methodology'], 'is_influential': True,
                         'contexts': ['We build on Swanson and Ramiller [36], who point to organizing visions.']},
            ('3', '2'): {'status': 'edge_absent_in_s2', 'context_source': 'none',
                         'fulltext_note': 'no full text available (not entitled, no open copy)'},
        }
        idents = {'1': {'surnames': ['Swanson', 'Ramiller'], 'year': 1997},
                  '2': {'surnames': ['Ramiller'], 'year': 2003}, '3': {'surnames': ['Wang'], 'year': 2021}}
        return [dict(p, context=ctx[(p['citing'], p['cited'])], citing_ident=idents[p['citing']],
                     cited_ident=idents[p['cited']]) for p in reversed(pairs)]
    corpus_json = tmp_path / 'net.json'
    corpus_json.write_text(json.dumps({'edges': [{'citing': '2', 'cited': '1', 'spc': 664}]}))
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(transmission, 'gather_contexts', new=fake_gather):
        text = _call('path_transmission', {'path_ids': ['1', '2', '3'], 'construct_terms': ['organizing vision'],
                                           'corpus_json': str(corpus_json)})
    assert text.startswith("Transmission audit of a 3-paper path for 'organizing vision': "
                           "1 substantive, 0 construct-shifted, 0 hollow, 1 unresolved.")
    assert '1. Ramiller 2003 → Swanson 1997 [SPC 664]: SUBSTANTIVE (S2 influential; intents methodology' in text
    assert '2. Wang 2021 → Ramiller 2003: UNRESOLVED' in text
    sheet = next(tmp_path.glob('*-coding.csv'))
    rows = list(csv.DictReader(sheet.open(encoding='utf-8-sig')))
    assert [r['draft_label'] for r in rows] == ['substantive', 'unresolved']
    assert rows[0]['coder_1_label'] == '' and 'coder_2_label' in rows[0]


def test_index_coverage_three_way_overlap(tmp_path):
    from scopus_mcp.tools import transmission
    client = MagicMock()
    client.get_abstract = AsyncMock(return_value={'abstracts-retrieval-response': {'coredata': {
        'prism:doi': '10.1287/orsc.8.5.458', 'dc:title': 'OV', 'prism:coverDate': '1997-01-01'}}})
    client.search_all = AsyncMock(return_value={'search-results': {'entry': [
        {'dc:identifier': 'SCOPUS_ID:1', 'dc:title': 'All three', 'prism:doi': '10.1/a',
         'prism:coverDate': '2005-01-01', 'prism:publicationName': 'MIS Quarterly'},
        {'dc:identifier': 'SCOPUS_ID:2', 'dc:title': 'Scopus only', 'prism:doi': '10.1/b',
         'prism:coverDate': '2021-01-01', 'prism:publicationName': 'MIS Quarterly'}]}})
    oa = MagicMock()
    oa.get_work = AsyncMock(return_value={'id': 'https://openalex.org/W1', 'doi': 'https://doi.org/10.1287/orsc.8.5.458'})
    oa.citing = AsyncMock(return_value=([
        {'id': 'https://openalex.org/W2', 'doi': 'https://doi.org/10.1/a', 'title': 'All three', 'publication_year': 2005},
        {'id': 'https://openalex.org/W3', 'doi': None, 'title': 'No DOI in OpenAlex', 'publication_year': 2004}], {}))
    s2_citers = [{'title': 'All three', 'year': 2005, 'externalIds': {'DOI': '10.1/A'},
                  'publicationVenue': {'issn': '0276-7783'}},
                 {'title': 'No DOI in OpenAlex', 'year': 2004, 'externalIds': {'DOI': '10.1/c'},
                  'publicationVenue': {'issn': '1536-9323'}},
                 {'title': 'Out of scope', 'year': 2010, 'externalIds': {'DOI': '10.9/x'},
                  'publicationVenue': {'issn': '1234-5679'}}]
    with patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': str(tmp_path)}), \
         patch.object(transmission, 'citing_papers', new=AsyncMock(return_value=({'paperId': 'P'}, s2_citers))):
        text = _call('index_coverage', {'seed_ids': ['31512927'], 'scope': 'ais8'}, client, oa)
    assert 'Scopus 2, OpenAlex 2, Semantic Scholar 2 (3 distinct papers).' in text
    assert '  openalex & scopus & S2: 1' in text
    assert '  openalex & S2: 1 — 2004' in text     # matched across indexes by title and year
    assert '  scopus: 1 — 2021 MISQ Scopus only' in text
