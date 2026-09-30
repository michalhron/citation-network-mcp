"""
Author search: name parsing, query building and result cleaning for the
Scopus Author Search API and OpenAlex authors.
"""
import re
from typing import Any, Dict, List, Optional, Tuple


def parse_author_name(name: str) -> Tuple[str, Optional[str]]:
    """(surname, given names) from 'Swanson, E. Burton', 'E. Burton Swanson'
    or 'Swanson'."""
    name = ' '.join((name or '').split())
    if ',' in name:
        surname, given = (part.strip() for part in name.split(',', 1))
        return surname, given or None
    parts = name.split(' ')
    if len(parts) == 1:
        return parts[0], None
    return parts[-1], ' '.join(parts[:-1])


def _clean_term(value: str) -> str:
    # Parentheses and quotes would break Scopus field syntax.
    return ' '.join(re.sub(r'[()"\\]', ' ', value).split())


def scopus_author_query(name: str, affiliation: Optional[str] = None) -> str:
    """AUTHLASTNAME(...) AND AUTHFIRST(...) AND AFFIL(...)."""
    surname, given = parse_author_name(name)
    surname = _clean_term(surname)
    if not surname:
        raise ValueError("name is required")
    parts = [f'AUTHLASTNAME({surname})']
    if given:
        parts.append(f'AUTHFIRST({_clean_term(given)})')
    if affiliation and _clean_term(affiliation):
        parts.append(f'AFFIL({_clean_term(affiliation)})')
    return ' AND '.join(parts)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def clean_scopus_author(entry: Dict[str, Any]) -> Dict[str, Any]:
    pn = entry.get('preferred-name') or {}
    aff = entry.get('affiliation-current') or {}
    subjects: List[str] = []
    for s in _as_list(entry.get('subject-area')):
        label = s.get('$') if isinstance(s, dict) else None
        if label and label not in subjects:
            subjects.append(label)
    variants = []
    for v in _as_list(entry.get('name-variant')):
        if isinstance(v, dict):
            label = ', '.join(x for x in (v.get('surname'), v.get('given-name')) if x)
            if label and label not in variants:
                variants.append(label)
    try:
        docs = int(entry.get('document-count'))
    except (TypeError, ValueError):
        docs = None
    return {
        'author_id': (entry.get('dc:identifier') or '').replace('AUTHOR_ID:', '') or None,
        'name': ', '.join(x for x in (pn.get('surname'), pn.get('given-name')) if x) or None,
        'document_count': docs,
        'affiliation': aff.get('affiliation-name'),
        'city': aff.get('affiliation-city'),
        'country': aff.get('affiliation-country'),
        'subject_areas': subjects,
        'name_variants': variants,
        'source': 'scopus',
    }


def clean_openalex_author(author: Dict[str, Any]) -> Dict[str, Any]:
    institutions = author.get('last_known_institutions') or []
    inst = institutions[0] if institutions else {}
    orcid = author.get('orcid')
    return {
        'openalex_id': (author.get('id') or '').rsplit('/', 1)[-1] or None,
        'name': author.get('display_name'),
        'orcid': orcid.rsplit('/', 1)[-1] if orcid else None,
        'works_count': author.get('works_count'),
        'cited_by_count': author.get('cited_by_count'),
        'h_index': (author.get('summary_stats') or {}).get('h_index'),
        'affiliation': inst.get('display_name'),
        'country': inst.get('country_code'),
        'topics': [t.get('display_name') for t in (author.get('topics') or [])[:3]
                   if t.get('display_name')],
        'source': 'openalex',
    }
