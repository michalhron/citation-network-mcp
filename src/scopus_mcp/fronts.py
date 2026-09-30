"""Research fronts: communities of the within-set citation network (as in
CitNetExplorer), labelled by the keywords that distinguish them, and the
route a main path takes across them."""
import math
from collections import Counter
from typing import Any, Dict, List, Sequence

from .graphs import _make_node_label


def cluster_citation_network(nodes: Dict[str, Dict[str, Any]], resolution: float = 1.0,
                             min_size: int = 3, seed: int = 42) -> Dict[str, Any]:
    """Louvain communities of the undirected direct-citation network.
    Communities smaller than min_size, and papers with no edge, are
    'unclustered' (front 0)."""
    import networkx as nx
    from networkx.algorithms.community import louvain_communities
    g = nx.Graph()
    g.add_nodes_from(nodes)
    for key, n in nodes.items():
        for p in n.get('parents') or []:
            if p in nodes and p != key:
                g.add_edge(key, p)
    connected = [k for k in g if g.degree(k) > 0]
    comms = louvain_communities(g.subgraph(connected), resolution=resolution, seed=seed) if connected else []
    comms = sorted((c for c in comms if len(c) >= min_size), key=lambda c: (-len(c), min(c)))
    front_of = {k: 0 for k in nodes}
    for i, c in enumerate(comms, start=1):
        for k in c:
            front_of[k] = i
    return {'front_of': front_of, 'fronts': [sorted(c) for c in comms], 'graph': g}


def local_citations(nodes: Dict[str, Dict[str, Any]]) -> Counter:
    lcs = Counter()
    for n in nodes.values():
        for p in n.get('parents') or []:
            lcs[p] += 1
    return lcs


def label_terms(members: Sequence[str], terms_of: Dict[str, List[str]], top: int = 4,
                min_count: int = 2) -> List[str]:
    """Keywords that distinguish a front: frequent in it and over-represented
    against the whole set (count x log lift)."""
    total = Counter(t for ts in terms_of.values() for t in set(ts))
    n_all = max(1, sum(1 for ts in terms_of.values() if ts))
    inside = Counter(t for m in members for t in set(terms_of.get(m) or []))
    n_in = max(1, sum(1 for m in members if terms_of.get(m)))
    scored = []
    for t, c in inside.items():
        if c < min_count:
            continue
        lift = (c / n_in) / (total[t] / n_all)
        scored.append((c * math.log(1 + lift), t))
    return [t for _, t in sorted(scored, key=lambda x: (-x[0], x[1]))[:top]]


def describe_fronts(nodes, clustering, terms_of, top_papers: int = 3) -> List[Dict[str, Any]]:
    lcs = local_citations(nodes)
    g = clustering['graph']
    out = []
    for i, members in enumerate(clustering['fronts'], start=1):
        years = sorted(int(str(nodes[m].get('year'))[:4]) for m in members
                       if str(nodes[m].get('year') or '')[:4].isdigit())
        sub = g.subgraph(members)
        possible = len(members) * (len(members) - 1) / 2
        out.append({
            'front': i,
            'size': len(members),
            'years': (years[0], years[-1]) if years else None,
            'median_year': years[len(years) // 2] if years else None,
            'density': round(sub.number_of_edges() / possible, 3) if possible else 0,
            'keywords': label_terms(members, terms_of),
            'core_papers': [{'id': m, 'label': _make_node_label(nodes[m], m), 'lcs': lcs[m],
                             'title': nodes[m].get('title')}
                            for m in sorted(members, key=lambda m: (-lcs[m], m))[:top_papers]],
            'members': members,
        })
    return out


def path_route(path: List[str], front_of: Dict[str, int]) -> Dict[str, Any]:
    """The fronts a main path passes through, in order, and its hops."""
    seq = [front_of.get(k, 0) for k in path]
    hops = [(path[i], path[i + 1], seq[i], seq[i + 1])
            for i in range(len(path) - 1) if seq[i] != seq[i + 1]]
    runs = []
    for f in seq:
        if not runs or runs[-1] != f:
            runs.append(f)
    return {'fronts': seq, 'route': runs, 'hops': hops}


def hop_robustness(nodes, hops, resolutions=(0.5, 1.0, 1.5), min_size: int = 3) -> List[int]:
    """For each hop (a, b, ...): at how many of the resolutions a and b
    fall in different fronts. A hop found at every resolution is a
    boundary in the literature, not an artefact of the clustering."""
    splits = [cluster_citation_network(nodes, r, min_size)['front_of'] for r in resolutions]
    return [sum(1 for f in splits if f.get(h[0]) != f.get(h[1])) for h in hops]


def write_pajek_partition(order: List[str], front_of: Dict[str, int], path) -> str:
    """Pajek .clu: one front number per vertex, in the .net vertex order."""
    lines = [f'*Vertices {len(order)}'] + [str(front_of.get(k, 0)) for k in order]
    from pathlib import Path
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return str(path)
