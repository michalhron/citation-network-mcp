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


def clean_serial_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten one Serial Title entry to the metrics that matter."""
    cs = entry.get('citeScoreYearInfoList') or {}
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
