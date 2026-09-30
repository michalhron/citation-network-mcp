"""Citation contexts from Semantic Scholar: the sentences in which a citing
paper cites another, with intent labels (background, methodology, result)
and the "influential citation" flag.

These are evidence of what a citation edge carries, beyond its existence:
a network edge whose only context is a background mention in a list of
references is weak evidence of transmission.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from . import USER_AGENT
from .config import contact_user_agent, resolve_semantic_scholar_key
from .openalex import normalize_title

logger = logging.getLogger(__name__)

S2_GRAPH = 'https://api.semanticscholar.org/graph/v1/'
CONTEXT_FIELDS = 'contexts,intents,isInfluential,title,externalIds'
PAGE = 1000
MAX_REFERENCES = 2000
MAX_CITATIONS = 10000
MAX_RETRIES = 3


def _headers() -> Dict[str, str]:
    headers = {'User-Agent': contact_user_agent(USER_AGENT)}
    key, _ = resolve_semantic_scholar_key()
    if key:
        headers['x-api-key'] = key
    return headers


async def _get(http: httpx.AsyncClient, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """GET with backoff on 429 (the keyless pool is shared and busy).
    None for 404; raises on other failures."""
    for attempt in range(MAX_RETRIES + 1):
        r = await http.get(url, params=params)
        if r.status_code == 429 and attempt < MAX_RETRIES:
            await asyncio.sleep(1.5 * 2 ** attempt)
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            raise RuntimeError(
                "Semantic Scholar rate limit (429) after retries. A free API key "
                "(SEMANTIC_SCHOLAR_API_KEY) raises the limit.")
        r.raise_for_status()
        return r.json()
    return None


async def _paged(http: httpx.AsyncClient, path: str, max_items: int) -> Optional[List[Dict[str, Any]]]:
    """All entries of a paged S2 list endpoint; None when S2 has no such
    paper or the publisher elided the list (data: null)."""
    items: List[Dict[str, Any]] = []
    offset = 0
    while offset < max_items:
        data = await _get(http, f'{S2_GRAPH}{path}',
                          {'fields': CONTEXT_FIELDS, 'limit': PAGE, 'offset': offset})
        if data is None or data.get('data') is None:
            return items or None
        batch = data['data']
        items.extend(batch)
        if data.get('next') is None or not batch:
            break
        offset = data['next']
    return items


def _same_paper(paper: Dict[str, Any], doi: Optional[str], title: Optional[str]) -> bool:
    ext = paper.get('externalIds') or {}
    if doi and (ext.get('DOI') or '').lower() == doi.lower():
        return True
    t = normalize_title(title)
    return bool(t) and normalize_title(paper.get('title')) == t


def describe(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if entry is None:
        return {'status': 'not_found'}
    contexts = [c for c in (entry.get('contexts') or []) if c]
    out = {
        'status': 'found',
        'intents': entry.get('intents') or [],
        'is_influential': bool(entry.get('isInfluential')),
        'n_contexts': len(contexts),
        'contexts': contexts,
    }
    if not contexts:
        out['note'] = ("Semantic Scholar has the citation but no context "
                       "sentences (often withheld for the publisher's content).")
    return out


async def citation_contexts(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Contexts for pairs of {'citing_doi', 'citing_title', 'cited_doi',
    'cited_title', ...}; each pair comes back with a 'context' entry.

    First the citing paper's reference list (one request per citing
    paper). Publishers such as Wiley and Taylor & Francis have S2 elide
    reference lists, so unresolved pairs then go through the cited paper's
    citation list, which S2 still serves with contexts.
    """
    async with httpx.AsyncClient(timeout=30.0, headers=_headers(), follow_redirects=True) as http:
        by_citing: Dict[str, List[Dict[str, Any]]] = {}
        for p in pairs:
            if p.get('citing_doi'):
                by_citing.setdefault(p['citing_doi'].lower(), []).append(p)
        for citing, group in by_citing.items():
            try:
                refs = await _paged(http, f'paper/DOI:{citing}/references', MAX_REFERENCES)
            except Exception as exc:
                logger.info(f"S2 references failed for {citing}: {exc}")
                continue
            for p in group:
                hit = next((r for r in refs or []
                            if _same_paper(r.get('citedPaper') or {}, p.get('cited_doi'),
                                           p.get('cited_title'))), None)
                if hit is not None:
                    p['context'] = describe(hit)
                    p['context']['via'] = 'citing paper references'

        by_cited: Dict[str, List[Dict[str, Any]]] = {}
        for p in pairs:
            if 'context' not in p and p.get('cited_doi'):
                by_cited.setdefault(p['cited_doi'].lower(), []).append(p)
        for cited, group in by_cited.items():
            try:
                citers = await _paged(http, f'paper/DOI:{cited}/citations', MAX_CITATIONS)
            except Exception as exc:
                for p in group:
                    p['context'] = {'status': 'error', 'detail': str(exc)[:200]}
                continue
            for p in group:
                if citers is None:
                    p['context'] = {'status': 'cited_paper_not_in_semantic_scholar'}
                    continue
                hit = next((c for c in citers
                            if _same_paper(c.get('citingPaper') or {}, p.get('citing_doi'),
                                           p.get('citing_title'))), None)
                p['context'] = describe(hit)
                if hit is not None:
                    p['context']['via'] = 'cited paper citations'
                elif len(citers) >= MAX_CITATIONS:
                    p['context']['note'] = (f"Only the first {MAX_CITATIONS} citations "
                                            "of the cited paper were searched.")
        for p in pairs:
            p.setdefault('context', {'status': 'not_found',
                                     'note': 'No DOI for the cited paper to search its citations.'})
    return pairs
