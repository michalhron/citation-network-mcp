"""
Journal metrics: parsing for the Scopus Serial Title API and OpenAlex sources.

Two live traps shape the callers (checked 2026-09-30):
- Serial Title cannot look journals up by Scopus source ID. Given one, it
  ignores it and returns the first 25 journals alphabetically, with HTTP
  200. Source IDs are therefore mapped to ISSNs through Scopus search first.
- A journal's ISSN in Scopus search records need not be one Serial Title
  knows. Journal of Information Technology is 0268-3962 in search records
  but 2251-919X / 1466-4437 in Serial Title, where 0268-3962 is a 404. Try
  both print and electronic ISSNs, and match results by source ID.
"""
import re
from typing import Any, Dict, List, Optional


def normalize_issn(value: Optional[str]) -> Optional[str]:
    """'0276-7783' / '02767783' / '0960-085x' -> '02767783' / '0960085X';
    None for anything that is not an 8-character ISSN."""
    if not value:
        return None
    compact = re.sub(r'[\s-]', '', str(value)).upper()
    return compact if re.fullmatch(r'\d{7}[\dX]', compact) else None


def format_issn(compact: Optional[str]) -> Optional[str]:
    return f"{compact[:4]}-{compact[4:]}" if compact else None


def _latest(block: Any, key: str) -> Optional[Dict[str, Any]]:
    """Most recent {'year', 'value'} from a Serial Title list like
    {'SJR': [{'@year': '2025', '$': '4.061'}]}."""
    items = (block or {}).get(key) if isinstance(block, dict) else None
    if isinstance(items, dict):
        items = [items]
    best = None
    for item in items or []:
        try:
            year, value = int(item.get('@year')), float(item.get('$'))
        except (TypeError, ValueError):
            continue
        if best is None or year > best['year']:
            best = {'year': year, 'value': value}
    return best


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_serial_entry(entry: Dict[str, Any], names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Flatten one Serial Title entry to the metrics that matter, including
    per-category percentiles when the entry comes from view=CITESCORE."""
    cs = entry.get('citeScoreYearInfoList') or {}
    ranks = subject_ranks(entry, names)
    subjects = entry.get('subject-area') or []
    if isinstance(subjects, dict):
        subjects = [subjects]
    return {
        'title': entry.get('dc:title'),
        'publisher': entry.get('dc:publisher'),
        'issn': entry.get('prism:issn'),
        'eissn': entry.get('prism:eIssn'),
        'source_id': entry.get('source-id'),
        'sjr': _latest(entry.get('SJRList'), 'SJR'),
        'snip': _latest(entry.get('SNIPList'), 'SNIP'),
        'citescore': ({'year': int(cs['citeScoreCurrentMetricYear']),
                       'value': _float(cs.get('citeScoreCurrentMetric'))}
                      if cs.get('citeScoreCurrentMetricYear') else None),
        'citescore_tracker': ({'year': int(cs['citeScoreTrackerYear']),
                               'value': _float(cs.get('citeScoreTracker'))}
                              if cs.get('citeScoreTrackerYear') else None),
        'subject_areas': [s.get('$') for s in subjects if isinstance(s, dict) and s.get('$')],
        'coverage': (f"{entry.get('coverageStartYear')}-{entry.get('coverageEndYear')}"
                     if entry.get('coverageStartYear') else None),
        'venue_type': venue_type(entry.get('prism:aggregationType')),
        'percentile_year': ranks['year'],
        'category_percentiles': ranks['ranks'],
        'best_quartile': ranks['ranks'][0]['quartile'] if ranks['ranks'] else None,
        'source': 'scopus',
    }


def serial_entry_issns(entry: Dict[str, Any]) -> List[str]:
    """Normalized print and electronic ISSNs of a Serial Title entry."""
    return [i for i in (normalize_issn(entry.get('prism:issn')),
                        normalize_issn(entry.get('prism:eIssn'))) if i]


def clean_openalex_source(source: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAlex's own journal measures; they are not SJR, SNIP or CiteScore."""
    stats = source.get('summary_stats') or {}
    return {
        'title': source.get('display_name'),
        'publisher': source.get('host_organization_name'),
        'issn': source.get('issn_l'),
        'issns': source.get('issn') or [],
        'openalex_id': (source.get('id') or '').rsplit('/', 1)[-1] or None,
        'two_year_mean_citedness': stats.get('2yr_mean_citedness'),
        'h_index': stats.get('h_index'),
        'i10_index': stats.get('i10_index'),
        'works_count': source.get('works_count'),
        'cited_by_count': source.get('cited_by_count'),
        'source': 'openalex',
    }


# ---------------------------------------------------------------------------
# Subject-category percentiles (Serial Title view=CITESCORE)
# ---------------------------------------------------------------------------
# CiteScore ranks every journal within each ASJC subject category it belongs
# to. A journal can be Q1 in one category and Q3 in another, so quartiles are
# always reported per category. The latest *complete* CiteScore year is used;
# the current year's "In-Progress" tracker changes monthly.

VENUE_TYPES = {
    'journal': 'journal',
    'conferenceproceeding': 'conference proceedings',
    'bookseries': 'book series',
    'book': 'book',
    'tradejournal': 'trade journal',
}


def venue_type(aggregation_type: Optional[str]) -> str:
    """Normalise Scopus aggregation types: Serial Title says 'conferenceproceeding',
    search records say 'Conference Proceeding'. Proceedings series such as
    IFAC-PapersOnLine or Procedia CIRP carry CiteScore ranks too, which is why
    rank alone does not mean journal."""
    key = (aggregation_type or '').lower().replace(' ', '')
    return VENUE_TYPES.get(key, 'other') if key else 'other'


def quartile(percentile: Optional[float]) -> Optional[str]:
    """Q1 = 75th percentile and up, Q2 = 50-74, Q3 = 25-49, Q4 = below 25."""
    if percentile is None:
        return None
    if percentile >= 75:
        return 'Q1'
    if percentile >= 50:
        return 'Q2'
    if percentile >= 25:
        return 'Q3'
    return 'Q4'


def _listify(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def subject_ranks(entry: Dict[str, Any], names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """CiteScore year and per-category rank, percentile and quartile.

    names maps ASJC codes to category names; the entry's own subject-area
    list is used first. Returns {'year': None, 'ranks': []} when the journal
    has no complete CiteScore year (e.g. new or discontinued titles).
    """
    own = {str(s.get('@code')): s.get('$') for s in _listify(entry.get('subject-area'))
           if isinstance(s, dict)}
    years = _listify((entry.get('citeScoreYearInfoList') or {}).get('citeScoreYearInfo'))
    complete = [y for y in years if isinstance(y, dict) and y.get('@status') == 'Complete'
                and str(y.get('@year', '')).isdigit()]
    if not complete:
        return {'year': None, 'citescore': None, 'ranks': []}
    latest = max(complete, key=lambda y: int(y['@year']))
    infos = []
    for block in _listify(latest.get('citeScoreInformationList')):
        infos.extend(_listify((block or {}).get('citeScoreInfo')))
    info = next((i for i in infos if i.get('docType') == 'all'), infos[0] if infos else {})
    ranks = []
    for r in _listify(info.get('citeScoreSubjectRank')):
        try:
            percentile = int(r.get('percentile'))
        except (TypeError, ValueError):
            continue
        code = str(r.get('subjectCode'))
        ranks.append({
            'code': code,
            'category': own.get(code) or (names or {}).get(code),
            'percentile': percentile,
            'rank': int(r['rank']) if str(r.get('rank', '')).isdigit() else None,
            'quartile': quartile(percentile),
        })
    ranks.sort(key=lambda r: -r['percentile'])
    return {'year': int(latest['@year']), 'citescore': _float(info.get('citeScore')),
            'ranks': ranks}


def category_names(asjc: List[Dict[str, Any]]) -> Dict[str, str]:
    """ASJC code -> category name (the list's 'detail' field)."""
    return {str(s.get('code')): s.get('detail') for s in asjc if s.get('code')}


def resolve_categories(inputs: List[str], asjc: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Codes or names -> [{'code', 'category', 'area'}].

    Accepts 4-digit ASJC codes, exact category names ('Information Systems'),
    or a unique fragment. Similar names are common ('Information Systems',
    'Management Information Systems', 'Information Systems and Management'),
    so an ambiguous fragment raises with the candidates instead of guessing.
    """
    by_code = {str(s['code']): s for s in asjc if s.get('code')}
    resolved = []
    for raw in inputs:
        text = str(raw).strip()
        if text in by_code:
            hit = by_code[text]
        else:
            exact = [s for s in asjc if (s.get('detail') or '').lower() == text.lower()]
            partial = exact or [s for s in asjc if text.lower() in (s.get('detail') or '').lower()]
            if len(partial) != 1:
                if not partial:
                    raise ValueError(f"No subject category matches {text!r}.")
                options = '; '.join(f"{s['code']} {s['detail']} ({s['description']})"
                                    for s in partial[:12])
                raise ValueError(f"{text!r} matches several categories, pick one by code: {options}")
            hit = partial[0]
        resolved.append({'code': str(hit['code']), 'category': hit.get('detail'),
                         'area': hit.get('description')})
    return resolved


def srcid_queries(source_ids: List[str], chunk: int = 100) -> List[str]:
    """Scopus query fragments SRCID(a OR b ...), at most `chunk` IDs each, so
    long journal lists stay within query-length limits."""
    ids = [str(s) for s in source_ids if s]
    return [f"SRCID({' OR '.join(ids[i:i + chunk])})" for i in range(0, len(ids), chunk)]
