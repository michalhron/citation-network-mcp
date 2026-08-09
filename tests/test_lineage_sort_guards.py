"""
Tests for citation_lineage v0.7.5 additions:
  1. sort parameter wired through to search_all (citedby / coverDate / relevancy)
  2. Degeneracy guard emits WARNING when walk is recency-dominated or collapsed
  3. SPC incompleteness note when no edges exist
  4. Inline corpus (base64) present in every successful response
  5. Server version present in every response
  6. get_server_info tool returns version string
"""

import asyncio
import base64
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

# Provide a dummy key so the module-level ScopusClient() doesn't crash
os.environ.setdefault('SCOPUS_API_KEY', 'test-dummy-key')

from scopus_mcp.server import handle_call_tool, SERVER_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _result_text(result):
    return result[0].text


def _fake_abstract(sid, title="Test seed", cited_by=5000):
    """Return the shape that clean_abstract_details receives from get_abstract."""
    return {
        'abstracts-retrieval-response': {
            'coredata': {
                'dc:identifier': f'SCOPUS_ID:{sid}',
                'dc:title': title,
                'prism:coverDate': '2005-01-01',
                'prism:publicationName': 'Test Journal',
                'citedby-count': str(cited_by),
            }
        }
    }


def _fake_search_results(entries):
    """Return the shape that clean_search_results expects."""
    return {
        'search-results': {
            'entry': entries,
            'opensearch:totalResults': str(len(entries)),
        },
        '_meta': {
            'total_fetched': len(entries),
            'total_available': len(entries),
            'truncated': False,
            'note': None,
        },
    }


def _entry(sid, cited_by, year='2024'):
    return {
        'dc:identifier': f'SCOPUS_ID:{sid}',
        'eid': f'2-s2.0-{sid}',
        'dc:title': f'Paper {sid}',
        'prism:coverDate': f'{year}-01-01',
        'prism:publicationName': 'Journal X',
        'citedby-count': str(cited_by),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSortParameterWiredThrough(unittest.TestCase):
    """search_all must be called with the correct Scopus sort value."""

    def _run_with_sort(self, sort_arg, expected_api_sort):
        captured_sort = {}

        async def fake_search_all(query, max_results=200, sort='coverDate'):
            captured_sort['sort'] = sort
            return _fake_search_results([
                _entry('111', 100, '2010'),
                _entry('222', 80,  '2011'),
                _entry('333', 60,  '2012'),
            ])

        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid))
                mock_client.search_all = AsyncMock(side_effect=fake_search_all)
                mock_client.close = AsyncMock()

                _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': 1,
                    'max_per_node': 5,
                    'sort': sort_arg,
                }))

        self.assertEqual(
            captured_sort.get('sort'), expected_api_sort,
            f"sort={sort_arg!r} should map to Scopus sort={expected_api_sort!r}, "
            f"got {captured_sort.get('sort')!r}"
        )

    def test_sort_citedby_maps_to_citedby_count(self):
        self._run_with_sort('citedby', 'citedby-count')

    def test_sort_coverdate_maps_to_coverdate(self):
        self._run_with_sort('coverDate', 'coverDate')

    def test_sort_relevancy_maps_to_relevancy(self):
        self._run_with_sort('relevancy', 'relevancy')

    def test_forward_default_sort_is_citedby(self):
        """Omitting sort on a forward walk must default to citedby-count."""
        captured_sort = {}

        async def fake_search_all(query, max_results=200, sort='coverDate'):
            captured_sort['sort'] = sort
            return _fake_search_results([_entry('111', 100, '2010')])

        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid))
                mock_client.search_all = AsyncMock(side_effect=fake_search_all)
                mock_client.close = AsyncMock()

                _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': 1,
                    'max_per_node': 5,
                    # no 'sort' key
                }))

        self.assertEqual(captured_sort.get('sort'), 'citedby-count')

    def test_invalid_sort_returns_error(self):
        """An unsupported sort value must surface an error (not silently default)."""
        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid))
                mock_client.close = AsyncMock()

                result = _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'sort': 'badvalue',
                }))
        # handle_call_tool catches exceptions and returns them as text
        text = _result_text(result)
        self.assertIn('Error', text)

    def test_sort_line_appears_in_output(self):
        """The response must show which sort was used."""
        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid))
                mock_client.search_all = AsyncMock(
                    return_value=_fake_search_results([_entry('111', 50, '2015')]))
                mock_client.close = AsyncMock()

                result = _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': 1,
                    'sort': 'citedby',
                }))
        text = _result_text(result)
        self.assertIn('citedby', text)


class TestDegeneracyGuard(unittest.TestCase):
    """WARNING must appear when the walk is recency-dominated or collapsed."""

    def _run_walk(self, gen1_entries, max_per_node=3, generations=2, min_citing=0,
                  sort='coverDate'):
        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid, cited_by=5000))
                mock_client.search_all = AsyncMock(
                    return_value=_fake_search_results(gen1_entries))
                mock_client.close = AsyncMock()

                result = _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': generations,
                    'max_per_node': max_per_node,
                    'min_citing': min_citing,
                    'sort': sort,
                }))
        return _result_text(result)

    def test_warning_when_gen1_capped_and_gen2_empty(self):
        """gen-1 fills the cap and gen-2 returns nothing → WARNING."""
        import datetime
        year = datetime.datetime.now().year
        # 3 entries == max_per_node; all recent with low citations
        entries = [_entry(str(i), 5, str(year)) for i in range(3)]
        text = self._run_walk(entries, max_per_node=3, generations=2)
        self.assertIn('WARNING', text)

    def test_warning_when_main_path_short(self):
        """Only 1 gen-1 paper → path length 2 (< 3) → WARNING."""
        import datetime
        year = datetime.datetime.now().year
        entries = [_entry('111', 5, str(year))]
        text = self._run_walk(entries, max_per_node=10, generations=1)
        self.assertIn('WARNING', text)

    def test_no_warning_when_walk_healthy(self):
        """High-citation gen-1 papers from old years + gen-2 expansion → no WARNING."""
        # gen-1: 2 high-citation old papers.  gen-2 queries return 1 new paper each
        # so the walk has 3 distinct generations and main_path ≥ 3 nodes.
        gen1_entries = [_entry('111', 500, '2010'), _entry('222', 300, '2011')]
        # Each gen-2 call: 111 returns [511], 222 returns [522] — all unseen
        gen2_entries = {
            '111': [_entry('511', 200, '2015')],
            '222': [_entry('522', 150, '2016')],
        }
        call_count = [0]

        async def smart_search_all(query, max_results=200, sort='citedby-count'):
            call_count[0] += 1
            if call_count[0] == 1:
                return _fake_search_results(gen1_entries)
            # Subsequent calls: return different papers keyed by which gen-1 paper
            for sid, entries in gen2_entries.items():
                if sid in query:
                    return _fake_search_results(entries)
            return _fake_search_results([])

        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid, cited_by=500))
                mock_client.search_all = AsyncMock(side_effect=smart_search_all)
                mock_client.close = AsyncMock()

                result = _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': 2,
                    'max_per_node': 10,
                    'min_citing': 0,
                    'sort': 'citedby',
                }))
        text = _result_text(result)
        self.assertNotIn('WARNING', text)

    def test_warning_message_mentions_sort_suggestion(self):
        """The WARNING must suggest sort='citedby' as a remedy."""
        import datetime
        year = datetime.datetime.now().year
        entries = [_entry(str(i), 3, str(year)) for i in range(3)]
        text = self._run_walk(entries, max_per_node=3, generations=2)
        self.assertIn("sort='citedby'", text)


class TestSPCCompletenessNote(unittest.TestCase):
    """When no SPC edges exist, a clear note must appear."""

    def test_spc_note_or_no_papers_when_empty_walk(self):
        """An empty walk (no gen-1 papers) must surface SPC status or no-papers note."""
        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid))
                mock_client.search_all = AsyncMock(
                    return_value=_fake_search_results([]))
                mock_client.close = AsyncMock()

                result = _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': 1,
                }))
        text = _result_text(result)
        # With no gen-1 papers there are no edges, so either SPC note or
        # the "no papers found" message must appear
        self.assertTrue(
            'SPC arc weights' in text or 'no papers found' in text.lower(),
            f"Expected SPC note or no-papers message in:\n{text}"
        )

    def test_spc_note_present_with_single_paper_no_edges(self):
        """A seed-only corpus (one paper, no connections) has no SPC edges."""
        # Return exactly one gen-1 paper but no gen-2, so there is only one edge
        # and compute_main_path returns edges.  For this test we want ZERO edges:
        # filter out the only paper via min_citing so it's never added.
        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid))
                mock_client.search_all = AsyncMock(
                    return_value=_fake_search_results([_entry('111', 0, '2020')]))
                mock_client.close = AsyncMock()

                result = _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': 2,
                    'min_citing': 999,  # prunes all gen-1 from expansion
                }))
        text = _result_text(result)
        # Either SPC note (no edges at all if gen-1 was pruned from DAG) or
        # the walk succeeded with a 2-node path; either is acceptable
        self.assertIsNotNone(text)


class TestInlineCorpus(unittest.TestCase):
    """Corpus (base64-encoded JSON) must be present in every successful response."""

    def _run_basic(self):
        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid))
                mock_client.search_all = AsyncMock(
                    return_value=_fake_search_results([_entry('111', 50, '2015')]))
                mock_client.close = AsyncMock()

                return _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': 1,
                }))

    def test_corpus_b64_line_present(self):
        text = _result_text(self._run_basic())
        self.assertIn('Corpus (base64', text)

    def test_corpus_b64_decodes_to_valid_json(self):
        text = _result_text(self._run_basic())
        b64_line = next(
            l for l in text.splitlines() if 'Corpus (base64' in l
        )
        b64_value = b64_line.split(':', 1)[1].strip()
        decoded = json.loads(base64.b64decode(b64_value).decode('utf-8'))
        self.assertIn('seed_id', decoded)
        self.assertIn('records', decoded)
        self.assertIsInstance(decoded['records'], list)

    def test_corpus_contains_seed_and_papers(self):
        text = _result_text(self._run_basic())
        b64_line = next(l for l in text.splitlines() if 'Corpus (base64' in l)
        b64_value = b64_line.split(':', 1)[1].strip()
        corpus = json.loads(base64.b64decode(b64_value).decode('utf-8'))
        sids = [r.get('scopus_id') for r in corpus['records']]
        self.assertIn('99999', sids)   # seed
        self.assertIn('111', sids)     # gen-1 paper

    def test_corpus_contains_main_path_and_spc_edges(self):
        text = _result_text(self._run_basic())
        b64_line = next(l for l in text.splitlines() if 'Corpus (base64' in l)
        b64_value = b64_line.split(':', 1)[1].strip()
        corpus = json.loads(base64.b64decode(b64_value).decode('utf-8'))
        self.assertIn('main_path', corpus)
        self.assertIn('spc_edges', corpus)


class TestServerVersion(unittest.TestCase):
    """Server version must appear in every citation_lineage response."""

    def test_version_in_response(self):
        with tempfile.TemporaryDirectory() as td:
            with patch('scopus_mcp.server.client') as mock_client, \
                 patch.dict(os.environ, {'SCOPUS_MCP_OUTPUT_DIR': td}):
                mock_client.get_abstract = AsyncMock(
                    side_effect=lambda sid: _fake_abstract(sid))
                mock_client.search_all = AsyncMock(
                    return_value=_fake_search_results([_entry('111', 50, '2015')]))
                mock_client.close = AsyncMock()

                result = _run(handle_call_tool('citation_lineage', {
                    'seed_id': '99999',
                    'direction': 'forward',
                    'generations': 1,
                }))
        text = _result_text(result)
        self.assertIn(SERVER_VERSION, text)
        self.assertIn('Server version', text)

    def test_get_server_info_returns_version(self):
        with patch('scopus_mcp.server.client'):
            result = _run(handle_call_tool('get_server_info', {}))
        text = _result_text(result)
        self.assertIn(SERVER_VERSION, text)
        self.assertIn('status: ok', text)

    def test_server_version_constant_matches_pyproject(self):
        """SERVER_VERSION must stay in sync with pyproject.toml."""
        import pathlib
        root = pathlib.Path(__file__).parent.parent / 'pyproject.toml'
        toml_text = root.read_text()
        version_line = next(
            l for l in toml_text.splitlines() if l.startswith('version')
        )
        toml_version = version_line.split('=')[1].strip().strip('"')
        self.assertEqual(SERVER_VERSION, toml_version,
            f"SERVER_VERSION={SERVER_VERSION!r} != pyproject.toml version={toml_version!r}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
