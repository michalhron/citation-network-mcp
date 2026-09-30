"""Research fronts (offline)."""
from scopus_mcp.fronts import (
    cluster_citation_network,
    describe_fronts,
    hop_robustness,
    label_terms,
    path_route,
    write_pajek_partition,
)


def _two_fronts():
    """Two dense groups (a*, b*) joined by one citation b0 -> a3."""
    nodes = {}
    for g in 'ab':
        for i in range(5):
            key = f'{g}{i}'
            nodes[key] = {'year': str(2000 + i + (10 if g == 'b' else 0)), 'creator': f'{g.upper()}uthor X.',
                          'parents': [f'{g}{j}' for j in range(i)]}
    nodes['b0']['parents'] = ['a3']
    nodes['loner'] = {'year': '2020', 'parents': []}
    return nodes


def test_two_fronts_and_an_unclustered_paper():
    nodes = _two_fronts()
    c = cluster_citation_network(nodes)
    fa, fb = c['front_of']['a0'], c['front_of']['b4']
    assert fa and fb and fa != fb
    assert all(c['front_of'][f'a{i}'] == fa for i in range(5))
    assert c['front_of']['loner'] == 0


def test_route_and_hops():
    nodes = _two_fronts()
    c = cluster_citation_network(nodes)
    route = path_route(['a0', 'a3', 'b0', 'b4'], c['front_of'])
    assert len(route['route']) == 2 and len(route['hops']) == 1
    assert route['hops'][0][:2] == ('a3', 'b0')
    assert hop_robustness(nodes, route['hops']) == [3]


def test_labels_prefer_distinctive_keywords():
    terms = {'1': ['organizing vision', 'discourse'], '2': ['organizing vision', 'discourse'],
             '3': ['it fashion', 'discourse'], '4': ['it fashion', 'discourse']}
    assert label_terms(['1', '2'], terms, top=1) == ['organizing vision']


def test_describe_and_partition(tmp_path):
    nodes = _two_fronts()
    c = cluster_citation_network(nodes)
    fronts = describe_fronts(nodes, c, {k: [] for k in nodes})
    assert sorted(f['size'] for f in fronts) == [5, 5]
    a_front = next(f for f in fronts if 'a0' in f['members'])
    assert a_front['core_papers'][0]['id'] == 'a0' and a_front['years'] == (2000, 2004)
    assert a_front['density'] == 1.0
    path = write_pajek_partition(['a0', 'loner'], c['front_of'], tmp_path / 'x.clu')
    assert open(path).read().splitlines() == ['*Vertices 2', str(c['front_of']['a0']), '0']
