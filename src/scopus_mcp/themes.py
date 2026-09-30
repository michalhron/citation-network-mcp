"""Thematic evolution of a corpus (after Cobo et al. 2011 and bibliometrix's
thematicMap / thematicEvolution).

Per period: a keyword co-occurrence network, normalised by the equivalence
index e_ij = c_ij^2 / (c_i c_j), clustered with Louvain. Each cluster (a
theme) gets Callon's centrality (strength of its links to other themes) and
density (strength of its internal links), which place it in the strategic
diagram: motor (central, dense), basic (central, loose), niche (peripheral,
dense), emerging or declining (peripheral, loose). Themes in consecutive
periods are linked by the inclusion index |A n B| / min(|A|, |B|) of their
keyword sets, which shows continuation, splits, merges, new and vanished
themes. A construct's keyword can be followed through the periods: which
theme holds it, where that theme sits, and which keywords it keeps company
with.
"""
import itertools
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence

# British spellings folded into American ones, so "organising vision" and
# "organizing vision" count as one keyword.
_ISE_STEMS = (
    'organis', 'theoris', 'legitimis', 'institutionalis', 'digitalis', 'conceptualis',
    'standardis', 'categoris', 'recognis', 'realis', 'optimis', 'mobilis', 'commercialis',
    'globalis', 'personalis', 'customis', 'virtualis', 'modernis', 'rationalis',
    'prioritis', 'characteris', 'utilis', 'specialis', 'centralis', 'decentralis',
    'formalis', 'normalis', 'operationalis', 'contextualis', 'servitis', 'platformis',
    'materialis', 'socialis', 'visualis',
)
_ISE = re.compile(r'\b(' + '|'.join(_ISE_STEMS) + r')(e|ed|es|ing|ation|ations)\b')

STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had
has have having he her here hers him his how i if in into is it its itself just me more most my
no nor not now of off on once only or other our ours out over own same she should so some such
than that the their theirs them then there these they this those through to too under until up
very was we were what when where which while who whom why will with would you your yours
study paper research article approach results findings using based use used new however
through toward towards within across among via thus therefore whereas furthermore moreover
suggest suggests examine examines examined show shows shown find finds found identify identifies
provide provides provided propose proposes develop develops developed present presents
discuss discusses explore explores explored argue argues offer offers draw draws
one two three four five first second third several various different many much well
also overall important particular implication implications contribution contributions
literature paper papers purpose design methodology originality value limitations
right rights reserved copyright elsevier ltd inc palgrave macmillan taylor francis wiley
sage publishing published springer emerald informs association society journal
year years period around positively negatively associated make made take taken
""".split())
_COPYRIGHT = re.compile(r'(©|\(c\)|copyright).*$|all rights reserved.*$', re.IGNORECASE | re.MULTILINE)


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 4 and word.endswith('s') and not word.endswith(('ss', 'is', 'us', 'ics', 'sis')):
        return word[:-1]
    return word


def normalize_term(term: str) -> str:
    """'Organising Visions' -> 'organizing vision'; 'IT-fashion' -> 'it fashion'."""
    t = str(term).lower().replace('-', ' ').replace('/', ' ')
    t = re.sub(r'[^\w\s]', ' ', t)
    t = _ISE.sub(lambda m: m.group(1)[:-1] + 'z' + m.group(2), t)
    words = t.split()
    if not words:
        return ''
    words[-1] = _singular(words[-1])
    return ' '.join(words)


SHORT_ACRONYMS = {'it', 'ai', 'ar', 'vr', 'ml', 'hr', 'ux', 'ui', 'bi'}


def _too_short(word: str) -> bool:
    return len(word.rstrip('_')) < 3 and word.rstrip('_') not in SHORT_ACRONYMS


def phrases(text: str, max_n: int = 3, min_n: int = 2) -> List[str]:
    """Candidate terms from free text: 2- to 3-word phrases that neither
    start nor end with a stopword, after dropping copyright lines.
    Single words are too generic to separate themes."""
    text = _COPYRIGHT.sub(' ', str(text or ''))
    # Upper-case acronyms ("IT", "AI") are not the pronoun "it": mark them
    # before lower-casing, unmark after.
    text = re.sub(r'\b(IT|AI|AR|VR|ML|HR|UX|UI|BI)\b', lambda m: m.group(1) + '_', text)
    out = set()
    for chunk in re.split(r'[.;:!?()\[\]]', text.lower()):   # phrases do not cross clauses
        tokens = re.findall(r"[a-z][a-z_\-']+", chunk)
        for n in range(min_n, max_n + 1):
            for i in range(len(tokens) - n + 1):
                gram = tokens[i:i + n]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS or \
                        _too_short(gram[0]) or _too_short(gram[-1]):
                    continue
                if n == 3 and gram[1] in STOPWORDS and gram[1] not in ('of', 'and', 'for'):
                    continue  # "visions in IT" is not a term; "diffusion of innovation" is
                out.add(normalize_term(' '.join(gram)).replace('_', ''))
    return sorted(t for t in out if t)


def make_periods(years: Sequence[int], cut_years: Optional[Sequence[int]] = None,
                 n_periods: int = 3) -> List[tuple]:
    """[(first_year, last_year)]. cut_years [2005, 2012] -> up to 2004,
    2005-2011, 2012 on; otherwise n_periods with similar paper counts."""
    ys = sorted(int(y) for y in years)
    if not ys:
        return []
    if cut_years:
        cuts = sorted(int(c) for c in cut_years if ys[0] < int(c) <= ys[-1])
    else:
        n = max(1, min(int(n_periods), len(set(ys))))
        cuts = []
        for k in range(1, n):
            y = ys[round(k * len(ys) / n)]
            if y > ys[0] and y not in cuts and (not cuts or y > cuts[-1]):
                cuts.append(y)
    bounds, start = [], ys[0]
    for c in cuts:
        bounds.append((start, c - 1))
        start = c
    bounds.append((start, ys[-1]))
    return bounds


def _louvain(graph, seed: int = 42):
    from networkx.algorithms.community import louvain_communities
    return louvain_communities(graph, weight='weight', seed=seed)


def period_themes(docs: List[Dict[str, Any]], min_freq: int = 2, max_terms: int = 150,
                  max_share: float = 0.5, keep_terms: Sequence[str] = ()) -> Dict[str, Any]:
    """Themes of one period. docs: [{'id', 'terms': [normalised terms]}].
    Terms in more than max_share of the period's papers are too generic to
    separate themes and are left out (with at least 10 papers), except
    terms containing one of keep_terms."""
    import networkx as nx
    freq = Counter(t for d in docs for t in set(d['terms']))
    ceiling = max_share * len(docs) if len(docs) >= 10 else float('inf')
    vocab = [t for t, c in freq.most_common() if min_freq <= c <= ceiling][:max_terms]
    # A followed construct stays in, however common: it is the point of the run.
    vocab += [t for t in freq if t not in vocab and freq[t] >= min_freq
              and any(k and k in t for k in keep_terms)]
    keep = set(vocab)
    co = Counter()
    for d in docs:
        ts = sorted(set(d['terms']) & keep)
        for a, b in itertools.combinations(ts, 2):
            co[(a, b)] += 1
    g = nx.Graph()
    g.add_nodes_from(vocab)
    for (a, b), c in co.items():
        g.add_edge(a, b, weight=c * c / (freq[a] * freq[b]), count=c)
    connected = [n for n in g if g.degree(n) > 0]
    communities = _louvain(g.subgraph(connected)) if connected else []
    themes = []
    for comm in communities:
        terms = sorted(comm, key=lambda t: (-freq[t], t))
        internal = [d['weight'] for a, b, d in g.subgraph(comm).edges(data=True)]
        external = sum(d['weight'] for a, b, d in g.edges(comm, data=True)
                       if (a in comm) != (b in comm))
        n_docs = sum(1 for d in docs if set(d['terms']) & set(comm))
        themes.append({
            'label': ' · '.join(terms[:3]),
            'terms': terms,
            'frequencies': {t: freq[t] for t in terms},
            'n_docs': n_docs,
            'centrality': round(10 * external, 3),
            'density': round(100 * (sum(internal) / len(internal) if internal else 0), 3),
        })
    if themes:
        c_med = sorted(t['centrality'] for t in themes)[len(themes) // 2]
        d_med = sorted(t['density'] for t in themes)[len(themes) // 2]
        for t in themes:
            hi_c, hi_d = t['centrality'] >= c_med, t['density'] >= d_med
            t['quadrant'] = ('motor' if hi_c and hi_d else 'basic' if hi_c else
                             'niche' if hi_d else 'emerging or declining')
    themes.sort(key=lambda t: (-t['n_docs'], t['label']))
    for i, t in enumerate(themes, start=1):
        t['id'] = i
    return {'themes': themes, 'n_terms': len(vocab), 'co_occurrence': co, 'frequency': freq,
            'doc_terms': [set(d['terms']) for d in docs]}


def evolution(periods: List[Dict[str, Any]], min_inclusion: float = 0.1) -> List[Dict[str, Any]]:
    """Links between themes of consecutive periods by the inclusion index."""
    links = []
    for i in range(len(periods) - 1):
        for a in periods[i]['themes']:
            for b in periods[i + 1]['themes']:
                shared = set(a['terms']) & set(b['terms'])
                if not shared:
                    continue
                inc = len(shared) / min(len(a['terms']), len(b['terms']))
                if inc >= min_inclusion:
                    links.append({'from_period': i, 'from': a['id'], 'from_label': a['label'],
                                  'to_period': i + 1, 'to': b['id'], 'to_label': b['label'],
                                  'inclusion': round(inc, 2),
                                  'shared': sorted(shared, key=lambda t: -a['frequencies'].get(t, 0))[:6]})
    return links


def fates(periods: List[Dict[str, Any]], links: List[Dict[str, Any]]) -> Dict[tuple, str]:
    """(period, theme id) -> 'continues', 'splits', 'merges into', 'vanishes', 'new', 'merged from'."""
    out = {}
    succ, pred = defaultdict(list), defaultdict(list)
    for l in links:
        succ[(l['from_period'], l['from'])].append((l['to_period'], l['to']))
        pred[(l['to_period'], l['to'])].append((l['from_period'], l['from']))
    for i, p in enumerate(periods):
        for t in p['themes']:
            key = (i, t['id'])
            notes = []
            if i > 0:
                notes.append('new' if not pred[key] else 'merged from several' if len(pred[key]) > 1 else '')
            if i < len(periods) - 1:
                s = succ[key]
                notes.append('vanishes' if not s else 'splits' if len(s) > 1 else
                             'merges' if len(pred[s[0]]) > 1 else 'continues')
            out[key] = ', '.join(n for n in notes if n)
    return out


def track_construct(periods: List[Dict[str, Any]], construct_terms: Sequence[str],
                    top: int = 6) -> List[Dict[str, Any]]:
    """Per period: the keywords containing the construct, the theme(s)
    holding them, and the keywords they co-occur with most."""
    wanted = [normalize_term(t) for t in construct_terms if t]
    rows = []
    for i, p in enumerate(periods):
        hits = [t for t in p['frequency'] if any(w and w in t for w in wanted)]
        themes = [th for th in p['themes'] if set(th['terms']) & set(hits)]
        company = Counter()
        for (a, b), c in p['co_occurrence'].items():
            if a in hits and b not in hits:
                company[b] += c
            elif b in hits and a not in hits:
                company[a] += c
        n_docs = sum(1 for terms in p.get('doc_terms') or [] if terms & set(hits))
        rows.append({'period': i, 'years': p['years'], 'keywords': hits,
                     'doc_mentions': n_docs, 'n_docs': p['n_docs'],
                     'themes': [{'id': th['id'], 'label': th['label'], 'quadrant': th.get('quadrant')}
                                for th in themes],
                     'co_occurs_with': [t for t, _ in company.most_common(top)]})
    return rows


def render_strategic_png(periods: List[Dict[str, Any]], path, title: str,
                         highlight: Sequence[str] = ()) -> Optional[str]:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except Exception:
        return None
    n = len(periods)
    fig, axes = plt.subplots(1, n, figsize=(5.2 * n, 5), squeeze=False)
    colors = {'motor': '#b45309', 'basic': '#2563eb', 'niche': '#059669',
              'emerging or declining': '#6b7280'}
    for ax, p in zip(axes[0], periods):
        themes = p['themes']
        if themes:
            cs = [t['centrality'] for t in themes]
            ds = [t['density'] for t in themes]
            c_med = sorted(cs)[len(cs) // 2]
            d_med = sorted(ds)[len(ds) // 2]
            big = max(t['n_docs'] for t in themes) or 1
            for t in themes:
                ax.scatter(t['centrality'], t['density'], s=80 + 900 * t['n_docs'] / big,
                           color=colors.get(t.get('quadrant'), '#6b7280'), alpha=0.55,
                           edgecolors='white')
                marked = any(h and h in term for h in highlight for term in t['terms'])
                shown = t['terms'][:2]
                if marked:
                    hit = next(term for term in t['terms'] if any(h and h in term for h in highlight))
                    shown = ['★ ' + hit] + [x for x in t['terms'] if x != hit][:2]
                ax.annotate('\n'.join(shown), (t['centrality'], t['density']), ha='center',
                            va='center', fontsize=7, fontweight='bold' if marked else 'normal')
            ax.axvline(c_med, color='#999', lw=0.6, ls='--')
            ax.axhline(d_med, color='#999', lw=0.6, ls='--')
        ax.set_title(f"{p['years'][0]}–{p['years'][1]} ({p['n_docs']} papers)", fontsize=9)
        ax.set_xlabel('centrality (relevance)', fontsize=8)
        ax.set_ylabel('density (development)', fontsize=8)
        ax.tick_params(labelsize=7)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
    fig.suptitle(title + '  —  orange: motor · blue: basic · green: niche · grey: emerging or declining'
                 + ('  ·  ★ followed construct' if highlight else ''),
                 fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)
