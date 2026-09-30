"""Historical views of a corpus: Reference Publication Year Spectroscopy
(RPYS) and Garfield's historiograph.

RPYS (Marx, Bornmann, Barth & Leydesdorff 2014) counts the cited references
of a corpus by the year they were published and subtracts the five-year
median; peaks mark the years whose works the field treats as its roots,
and the works cited most in a peak year are those roots.

The historiograph (Garfield 2004, HistCite; bibliometrix's histNetwork)
draws the papers most cited within the set on a time axis, with the
citations among them.
"""
import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from .graphs import _make_node_label
from .openalex import normalize_title


def cited_work_key(ref: Dict[str, Any]) -> Optional[str]:
    """One key per cited work across citing papers: Scopus ID, DOI, else
    first author plus title."""
    if ref.get('scopus_id'):
        return f"sid:{ref['scopus_id']}"
    if ref.get('openalex_id'):
        return f"oa:{ref['openalex_id']}"
    if ref.get('doi'):
        return f"doi:{ref['doi'].lower()}"
    title = normalize_title(ref.get('title'))
    if title:
        first = (ref.get('authors') or [''])[0].split(' ')[0].lower()
        return f"t:{first}:{title[:80]}"
    return None


def _surname(indexed_name: str) -> str:
    """'DiMaggio P.J.' / 'Di Maggio P.J.' / 'van de Ven A.H.' -> surname."""
    tokens = [t for t in str(indexed_name).split() if not re.fullmatch(r"(?:[A-Z]\.)+(?:-[A-Z]\.)*", t)]
    return ' '.join(tokens) or str(indexed_name)


def cited_work_label(ref: Dict[str, Any]) -> str:
    """'Swanson & Ramiller: The organizing vision ...' (no year: citers
    date the same work differently, and the peak year is shown anyway)."""
    authors = ref.get('authors') or []
    who = _surname(authors[0]) if authors else (ref.get('source') or '?')
    if len(authors) == 2:
        who += f" & {_surname(authors[1])}"
    elif len(authors) > 2:
        who += " et al."
    title = (ref.get('title') or ref.get('source') or '').strip()
    return f"{who}: {title[:90]}".strip()


def spectrogram(refs: List[Dict[str, Any]], from_year: int, to_year: int,
                top_peaks: int = 10, top_works: int = 3) -> Dict[str, Any]:
    """RPYS over cited references ({'year', ...} per citation instance).

    Returns per-year counts, the deviation from the five-year median
    (y-2 .. y+2), the peaks (local maxima of a positive deviation, largest
    first) and, per peak, the works cited most often from that year."""
    per_year: Dict[int, int] = Counter()
    works: Dict[int, Counter] = defaultdict(Counter)
    labels: Dict[str, str] = {}
    undated = 0
    for r in refs:
        y = str(r.get('year') or '')[:4]
        if not y.isdigit():
            undated += 1
            continue
        y = int(y)
        if not from_year <= y <= to_year:
            continue
        per_year[y] += 1
        key = cited_work_key(r)
        if key:
            works[y][key] += 1
            labels.setdefault(key, cited_work_label(r))
    years = list(range(from_year, to_year + 1))
    n = [per_year.get(y, 0) for y in years]
    rows = []
    for i, y in enumerate(years):
        window = n[max(0, i - 2): i + 3]
        med = statistics.median(window)
        rows.append({'year': y, 'n': n[i], 'median': med, 'deviation': n[i] - med})
    dev = [r['deviation'] for r in rows]
    peaks = []
    for i, r in enumerate(rows):
        left = dev[i - 1] if i > 0 else float('-inf')
        right = dev[i + 1] if i + 1 < len(dev) else float('-inf')
        if r['deviation'] > 0 and r['deviation'] >= left and r['deviation'] >= right:
            peaks.append(r)
    peaks.sort(key=lambda r: (-r['deviation'], r['year']))
    peaks = peaks[:top_peaks]
    for p in peaks:
        p['works'] = [{'label': labels[k], 'citations': c}
                      for k, c in works[p['year']].most_common(top_works)]
        p['share_top_work'] = round(p['works'][0]['citations'] / p['n'], 2) if p['works'] and p['n'] else 0
    return {'rows': rows, 'peaks': peaks, 'n_references': sum(n), 'undated': undated,
            'from_year': from_year, 'to_year': to_year}


def render_rpys_png(spec: Dict[str, Any], path, title: str) -> Optional[str]:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except Exception:
        return None
    rows = spec['rows']
    # Plot from where references begin (3+ in a year), not from from_year.
    first = next((i for i, r in enumerate(rows) if r['n'] >= 3), 0)
    rows = rows[max(0, first - 2):]
    years = [r['year'] for r in rows]
    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.bar(years, [r['n'] for r in rows], color='#c7d2e0', width=1.0, label='cited references')
    ax.plot(years, [r['deviation'] for r in rows], color='#b45309', linewidth=1.4,
            label='deviation from 5-year median')
    ax.axhline(0, color='#555', linewidth=0.6)
    for p in spec['peaks'][:6]:
        ax.annotate(str(p['year']), (p['year'], p['deviation']), textcoords='offset points',
                    xytext=(0, 6), ha='center', fontsize=8, color='#b45309')
    ax.set_xlabel('publication year of cited reference')
    ax.set_ylabel('cited references')
    ax.set_title(title, fontsize=10)
    ax.legend(frameon=False, fontsize=8, loc='upper left')
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def historiograph(nodes: Dict[str, Dict[str, Any]], top: int = 30) -> Dict[str, Any]:
    """The `top` papers by local citation score (citations from within the
    set), ties broken by global citations, and the citations among them."""
    lcs: Dict[str, int] = Counter()
    for key, n in nodes.items():
        for parent in n.get('parents') or []:
            lcs[parent] += 1

    def gcs(key):
        try:
            return int(nodes[key].get('cited_by_count') or 0)
        except (TypeError, ValueError):
            return 0
    ranked = sorted(nodes, key=lambda k: (-lcs.get(k, 0), -gcs(k), k))
    chosen = [k for k in ranked if lcs.get(k, 0) > 0][:top] or ranked[:top]
    keep = set(chosen)
    edges = [(p, k) for k in chosen for p in nodes[k].get('parents') or [] if p in keep]
    return {'nodes': [{'id': k, 'label': _make_node_label(nodes[k], k), 'year': nodes[k].get('year'),
                       'lcs': lcs.get(k, 0), 'gcs': gcs(k), 'title': nodes[k].get('title')}
                      for k in sorted(chosen, key=lambda k: (str(nodes[k].get('year') or ''), k))],
            'edges': edges}


def render_historiograph_png(hist: Dict[str, Any], main_path: List[str], path, title: str) -> Optional[str]:
    """Papers on a time axis (x = year), sized by local citations; citations
    drawn from the cited to the citing paper, main-path edges highlighted."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except Exception:
        return None
    nodes = hist['nodes']
    if not nodes:
        return None
    by_year = defaultdict(list)
    for n in nodes:
        by_year[str(n.get('year') or '?')].append(n)
    pos = {}
    for year, group in by_year.items():
        group.sort(key=lambda n: -n['lcs'])
        x = int(year) if year.isdigit() else min(int(y) for y in by_year if y.isdigit()) - 2
        for i, n in enumerate(group):
            pos[n['id']] = (x, (i - (len(group) - 1) / 2) * 1.0)
    on_path = set(zip(main_path, main_path[1:]))
    max_lcs = max(n['lcs'] for n in nodes) or 1
    fig, ax = plt.subplots(figsize=(14, 7))
    for a, b in hist['edges']:
        if a not in pos or b not in pos:
            continue
        hot = (a, b) in on_path
        ax.annotate('', xy=pos[b], xytext=pos[a],
                    arrowprops=dict(arrowstyle='-|>', lw=2.2 if hot else 0.6, alpha=0.9 if hot else 0.35,
                                    color='#d97706' if hot else '#6b7280', shrinkA=6, shrinkB=6,
                                    connectionstyle='arc3,rad=0.08'))
    path_nodes = set(main_path)
    # Labels alternate above and below along each row, so neighbouring
    # years on the same row do not overprint each other.
    rows = defaultdict(list)
    for n in nodes:
        rows[pos[n['id']][1]].append(n)
    flip = {}
    for row in rows.values():
        for i, n in enumerate(sorted(row, key=lambda n: pos[n['id']][0])):
            flip[n['id']] = i % 2 == 1
    for n in nodes:
        x, y = pos[n['id']]
        size = 60 + 540 * n['lcs'] / max_lcs
        ax.scatter([x], [y], s=size, color='#d97706' if n['id'] in path_nodes else '#3b82f6',
                   alpha=0.85, zorder=3, edgecolors='white', linewidths=0.8)
        below = flip[n['id']]
        ax.annotate(n['label'], (x, y), textcoords='offset points', xytext=(0, -13 if below else 9),
                    ha='center', va='top' if below else 'bottom', fontsize=7, zorder=4,
                    bbox=dict(boxstyle='round,pad=0.15', fc='white', ec='none', alpha=0.7))
    ax.set_yticks([])
    for side in ('top', 'right', 'left'):
        ax.spines[side].set_visible(False)
    ax.set_xlabel('publication year')
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)
