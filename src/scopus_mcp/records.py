"""Scopus response parsing, identifier helpers, and abstract fallbacks (OpenAlex, Crossref)."""
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from . import USER_AGENT
from .config import contact_user_agent

logger = logging.getLogger(__name__)


def clean_search_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extracts and normalizes search results from the Scopus Search API response.

    Args:
        data: The raw JSON response from Scopus API.

    Returns:
        A list of simplified dictionaries containing key article metadata.
        Returns [] when there are zero results or when the API returns an
        error-sentinel entry (no dc:identifier, carries an 'error' key).
    """
    if not data or 'search-results' not in data:
        return []

    sr = data['search-results']

    # Scopus signals an empty result set with totalResults "0".
    if str(sr.get('opensearch:totalResults', '')).strip() == '0':
        return []

    entries = sr.get('entry', [])
    cleaned_entries = []

    for entry in entries:
        # Skip error-sentinel entries: they carry an 'error' key and no real ID.
        if entry.get('error') and not entry.get('dc:identifier'):
            continue
        cleaned = {
            'scopus_id': entry.get('dc:identifier', '').replace('SCOPUS_ID:', ''),
            'title': entry.get('dc:title'),
            'creator': entry.get('dc:creator'),
            'publication_name': entry.get('prism:publicationName'),
            'cover_date': entry.get('prism:coverDate'),
            'doi': entry.get('prism:doi'),
            'cited_by_count': entry.get('citedby-count'),
            'aggregation_type': entry.get('prism:aggregationType'),
            'url': next((link['@href'] for link in entry.get('link', []) if link.get('@ref') == 'scopus'), None)
        }
        cleaned_entries.append(cleaned)

    return cleaned_entries


def _reconstruct_abstract_from_openalex(inverted_index: Dict[str, Any]) -> str:
    """Reconstruct abstract text from OpenAlex abstract_inverted_index."""
    if not inverted_index:
        return ''
    pos_word: Dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            pos_word[pos] = word
    if not pos_word:
        return ''
    return ' '.join(pos_word[i] for i in sorted(pos_word))


def _strip_jats_tags(text: str) -> str:
    """Strip JATS XML tags from Crossref abstract text."""
    return re.sub(r'<[^>]+>', '', text).strip()


async def _fetch_abstract_openalex(doi: str) -> Optional[str]:
    """Fetch abstract from OpenAlex by DOI. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=15, headers={'User-Agent': contact_user_agent(USER_AGENT)}) as client:
            r = await client.get(f'https://api.openalex.org/works/doi:{doi}')
        if r.status_code != 200:
            return None
        body = r.json()
        idx = body.get('abstract_inverted_index')
        if idx:
            return _reconstruct_abstract_from_openalex(idx)
    except Exception as exc:
        logger.debug(f"OpenAlex abstract fetch failed for doi={doi}: {exc}")
    return None


async def _fetch_abstract_crossref(doi: str) -> Optional[str]:
    """Fetch abstract from Crossref by DOI. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=15, headers={'User-Agent': contact_user_agent(USER_AGENT)}) as client:
            r = await client.get(f'https://api.crossref.org/works/{doi}')
        if r.status_code != 200:
            return None
        body = r.json()
        abstract = body.get('message', {}).get('abstract', '')
        if abstract:
            return _strip_jats_tags(abstract)
    except Exception as exc:
        logger.debug(f"Crossref abstract fetch failed for doi={doi}: {exc}")
    return None


def clean_abstract_details(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts relevant details from the Scopus Abstract Retrieval API response.
    Surfaces X-ELS-Status and HTTP status instead of silently returning None.
    Authors are populated from dc:creator (available in META view) or authors block.
    abstract_source indicates where the abstract came from: scopus, openalex, crossref, or none.
    """
    # Diagnostic fields injected by client.get_abstract
    els_status = data.get('_els_status', 'OK')
    view_used = data.get('_view_used')

    root = data.get('abstracts-retrieval-response') or data.get('abstract-retrieval-response')

    if not root:
        return {
            'scopus_id': None,
            'doi': None,
            'title': None,
            'description': None,
            'abstract_source': 'none',
            'publication_name': None,
            'cover_date': None,
            'cited_by_count': None,
            'authors': [],
            'url': None,
            '_els_status': els_status,
            '_view_used': view_used,
        }

    coredata = root.get('coredata', {})

    # Authors: prefer the fuller 'authors' block (FULL/META_ABS), fall back to dc:creator (META)
    authors_data = root.get('authors', {}).get('author', [])
    if not authors_data:
        creator = coredata.get('dc:creator', {})
        if isinstance(creator, dict):
            authors_data = creator.get('author', [])
        elif isinstance(creator, list):
            authors_data = creator

    if isinstance(authors_data, dict):
        authors_data = [authors_data]

    authors = []
    for auth in authors_data:
        authors.append({
            'auth_id': auth.get('@auid'),
            'name': auth.get('ce:indexed-name'),
            'surname': auth.get('ce:surname'),
            'initials': auth.get('ce:initials') or auth.get('ce:given-name'),
        })

    description = coredata.get('dc:description')
    abstract_source = 'scopus' if description else 'none'

    return {
        'scopus_id': coredata.get('dc:identifier', '').replace('SCOPUS_ID:', ''),
        'doi': coredata.get('prism:doi'),
        'title': coredata.get('dc:title'),
        'description': description,
        'abstract_source': abstract_source,
        'publication_name': coredata.get('prism:publicationName'),
        'cover_date': coredata.get('prism:coverDate'),
        'cited_by_count': coredata.get('citedby-count'),
        'authors': authors,
        'url': next((link['@href'] for link in coredata.get('link', []) if link.get('@ref') == 'scopus'), None),
        '_els_status': els_status,
        '_view_used': view_used,
    }


def clean_author_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts details from the Scopus Author Retrieval API response.

    Args:
        data: The raw JSON response from Scopus API.

    Returns:
        A simplified dictionary containing author profile information.
    """
    if not data or 'author-retrieval-response' not in data:
        return {}
    
    root = data['author-retrieval-response']
    core = root.get('coredata', {})
    profile = root.get('author-profile', {})
    
    name_variant = profile.get('preferred-name', {})
    
    return {
        'author_id': core.get('dc:identifier', '').replace('AUTHOR_ID:', ''),
        'orcid': core.get('orcid'),
        'document_count': core.get('document-count'),
        'cited_by_count': core.get('cited-by-count'),
        'citation_count': core.get('citation-count'),
        'name': {
            'surname': name_variant.get('surname'),
            'given_name': name_variant.get('given-name'),
            'initials': name_variant.get('initials')
        },
        'current_affiliation': _extract_affiliation(profile),
        'url': next((link['@href'] for link in core.get('link', []) if link.get('@ref') == 'scopus-author'), None)
    }


def _extract_affiliation(profile: Dict[str, Any]) -> Optional[str]:
    """Helper to extract current affiliation name."""
    affil = profile.get('affiliation-current', {}).get('affiliation', {})
    # Sometimes it's a list if multiple affiliations
    if isinstance(affil, list):
        affil = affil[0] if affil else {}
        
    return affil.get('ip-doc', {}).get('afdispname')


def to_scopus_id(value: str) -> str:
    """Return the bare numeric Scopus ID, stripping a SCOPUS_ID: prefix or
    2-s2.0- EID prefix if present."""
    clean = str(value).strip().replace('SCOPUS_ID:', '')
    if clean.lower().startswith('2-s2.0-'):
        clean = clean[len('2-s2.0-'):]
    return clean


def to_eid(value: str) -> str:
    """Return the EID form (2-s2.0-<id>) used by the Search API's REF() field."""
    return f'2-s2.0-{to_scopus_id(value)}'


def detect_id_type(value: str) -> str:
    """Best-effort detection of an identifier's type.

    Returns one of: 'eid', 'doi', 'pii', 'scopus_id'. Callers may override.
    """
    v = str(value).strip()
    if v.lower().startswith('2-s2.0-'):
        return 'eid'
    if v.startswith('10.') or '/' in v:
        return 'doi'
    bare = v.replace('SCOPUS_ID:', '')
    if bare.isdigit():
        return 'scopus_id'
    # PII is 17 chars, alphanumeric, often starting with S or B. Loose heuristic.
    if bare and bare[0] in ('S', 'B') and bare.replace('-', '').isalnum():
        return 'pii'
    return 'scopus_id'


def clean_identifiers(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the cross-reference identifier set from an Abstract Retrieval
    response. This is the join layer for cross-linking with OpenAlex/Crossref,
    which key on DOI."""
    root = data.get('abstracts-retrieval-response') or data.get('abstract-retrieval-response')
    if not root:
        return {}

    core = root.get('coredata', {})
    sid = core.get('dc:identifier', '').replace('SCOPUS_ID:', '')
    eid = core.get('eid') or (f'2-s2.0-{sid}' if sid else None)

    return {
        'scopus_id': sid or None,
        'eid': eid,
        'doi': core.get('prism:doi'),
        'pii': core.get('pii'),
        'pubmed_id': core.get('pubmed-id'),
        'title': core.get('dc:title'),
        'publication_name': core.get('prism:publicationName'),
        'cover_date': core.get('prism:coverDate'),
        'cited_by_count': core.get('citedby-count'),
    }


def clean_references(data: Dict[str, Any], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Extract the cited-reference list (backward citations) from an Abstract
    Retrieval REF-view response.

    The REF view returns each reference as a flat object with top-level keys:
    'title', 'sourcetitle', 'scopus-id', 'ce:doi', 'prism:coverDate',
    'author-list', '@id'.  Unresolved or partially matched references may omit
    some fields; the parser degrades gracefully to None for missing values.
    Authors are deduplicated by @auid to collapse multi-affiliation duplicates.
    """
    root = data.get('abstracts-retrieval-response') or data.get('abstract-retrieval-response')
    if not root:
        return []

    ref_block = root.get('references')
    if not isinstance(ref_block, dict):
        return []
    refs = ref_block.get('reference')
    if refs is None:
        return []
    if isinstance(refs, dict):
        refs = [refs]
    if not isinstance(refs, list):
        return []

    cleaned: List[Dict[str, Any]] = []
    for r in refs:
        if not isinstance(r, dict):
            continue

        # Year from ISO cover date (e.g. "1996-01-01" → "1996")
        cover = r.get('prism:coverDate') or ''
        year = cover[:4] if cover else None

        # Authors — deduplicate by @auid to collapse multi-affiliation entries
        raw_authors = r.get('author-list', {})
        if isinstance(raw_authors, dict):
            raw_authors = raw_authors.get('author', [])
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]
        seen_auids: set = set()
        authors: List[str] = []
        for a in (raw_authors or []):
            if not isinstance(a, dict):
                continue
            auid = a.get('@auid')
            if auid and auid in seen_auids:
                continue
            if auid:
                seen_auids.add(auid)
            name = a.get('ce:indexed-name') or a.get('ce:surname')
            if name:
                authors.append(name)

        cleaned.append({
            'position': r.get('@id'),
            'title': r.get('title'),
            'authors': authors,
            'source': r.get('sourcetitle'),
            'year': year or None,
            'scopus_id': r.get('scopus-id'),
            'doi': r.get('ce:doi'),
            'fulltext': None,
        })

    if limit is not None:
        return cleaned[:limit]
    return cleaned
