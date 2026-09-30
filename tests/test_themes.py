"""Thematic evolution (offline)."""
from scopus_mcp.themes import (
    evolution,
    fates,
    make_periods,
    normalize_term,
    period_themes,
    phrases,
    track_construct,
)


def test_normalize_term():
    assert normalize_term('Organising Visions') == 'organizing vision'
    assert normalize_term('IT-fashion') == 'it fashion'
    assert normalize_term('Institutionalisation') == 'institutionalization'
    assert normalize_term('Analysis') == 'analysis'
    assert normalize_term('Technologies') == 'technology'


def test_phrases_skip_boilerplate_and_copyright():
    text = ("This paper examines organizing visions in IT innovation. "
            "© 2009 Palgrave Macmillan. All rights reserved.")
    got = phrases(text)
    assert 'organizing vision' in got and 'it innovation' in got
    assert 'visions in it' not in got
    assert 'adoption and diffusion' in phrases('Studies of adoption and diffusion abound.')
    assert not any('palgrave' in t or 'reserved' in t or t.startswith('paper') for t in got)


def test_periods_by_cut_years_and_by_size():
    years = [1997, 1999, 2003, 2006, 2008, 2010, 2014, 2019, 2021]
    assert make_periods(years, [2005, 2013]) == [(1997, 2004), (2005, 2012), (2013, 2021)]
    assert make_periods(years, n_periods=3) == [(1997, 2005), (2006, 2013), (2014, 2021)]
    assert make_periods(years, [1990, 2030]) == [(1997, 2021)]    # cuts outside the range ignored


def _docs(groups):
    out, i = [], 0
    for terms, n in groups:
        for _ in range(n):
            out.append({'id': str(i), 'terms': list(terms)})
            i += 1
    return out


def test_two_themes_and_their_strategic_positions():
    docs = _docs([(('organizing vision', 'discourse', 'legitimacy'), 4),
                  (('it fashion', 'bandwagon', 'management fashion'), 3),
                  (('organizing vision', 'it fashion'), 1)])
    p = period_themes(docs, min_freq=2)
    labels = [set(t['terms']) for t in p['themes']]
    assert {'organizing vision', 'discourse', 'legitimacy'} in labels
    assert {'it fashion', 'bandwagon', 'management fashion'} in labels
    assert all(t['quadrant'] in ('motor', 'basic', 'niche', 'emerging or declining') for t in p['themes'])
    assert p['themes'][0]['n_docs'] == 5      # the organizing-vision theme, incl. the bridging paper


def test_construct_kept_even_when_ubiquitous():
    docs = _docs([(('organizing vision', 'discourse'), 8), (('organizing vision', 'erp'), 4)])
    assert 'organizing vision' not in period_themes(docs)['frequency'] or \
        not any('organizing vision' in t['terms'] for t in period_themes(docs)['themes'])
    kept = period_themes(docs, keep_terms=['organizing vision'])
    assert any('organizing vision' in t['terms'] for t in kept['themes'])


def test_evolution_fates_and_construct_track():
    p1 = period_themes(_docs([(('organizing vision', 'discourse'), 3), (('erp', 'adoption'), 3)]))
    p2 = period_themes(_docs([(('organizing vision', 'digital infrastructure'), 3),
                              (('discourse', 'blogging'), 3)]))
    for p, years, n in ((p1, (1997, 2004), 6), (p2, (2005, 2012), 6)):
        p.update({'years': years, 'n_docs': n})
    links = evolution([p1, p2])
    ov1 = next(t['id'] for t in p1['themes'] if 'organizing vision' in t['terms'])
    assert sorted((l['from'], len(l['shared'])) for l in links if l['from'] == ov1) == [(ov1, 1), (ov1, 1)]
    f = fates([p1, p2], links)
    assert f[(0, ov1)] == 'splits'
    erp = next(t['id'] for t in p1['themes'] if 'erp' in t['terms'])
    assert f[(0, erp)] == 'vanishes'
    track = track_construct([p1, p2], ['Organizing Visions'])
    assert [c['doc_mentions'] for c in track] == [3, 3]
    assert track[1]['co_occurs_with'] == ['digital infrastructure']
