"""SPC, SPLC and SPNP traversal weights, backward and global searches, and
robustness across weights (Batagelj 2003; Liu & Lu 2012)."""
from scopus_mcp.lineage import compute_main_path, key_route_paths, main_path_robustness

# A → B → D, A → C → D, B → E (edges point from cited to citing).
DIAMOND = [
    {'scopus_id': 'A', 'parents': []},
    {'scopus_id': 'B', 'parents': ['A']},
    {'scopus_id': 'C', 'parents': ['A']},
    {'scopus_id': 'D', 'parents': ['B', 'C']},
    {'scopus_id': 'E', 'parents': ['B']},
]


def _weights(weight):
    return {(e['source'], e['target']): e['spc_weight']
            for e in compute_main_path(DIAMOND, weight=weight)['edges']}


def test_spc_counts_source_to_sink_paths():
    # Paths: A-B-D, A-B-E, A-C-D.
    assert _weights('spc') == {('A', 'B'): 2, ('A', 'C'): 1, ('B', 'D'): 1,
                               ('C', 'D'): 1, ('B', 'E'): 1}


def test_splc_counts_paths_from_any_paper():
    # B→D also lies on B-D, which starts at B: 2. A→B: 1 x 2 sinks = 2.
    assert _weights('splc') == {('A', 'B'): 2, ('A', 'C'): 1, ('B', 'D'): 2,
                                ('C', 'D'): 2, ('B', 'E'): 2}


def test_spnp_counts_all_node_pairs():
    # A→B links {A} to {B, D, E}: 3 pairs. B→D links {A, B} to {D}: 2.
    assert _weights('spnp') == {('A', 'B'): 3, ('A', 'C'): 2, ('B', 'D'): 2,
                                ('C', 'D'): 2, ('B', 'E'): 2}


def test_backward_path_starts_at_heaviest_sink_edge():
    mp = compute_main_path(DIAMOND, weight='splc')
    assert mp['weight'] == 'splc'
    assert mp['backward_main_path'][0] == 'A' and mp['backward_main_path'][-1] in ('D', 'E')


def test_global_key_routes_take_heaviest_whole_paths():
    edges = [{'source': 'S', 'target': 'X', 'spc_weight': 1},
             {'source': 'S', 'target': 'Y', 'spc_weight': 5},
             {'source': 'X', 'target': 'K', 'spc_weight': 9},
             {'source': 'Y', 'target': 'K', 'spc_weight': 2},
             {'source': 'K', 'target': 'T', 'spc_weight': 3}]
    local = key_route_paths(edges, k=1)
    glob = key_route_paths(edges, k=1, search='global')
    assert local['routes'][0]['path'] == ['S', 'X', 'K', 'T']    # heaviest edge into X is S→X
    assert glob['routes'][0]['path'] == ['S', 'X', 'K', 'T']     # 1 + 9 beats 5 + 2
    edges[1]['spc_weight'] = 20                                 # now S→Y→K (22) beats S→X→K (10)
    assert key_route_paths(edges, k=1, search='global')['routes'][0]['path'][:3] == ['S', 'Y', 'K']
    assert glob['search'] == 'global'


def test_robustness_reports_the_shared_core():
    # A long thin chain versus a hub: the weights disagree about the middle.
    recs = [{'scopus_id': 'R', 'parents': []}] + \
        [{'scopus_id': f'c{i}', 'parents': ['R' if i == 0 else f'c{i - 1}']} for i in range(4)] + \
        [{'scopus_id': 'H', 'parents': ['R']}] + \
        [{'scopus_id': f'h{i}', 'parents': ['H']} for i in range(5)] + \
        [{'scopus_id': 'Z', 'parents': ['c3', 'h0']}]
    rob = main_path_robustness(recs)
    assert set(rob['paths']) == {'spc', 'splc', 'spnp'}
    assert rob['core'][0] == 'R'
    assert all(0 <= j <= 1 for j in rob['jaccard'].values())
    same = main_path_robustness(DIAMOND[:2])
    assert same['identical'] and same['core'] == ['A', 'B']
