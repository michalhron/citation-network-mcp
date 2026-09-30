"""Seed-level networks (bibliographic coupling, co-citation): edges, GraphML/CSV, PNG."""
import csv
import logging
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from .output import _output_dir, _query_slug

logger = logging.getLogger(__name__)


EDGE_CSV_COLUMNS = ['source', 'target', 'weight', 'cosine']


def _make_node_label(meta: Dict[str, Any], node_id: str = '') -> str:
    """Derive a short readable label for a graph node.

    Priority: 'Surname YYYY' (from ce:indexed-name + year) → title[:40] → node_id.
    """
    creator = (meta.get('creator') or '').strip()
    year = (meta.get('year') or '').strip()
    if creator:
        # ce:indexed-name is 'Surname I.' — first token is the surname
        surname = creator.split()[0].rstrip('.,')
        return f'{surname} {year}' if year else surname
    title = (meta.get('title') or '').strip()
    if title:
        return title[:40]
    return node_id or str(meta.get('id', ''))


def compute_pairwise_edges(
    seed_sets: Dict[str, Set[str]],
    min_shared: int = 2,
) -> List[Dict[str, Any]]:
    """Compute pairwise overlap between seed sets; return a weighted edge list.

    Args:
        seed_sets: mapping of seed_id → set of reference or citing-paper IDs.
                   Seeds with empty sets are silently skipped.
        min_shared: minimum shared items required for an edge to be emitted.

    Returns:
        List of {'source', 'target', 'weight' (int), 'cosine' (float)},
        sorted by weight descending. Each unordered pair (a, b) appears once.
    """
    seeds = [s for s, refs in seed_sets.items() if refs]
    edges = []
    for i in range(len(seeds)):
        for j in range(i + 1, len(seeds)):
            a, b = seeds[i], seeds[j]
            set_a, set_b = seed_sets[a], seed_sets[b]
            shared = len(set_a & set_b)
            if shared < min_shared:
                continue
            denom = math.sqrt(len(set_a) * len(set_b))
            cosine = round(shared / denom, 6) if denom > 0 else 0.0
            edges.append({'source': a, 'target': b, 'weight': shared, 'cosine': cosine})
    edges.sort(key=lambda e: e['weight'], reverse=True)
    return edges


def write_graph_to_disk(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    slug: str,
) -> Dict[str, str]:
    """Write graph data to GraphML + CSV edge list + PNG; return their absolute paths.

    Node dicts: {id, label, title, creator, year, venue} — all except id may be None.
    Edge dicts: {source, target, weight, cosine}

    GraphML carries explicit <key> declarations (label, title, creator, year, venue)
    so it opens cleanly in Gephi and VOSviewer. PNG is rendered via render_graph_png
    alongside the other files; render failures are non-fatal (png_path=None in result).
    Output directory follows the SCOPUS_MCP_OUTPUT_DIR convention.
    """
    out = _output_dir()
    ts = datetime.now().strftime('%Y%m%dT%H%M%S')
    base = f'scopus-{_query_slug(slug)}-{ts}'

    graphml_path = out / f'{base}.graphml'
    csv_path = out / f'{base}-edges.csv'

    # ---- GraphML ----
    root = ET.Element('graphml', {
        'xmlns': 'http://graphml.graphdrawing.org/graphml',
        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
        'xsi:schemaLocation': (
            'http://graphml.graphdrawing.org/graphml '
            'http://graphml.graphdrawing.org/graphml/graphml.xsd'
        ),
    })
    for kid, fname, ffor, ftype in [
        ('d_label',   'label',   'node', 'string'),
        ('d_title',   'title',   'node', 'string'),
        ('d_creator', 'creator', 'node', 'string'),
        ('d_year',    'year',    'node', 'string'),
        ('d_venue',   'venue',   'node', 'string'),
        ('d_weight',  'weight',  'edge', 'double'),
        ('d_cosine',  'cosine',  'edge', 'double'),
    ]:
        ET.SubElement(root, 'key', {
            'id': kid, 'for': ffor,
            'attr.name': fname, 'attr.type': ftype,
        })
    graph_el = ET.SubElement(root, 'graph', {'id': 'G', 'edgedefault': 'undirected'})
    for node in nodes:
        n_el = ET.SubElement(graph_el, 'node', {'id': str(node['id'])})
        for key_id, field in [
            ('d_label', 'label'), ('d_title', 'title'), ('d_creator', 'creator'),
            ('d_year', 'year'), ('d_venue', 'venue'),
        ]:
            val = node.get(field)
            if val is not None:
                d = ET.SubElement(n_el, 'data', {'key': key_id})
                d.text = str(val)
    for idx, edge in enumerate(edges):
        e_el = ET.SubElement(graph_el, 'edge', {
            'id': f'e{idx}',
            'source': str(edge['source']),
            'target': str(edge['target']),
        })
        dw = ET.SubElement(e_el, 'data', {'key': 'd_weight'})
        dw.text = str(edge['weight'])
        dc = ET.SubElement(e_el, 'data', {'key': 'd_cosine'})
        dc.text = str(edge['cosine'])

    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    with graphml_path.open('wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True)

    # ---- CSV edge list ----
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=EDGE_CSV_COLUMNS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(edges)

    # ---- PNG (non-fatal) ----
    png_path = None
    try:
        png_path = render_graph_png(nodes, edges, base)
    except Exception as exc:
        logger.warning(f'write_graph_to_disk: render_graph_png non-fatal error: {exc}')

    return {
        'graphml_path': str(graphml_path),
        'csv_path': str(csv_path),
        'png_path': png_path,
    }


def render_graph_png(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    base_filename: str,
) -> Optional[str]:
    """Render the graph to PNG via networkx + matplotlib (Agg backend).

    Returns the absolute path to the PNG, or None if the graph is empty or
    rendering fails for any reason. Never raises — errors are logged and
    the caller continues with GraphML/CSV as the primary artifacts.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import networkx as nx

        out = _output_dir()
        png_path = out / f'{base_filename}.png'

        G = nx.Graph()
        for node in nodes:
            G.add_node(str(node['id']), label=node.get('label', str(node['id'])))
        for edge in edges:
            G.add_edge(
                str(edge['source']), str(edge['target']),
                weight=float(edge.get('weight', 1)),
            )

        if len(G.nodes) == 0:
            return None

        fig, ax = plt.subplots(figsize=(12, 8))
        pos = nx.spring_layout(G, seed=42, k=2.0 / math.sqrt(max(len(G.nodes), 1)))

        degrees = dict(G.degree())
        node_sizes = [max(200, 150 * degrees.get(n, 1)) for n in G.nodes]

        weights = [G[u][v].get('weight', 1) for u, v in G.edges]
        max_w = max(weights) if weights else 1
        edge_widths = [0.5 + 3.5 * w / max_w for w in weights]

        nx.draw_networkx_nodes(G, pos, node_size=node_sizes, ax=ax, alpha=0.85)
        nx.draw_networkx_edges(G, pos, width=edge_widths, ax=ax, alpha=0.5)
        nx.draw_networkx_labels(
            G, pos,
            labels={n: G.nodes[n]['label'] for n in G.nodes},
            ax=ax, font_size=7,
        )

        ax.set_axis_off()
        plt.tight_layout()
        plt.savefig(str(png_path), dpi=150, bbox_inches='tight')
        plt.close(fig)

        return str(png_path)
    except Exception as exc:
        logger.warning(f'render_graph_png failed (non-fatal): {exc}')
        return None
