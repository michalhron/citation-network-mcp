"""Citation lineage: corpus file, search-path-count main path, HTML and PNG renderings."""
import json
import logging
import math
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from .graphs import _make_node_label
from .output import _output_dir, _query_slug

logger = logging.getLogger(__name__)


def write_lineage_to_disk(
    records: List[Dict[str, Any]],
    seed_id: str,
    main_path: Optional[List[str]] = None,
    spc_edges: Optional[List[Dict[str, Any]]] = None,
    base_filename: Optional[str] = None,
) -> str:
    """Write citation lineage corpus to JSON; return absolute path.

    Writes a dict with keys: seed_id, records, main_path, spc_edges.
    Output directory follows the SCOPUS_MCP_OUTPUT_DIR convention.
    """
    out = _output_dir()
    if base_filename:
        json_path = out / f'{base_filename}.json'
    else:
        ts = datetime.now().strftime('%Y%m%dT%H%M%S')
        slug = _query_slug(f'lineage-{seed_id}')
        json_path = out / f'scopus-{slug}-{ts}.json'
    payload = {
        'seed_id': seed_id,
        'records': records,
        'main_path': main_path or [],
        'spc_edges': spc_edges or [],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return str(json_path)


def _rec_key(r: Dict[str, Any]) -> Optional[str]:
    """Stable key for a lineage record: scopus_id, else openalex_id (OpenAlex
    walks), else doi:…"""
    sid = r.get('scopus_id') or r.get('openalex_id')
    if sid:
        return sid
    doi = r.get('doi')
    if doi:
        return f'doi:{doi}'
    return None


def compute_main_path(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute canonical Batagelj (2003) SPC weights and the main path.

    Builds a directed acyclic graph from the ``parents`` field of each record
    (edge direction: parent → child).  Uses Kahn's topological sort so cycles
    are silently excluded rather than crashing.

    SPC is computed with canonical pseudo-terminals (Batagelj 2003 / Hummon &
    Doreian 1989):
      * A virtual pseudo-source ``s`` is connected to every real source (in-degree 0).
      * A virtual pseudo-sink ``t`` is connected from every real sink (out-degree 0).
      * ``n_minus[v]`` = paths from ``s`` to ``v`` (forward DP over topo order).
      * ``n_plus[v]``  = paths from ``v`` to ``t`` (backward DP).
      * ``spc(u,v)``   = ``n_minus[u] * n_plus[v]``.

    This ensures correct weighting on multi-source / multi-sink graphs.

    Edge filtering: skip an edge when ``parent_gen == child_gen`` (both generation
    values known and equal).  Same-generation edges are invalid in a lineage DAG
    and arise from the backward-walk server bug (a gen-N paper may reference another
    gen-N paper).  Edges that span MORE than one generation are legitimate — a
    node's depth depends on its longest/shortest path from the seed, so direct
    edges can skip generation layers.

    Returns a dict:
    {
        'edges':            [{'source': id, 'target': id, 'spc_weight': int}, …],
        'main_path':        [id, …],   # greedy local (start from best source,
                                       # follow max-weight edge)
        'global_main_path': [id, …],  # canonical global: max-sum-weight path
                                       # from any source to any sink via DP
        'note':             str | None,
    }
    """
    # Build node index
    node_map: Dict[str, Dict] = {}
    for r in records:
        k = _rec_key(r)
        if k and k not in node_map:
            node_map[k] = r

    if not node_map:
        return {'edges': [], 'main_path': [], 'global_main_path': [], 'note': 'No nodes in lineage.'}

    # Build adjacency (succ / pred maps)
    succ: Dict[str, List[str]] = {k: [] for k in node_map}
    pred: Dict[str, List[str]] = {k: [] for k in node_map}

    for r in records:
        child = _rec_key(r)
        if not child or child not in node_map:
            continue
        child_gen = node_map[child].get('generation')
        for parent in (r.get('parents') or []):
            if parent in node_map and parent != child:
                parent_gen = node_map[parent].get('generation')
                # Skip same-generation edges only (not multi-generation-spanning edges).
                # A gen-N paper's references can include other gen-N papers (backward-walk
                # bug), producing invalid same-gen edges.  Edges spanning more than one
                # generation are legitimate (a node's depth is longest/shortest-path depth
                # from the seed, so direct edges can cross multiple layer boundaries).
                if child_gen is not None and parent_gen is not None:
                    if parent_gen == child_gen:
                        continue
                if child not in succ[parent]:
                    succ[parent].append(child)
                if parent not in pred[child]:
                    pred[child].append(parent)

    # Kahn's topological sort (cycle-safe — nodes in cycles are silently excluded)
    in_degree = {k: len(pred[k]) for k in node_map}
    queue: deque = deque([k for k in node_map if in_degree[k] == 0])
    topo: List[str] = []
    while queue:
        node = queue.popleft()
        topo.append(node)
        for v in succ[node]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    topo_set = set(topo)

    # Collect valid edges (only between topo nodes, deduped)
    valid_edges: List[tuple] = []
    seen_e: set = set()
    for u in topo:
        for v in succ[u]:
            if v in topo_set and (u, v) not in seen_e:
                seen_e.add((u, v))
                valid_edges.append((u, v))

    if not valid_edges:
        return {'edges': [], 'main_path': [], 'global_main_path': [], 'note': 'No edges in lineage graph.'}

    # Canonical SPC (Batagelj 2003) via pseudo-terminals
    # ------------------------------------------------------------------
    # Identify real sources (no in-edges within topo_set) and real sinks
    real_sources = [k for k in topo if not any(p in topo_set for p in pred[k])]
    real_sinks   = [k for k in topo if not any(s in topo_set for s in succ[k])]

    # n_minus[v] = # distinct paths from pseudo-source to v
    # Forward pass: pseudo-source → real sources (each counts 1)
    n_minus: Dict[str, int] = {}
    for k in topo:
        live_preds = [p for p in pred[k] if p in topo_set]
        if not live_preds:
            # Real source: connected from pseudo-source by a single edge → 1
            n_minus[k] = 1
        else:
            n_minus[k] = sum(n_minus.get(p, 0) for p in live_preds)

    # n_plus[v] = # distinct paths from v to pseudo-sink
    # Backward pass: real sinks → pseudo-sink (each counts 1)
    n_plus: Dict[str, int] = {}
    for k in reversed(topo):
        live_succs = [s for s in succ[k] if s in topo_set]
        if not live_succs:
            # Real sink: connected to pseudo-sink → 1
            n_plus[k] = 1
        else:
            n_plus[k] = sum(n_plus.get(s, 0) for s in live_succs)

    # Edge SPC weights: spc(u,v) = n_minus[u] * n_plus[v]
    edge_weights = [
        {'source': u, 'target': v, 'spc_weight': n_minus[u] * n_plus[v]}
        for u, v in valid_edges
    ]
    spc_map = {(e['source'], e['target']): e['spc_weight'] for e in edge_weights}

    # ------------------------------------------------------------------
    # Greedy local main path (preserved for backward compatibility):
    # start from the source with highest n_plus, follow max-weight edge
    # ------------------------------------------------------------------
    if not real_sources:
        greedy_path: List[str] = []
    else:
        start = max(real_sources, key=lambda s: n_plus.get(s, 0))
        greedy_path = [start]
        visited: set = {start}
        current = start
        while True:
            live_succs = [s for s in succ[current] if s in topo_set]
            if not live_succs:
                break
            best = max(live_succs, key=lambda v: spc_map.get((current, v), 0))
            if best in visited:
                break
            greedy_path.append(best)
            visited.add(best)
            current = best

    # ------------------------------------------------------------------
    # Canonical global main path:
    # longest-weighted path (max sum of SPC edge weights) from any real
    # source to any real sink, computed via DP over topological order.
    # ------------------------------------------------------------------
    # dp_val[v] = best total SPC weight of any path ending at v
    # dp_prev[v] = predecessor of v on that best path
    dp_val: Dict[str, int] = {k: 0 for k in topo}
    dp_prev: Dict[str, Any] = {k: None for k in topo}
    for k in topo:
        live_preds = [p for p in pred[k] if p in topo_set]
        for p in live_preds:
            w = spc_map.get((p, k), 0)
            candidate = dp_val[p] + w
            if candidate > dp_val[k]:
                dp_val[k] = candidate
                dp_prev[k] = p

    # Trace back from the sink with the highest dp_val
    if not real_sinks:
        global_path: List[str] = []
    else:
        end = max(real_sinks, key=lambda s: dp_val.get(s, 0))
        global_path = []
        cur: Any = end
        while cur is not None:
            global_path.append(cur)
            cur = dp_prev[cur]
        global_path.reverse()

    return {
        'edges': edge_weights,
        'main_path': greedy_path,
        'global_main_path': global_path,
        'note': None,
    }


_D3_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Citation Lineage &mdash; __SEED_ID__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #111827; font-family: ui-sans-serif, system-ui, sans-serif; overflow: hidden; color: #e5e7eb; }
svg { width: 100vw; height: 100vh; display: block; }
.link { fill: none; stroke: #374151; stroke-width: 1.3; }
.link.mp { stroke: #f59e0b; stroke-width: 3; }
.node-circle { stroke-width: 1.5; cursor: pointer; }
.node-label { font-size: 9px; fill: #9ca3af; pointer-events: none; text-anchor: middle; }
.gen-guide { stroke: #1f2937; stroke-width: 1; stroke-dasharray: 4 4; }
.gen-tag { fill: #4b5563; font-size: 11px; }
#tooltip {
  position: fixed; background: rgba(17,24,39,0.97); border: 1px solid #374151;
  border-radius: 8px; padding: 12px 14px; max-width: 320px; font-size: 12px;
  pointer-events: none; opacity: 0; transition: opacity 0.1s; z-index: 100; line-height: 1.6;
}
.tt-title { font-weight: 600; color: #f9fafb; margin-bottom: 4px; }
.tt-row { color: #9ca3af; }
.tt-row span { color: #d1d5db; }
#legend {
  position: fixed; bottom: 16px; left: 16px; background: rgba(17,24,39,0.85);
  border: 1px solid #374151; border-radius: 6px; padding: 8px 12px; font-size: 11px;
}
.lrow { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.ldot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
#hint {
  position: fixed; top: 10px; right: 16px; font-size: 11px; color: #4b5563;
}
</style>
</head>
<body>
<div id="tooltip"></div>
<svg id="viz"></svg>
<div id="legend"></div>
<div id="hint">Scroll to zoom &bull; Drag to pan &bull; Hover for details</div>
<script>
const DATA = __DATA__;
const MP_EDGE_IDS = new Set(__MP_EDGE_IDS__);

const GEN_COLORS = [
  "#3b82f6","#ef4444","#10b981","#f59e0b","#8b5cf6","#06b6d4","#f97316","#84cc16",
  "#ec4899","#a3e635"
];
function genColor(g) { return GEN_COLORS[g % GEN_COLORS.length]; }

const W = window.innerWidth, H = window.innerHeight;
const LAYER_GAP = Math.max(90, Math.min(200, (H - 120) / Math.max(DATA.max_gen, 1)));
const PAD_X = 80;

// Group and sort nodes by generation
const byGen = new Map();
DATA.nodes.forEach(n => {
  if (!byGen.has(n.generation)) byGen.set(n.generation, []);
  byGen.get(n.generation).push(n);
});
byGen.forEach(arr => arr.sort((a, b) => b.cited_by_count - a.cited_by_count));

// Assign x/y positions (layered layout)
DATA.nodes.forEach(n => {
  const arr = byGen.get(n.generation);
  const idx = arr.indexOf(n);
  const count = arr.length;
  n.x = PAD_X + (W - PAD_X * 2) * (count > 1 ? idx / (count - 1) : 0.5);
  n.y = 60 + n.generation * LAYER_GAP;
});

const nodeById = new Map(DATA.nodes.map(n => [n.id, n]));

const maxCBC = Math.max(...DATA.nodes.map(n => n.cited_by_count), 1);
function nodeRadius(cbc) {
  return 5 + 23 * Math.log1p(cbc) / Math.log1p(maxCBC);
}

// SVG setup
const svg = d3.select("#viz");
const g = svg.append("g");

const zoom = d3.zoom()
  .scaleExtent([0.1, 8])
  .on("zoom", e => g.attr("transform", e.transform));
svg.call(zoom);

// Generation guide lines
for (let gen = 0; gen <= DATA.max_gen; gen++) {
  const y = 60 + gen * LAYER_GAP;
  g.append("line")
    .attr("class", "gen-guide")
    .attr("x1", 0).attr("x2", W * 2).attr("y1", y).attr("y2", y);
  g.append("text")
    .attr("class", "gen-tag")
    .attr("x", 8).attr("y", y - 5)
    .text("gen " + gen);
}

// Links (regular first, then main-path on top)
function linkPath(d) {
  const s = nodeById.get(d.source), t = nodeById.get(d.target);
  if (!s || !t) return "";
  const sr = nodeRadius(s.cited_by_count), tr = nodeRadius(t.cited_by_count);
  const x1 = s.x, y1 = s.y + sr, x2 = t.x, y2 = t.y - tr;
  const cy = (y1 + y2) / 2;
  return `M${x1},${y1} C${x1},${cy} ${x2},${cy} ${x2},${y2}`;
}

g.selectAll(".link.regular")
  .data(DATA.edges.filter(e => !MP_EDGE_IDS.has(e.id)))
  .enter().append("path")
  .attr("class", "link regular")
  .attr("d", linkPath);

g.selectAll(".link.mp")
  .data(DATA.edges.filter(e => MP_EDGE_IDS.has(e.id)))
  .enter().append("path")
  .attr("class", "link mp")
  .attr("d", linkPath);

// Tooltip
const tip = document.getElementById("tooltip");

// Nodes
const node = g.selectAll(".node")
  .data(DATA.nodes)
  .enter().append("g")
  .attr("class", "node")
  .attr("transform", d => `translate(${d.x},${d.y})`);

const mpSet = new Set(DATA.main_path);

node.append("circle")
  .attr("class", "node-circle")
  .attr("r", d => nodeRadius(d.cited_by_count))
  .attr("fill", d => genColor(d.generation))
  .attr("stroke", d => mpSet.has(d.id) ? "#f59e0b" : "#1f2937")
  .attr("stroke-width", d => mpSet.has(d.id) ? 3 : 1.5)
  .attr("opacity", 0.9)
  .on("mousemove", function(event, d) {
    tip.innerHTML =
      `<div class="tt-title">${d.title}</div>` +
      `<div class="tt-row">Year: <span>${d.year || "?"}</span></div>` +
      `<div class="tt-row">Venue: <span>${d.venue || "?"}</span></div>` +
      `<div class="tt-row">Citations: <span>${d.cited_by_count}</span></div>` +
      `<div class="tt-row">Generation: <span>${d.generation}</span></div>` +
      `<div class="tt-row">ID: <span>${d.id}</span></div>`;
    tip.style.opacity = 1;
    const tx = Math.min(event.clientX + 14, W - 340);
    tip.style.left = tx + "px";
    tip.style.top = Math.max(event.clientY - 10, 0) + "px";
  })
  .on("mouseleave", () => { tip.style.opacity = 0; });

node.append("text")
  .attr("class", "node-label")
  .attr("y", d => nodeRadius(d.cited_by_count) + 13)
  .text(d => d.label);

// Legend
const legendEl = document.getElementById("legend");
let lhtml = "";
for (let gen = 0; gen <= DATA.max_gen; gen++) {
  lhtml += `<div class="lrow"><div class="ldot" style="background:${genColor(gen)}"></div>Gen ${gen}</div>`;
}
lhtml += `<div class="lrow"><div class="ldot" style="background:#f59e0b;border:2px solid #f59e0b"></div>Main path</div>`;
legendEl.innerHTML = lhtml;

// Auto-fit initial view
const xs = DATA.nodes.map(n => n.x);
const ys = DATA.nodes.map(n => n.y);
if (xs.length) {
  const minX = Math.min(...xs) - 60, maxX = Math.max(...xs) + 60;
  const minY = Math.min(...ys) - 60, maxY = Math.max(...ys) + 60;
  const bW = maxX - minX, bH = maxY - minY;
  if (bW > 0 && bH > 0) {
    const scale = Math.min((W / bW) * 0.88, (H / bH) * 0.88, 2);
    const tx = (W - bW * scale) / 2 - minX * scale;
    const ty = (H - bH * scale) / 2 - minY * scale;
    svg.call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }
}
</script>
</body>
</html>"""


def render_lineage_html(
    records: List[Dict[str, Any]],
    main_path: List[str],
    seed_id: str,
    base_filename: str,
) -> Optional[str]:
    """Write a self-contained interactive D3 v7 HTML visualization of the citation lineage.

    Returns the absolute path to the HTML file, or None if rendering fails (non-fatal).
    The file is fully self-contained: D3 loaded from CDN, all data inlined as JSON.
    """
    try:
        out = _output_dir()
        html_path = out / f'{base_filename}.html'

        # Build node data
        nodes_data = []
        nodes_by_key: Dict[str, dict] = {}
        for r in records:
            k = _rec_key(r)
            if not k:
                continue
            cbc = 0
            try:
                cbc = int(r.get('cited_by_count') or 0)
            except (ValueError, TypeError):
                pass
            nd = {
                'id': k,
                'title': r.get('title') or k,
                'year': r.get('year') or '',
                'venue': r.get('venue') or '',
                'cited_by_count': cbc,
                'generation': r.get('generation', 0),
                'label': _make_node_label(r, k),
            }
            nodes_data.append(nd)
            nodes_by_key[k] = nd

        # Build edge data from parents
        edges_data = []
        seen_e: set = set()
        for r in records:
            child_key = _rec_key(r)
            if not child_key or child_key not in nodes_by_key:
                continue
            for parent_key in (r.get('parents') or []):
                if parent_key in nodes_by_key:
                    eid = f'{parent_key}→{child_key}'
                    if eid not in seen_e:
                        seen_e.add(eid)
                        edges_data.append({'source': parent_key, 'target': child_key, 'id': eid})

        # Main-path edge ids for highlight
        mp_edge_ids = []
        for i in range(len(main_path) - 1):
            mp_edge_ids.append(f'{main_path[i]}→{main_path[i + 1]}')

        max_gen = max((nd['generation'] for nd in nodes_data), default=0)

        # Inline data — replace </script with <\/script to prevent tag injection
        def _safe_json(obj: Any) -> str:
            return json.dumps(obj, ensure_ascii=True).replace('</', '<\\/')

        data_payload = {
            'nodes': nodes_data,
            'edges': edges_data,
            'max_gen': max_gen,
            'seed_id': seed_id,
            'main_path': main_path,
        }

        html = _D3_HTML_TEMPLATE \
            .replace('__SEED_ID__', seed_id) \
            .replace('__DATA__', _safe_json(data_payload)) \
            .replace('__MP_EDGE_IDS__', _safe_json(mp_edge_ids))

        html_path.write_text(html, encoding='utf-8')
        return str(html_path)

    except Exception as exc:
        logger.warning(f'render_lineage_html failed (non-fatal): {exc}')
        return None


def render_lineage_png(
    records: List[Dict[str, Any]],
    main_path: List[str],
    seed_id: str,
    base_filename: str,
) -> Optional[str]:
    """Render the citation lineage as a static layered-DAG PNG.

    Uses networkx.multipartite_layout (gen on y-axis), nodes sized by citation
    count, colored by generation.  Labels only the top-3 most-cited nodes per
    generation to keep the image readable.  Returns None on failure (non-fatal).
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import networkx as nx

        out = _output_dir()
        png_path = out / f'{base_filename}.png'

        G = nx.DiGraph()
        node_meta: Dict[str, dict] = {}
        for r in records:
            k = _rec_key(r)
            if not k:
                continue
            gen = r.get('generation', 0)
            cbc = 0
            try:
                cbc = int(r.get('cited_by_count') or 0)
            except (ValueError, TypeError):
                pass
            G.add_node(k, generation=gen)
            node_meta[k] = {
                'gen': gen,
                'cbc': cbc,
                'label': _make_node_label(r, k),
            }

        for r in records:
            child = _rec_key(r)
            if not child or not G.has_node(child):
                continue
            for parent in (r.get('parents') or []):
                if G.has_node(parent):
                    G.add_edge(parent, child)

        if len(G.nodes) == 0:
            return None

        # multipartite_layout: subset_key='generation', align='horizontal' →
        # each generation has the same y coordinate; negate y so gen 0 is at top.
        pos_raw = nx.multipartite_layout(G, subset_key='generation', align='horizontal')
        pos = {k: (x, -y) for k, (x, y) in pos_raw.items()}

        max_cbc = max((node_meta[k]['cbc'] for k in G.nodes), default=1)

        def _node_size(k: str) -> float:
            cbc = node_meta[k]['cbc']
            return max(40, 1800 * math.log1p(cbc) / math.log1p(max(max_cbc, 1)))

        # Generation colours (Set2 palette, wraps for >8 gens)
        palette = plt.cm.Set2.colors  # 8 colours
        node_colors = [palette[node_meta[k]['gen'] % len(palette)] for k in G.nodes]
        node_sizes = [_node_size(k) for k in G.nodes]

        # Main-path edges
        mp_edge_set = set()
        for i in range(len(main_path) - 1):
            mp_edge_set.add((main_path[i], main_path[i + 1]))
        regular_edges = [(u, v) for u, v in G.edges() if (u, v) not in mp_edge_set]
        mp_edges = [(u, v) for u, v in G.edges() if (u, v) in mp_edge_set]

        # Labels: top-3 most-cited per generation
        by_gen: Dict[int, List[str]] = {}
        for k in G.nodes:
            g_idx = node_meta[k]['gen']
            by_gen.setdefault(g_idx, []).append(k)
        labeled: set = set()
        for _, nds in by_gen.items():
            for k in sorted(nds, key=lambda k: node_meta[k]['cbc'], reverse=True)[:3]:
                labeled.add(k)
        labels = {k: node_meta[k]['label'] for k in labeled}

        fig, ax = plt.subplots(figsize=(14, 10))
        fig.patch.set_facecolor('#111827')
        ax.set_facecolor('#111827')

        nx.draw_networkx_nodes(
            G, pos, node_size=node_sizes, node_color=node_colors, ax=ax, alpha=0.9,
        )
        if regular_edges:
            nx.draw_networkx_edges(
                G, pos, edgelist=regular_edges, edge_color='#4b5563',
                ax=ax, arrows=True, arrowsize=10, width=0.8, alpha=0.6,
            )
        if mp_edges:
            nx.draw_networkx_edges(
                G, pos, edgelist=mp_edges, edge_color='#f59e0b',
                ax=ax, arrows=True, arrowsize=15, width=2.5, alpha=0.95,
            )
        if labels:
            nx.draw_networkx_labels(
                G, pos, labels=labels, ax=ax, font_size=7, font_color='white',
            )

        ax.set_axis_off()
        plt.tight_layout()
        plt.savefig(str(png_path), dpi=150, bbox_inches='tight', facecolor='#111827')
        plt.close(fig)

        return str(png_path)

    except Exception as exc:
        logger.warning(f'render_lineage_png failed (non-fatal): {exc}')
        return None
