"""Citation contexts from Semantic Scholar: the sentences in which a citing
paper cites another, with intent labels (background, methodology, result)
and the "influential citation" flag.

These are evidence of what a citation edge carries, beyond its existence:
a network edge whose only context is a background mention in a list of
references is weak evidence of transmission.

Papers are resolved to Semantic Scholar IDs by DOI, then MAG ID, then an
exact-enough title (plus year) match, because S2 files some papers under a
different DOI than the publisher's (Swanson & Ramiller 2004, MISQ, has JSTOR
DOI 10.2307/25148655 but sits in S2 under an ACM 10.5555 DOI).
"""
import asyncio
import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from . import USER_AGENT
from .config import contact_user_agent, resolve_semantic_scholar_key
from .openalex import normalize_title

logger = logging.getLogger(__name__)

S2_GRAPH = 'https://api.semanticscholar.org/graph/v1/'
PAPER_FIELDS = 'paperId,title,year,externalIds,authors'
CONTEXT_FIELDS = 'contexts,intents,isInfluential,paperId'
PAGE = 1000
MAX_REFERENCES = 2000
MAX_CITATIONS = 10000
MAX_RETRIES = 4
TITLE_SIMILARITY = 0.9
DEFAULT_MAX_CONTEXTS = 3

# Running headers and footers that PDF extraction glues into contexts:
# "MIS Quarterly Vol. 33 No. 4, pp. 647-662/December 2009 647".
_HEADER = re.compile(r'\bVol(?:ume)?\.?\s*\d+\b.{0,20}\b(?:No|Issue)\.?\s*\d+|\bpp\.\s*\d+\s*[-–]\s*\d+',
                     re.IGNORECASE)
_NUMERIC_MARKER = re.compile(r'\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]')


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
        if r.status_code in (400, 404):
            return None
        if r.status_code == 429:
            raise RuntimeError(
                "Semantic Scholar rate limit (429) after retries. A free API key "
                "(SEMANTIC_SCHOLAR_API_KEY) raises the limit.")
        r.raise_for_status()
        return r.json()
    return None


def _similar(a: Optional[str], b: Optional[str]) -> bool:
    a, b = normalize_title(a), normalize_title(b)
    if not a or not b:
        return False
    # Trailing footnote digits ("Technology1") are common in OpenAlex titles.
    a, b = re.sub(r'(?<=[a-z])\d$', '', a), re.sub(r'(?<=[a-z])\d$', '', b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= TITLE_SIMILARITY


async def resolve_paper(http: httpx.AsyncClient, ident: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The S2 paper for {'doi', 'mag', 'title', 'year'}, with the 'route'
    that found it ('doi', 'mag' or 'title_match'); None when every route
    fails."""
    doi, mag, title, year = (ident.get(k) for k in ('doi', 'mag', 'title', 'year'))
    if doi:
        paper = await _get(http, f'{S2_GRAPH}paper/DOI:{doi}', {'fields': PAPER_FIELDS})
        if paper and paper.get('paperId'):
            return dict(paper, route='doi')
    if mag:
        paper = await _get(http, f'{S2_GRAPH}paper/MAG:{mag}', {'fields': PAPER_FIELDS})
        if paper and paper.get('paperId') and (not title or _similar(title, paper.get('title'))):
            return dict(paper, route='mag')
    if title:
        data = await _get(http, f'{S2_GRAPH}paper/search/match',
                          {'query': re.sub(r'[^\w\s]', ' ', title), 'fields': PAPER_FIELDS})
        for paper in (data or {}).get('data') or []:
            y = paper.get('year')
            year_ok = not year or not y or abs(int(y) - int(year)) <= 1
            if _similar(title, paper.get('title')) and year_ok:
                return dict(paper, route='title_match')
    return None


async def _paged(http: httpx.AsyncClient, path: str, max_items: int,
                 fields: str = CONTEXT_FIELDS) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
    """(entries, complete) of a paged S2 list. entries is None when the
    publisher had S2 elide the list (data: null); complete is False when
    max_items cut it."""
    items: List[Dict[str, Any]] = []
    offset = 0
    while offset < max_items:
        data = await _get(http, f'{S2_GRAPH}{path}',
                          {'fields': fields, 'limit': PAGE, 'offset': offset})
        if data is None or data.get('data') is None:
            return (items or None), True
        batch = data['data']
        items.extend(batch)
        if data.get('next') is None or not batch:
            return items, True
        offset = data['next']
    return items, False


CITER_FIELDS = 'title,year,externalIds,publicationVenue,referenceCount'


async def citing_papers(ident: Dict[str, Any], max_items: int = MAX_CITATIONS
                        ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """(resolved paper, citing papers with venue ISSNs and reference counts)
    for a seed; ([], None) when S2 cannot resolve it."""
    async with httpx.AsyncClient(timeout=30.0, headers=_headers(), follow_redirects=True) as http:
        paper = await resolve_paper(http, ident)
        if paper is None:
            return None, []
        entries, _ = await _paged(http, f"paper/{paper['paperId']}/citations", max_items,
                                  fields=CITER_FIELDS)
        return paper, [e.get('citingPaper') or {} for e in entries or []]


def venue_issns(paper: Dict[str, Any]) -> List[str]:
    venue = paper.get('publicationVenue') or {}
    issns = [venue.get('issn')] + list(venue.get('alternate_issns') or [])
    return [i.upper() for i in issns if i]


def surnames(paper: Optional[Dict[str, Any]]) -> List[str]:
    out = []
    for a in (paper or {}).get('authors') or []:
        name = (a.get('name') or '').strip()
        if name:
            out.append(name.split()[-1])
    return out


def clean_contexts(contexts: Sequence[str], names: Sequence[str], year: Optional[int],
                   terms: Sequence[str] = (), limit: int = DEFAULT_MAX_CONTEXTS) -> Tuple[List[str], int, int]:
    """(kept, n_total, n_dropped): contexts without running headers or
    citation-free noise, most informative first, at most `limit`.

    A context is kept only if it carries a citation marker for the cited
    work: an author surname, its year, a numeric marker ([36]) or "et al.".
    Ranking: naming the authors, then the construct terms, then the year.
    """
    names_re = [re.compile(rf'\b{re.escape(n)}\b', re.IGNORECASE) for n in names if len(n) > 1]
    terms_low = [t.lower() for t in terms if t]
    ranked, seen = [], set()
    for raw in contexts or []:
        text = ' '.join(str(raw).split())
        if len(text) < 25 or text.lower() in seen:
            continue
        has_name = any(r.search(text) for r in names_re)
        has_year = bool(year) and str(year) in text
        marker = has_name or has_year or bool(_NUMERIC_MARKER.search(text)) or 'et al' in text
        if not marker or (_HEADER.search(text) and not has_name):
            continue
        seen.add(text.lower())
        has_term = any(t in text.lower() for t in terms_low)
        ranked.append(((2 * has_name) + (2 * has_term) + has_year, text))
    ranked.sort(key=lambda x: -x[0])  # stable: S2 order within a score
    kept = [t for _, t in ranked[:max(0, limit)]]
    total = len(contexts or [])
    return kept, total, total - len(ranked)


def describe(entry: Dict[str, Any], cited: Dict[str, Any], terms: Sequence[str], limit: int) -> Dict[str, Any]:
    kept, total, dropped = clean_contexts(entry.get('contexts') or [], surnames(cited),
                                          cited.get('year'), terms, limit)
    out = {
        'status': 'found' if kept else 'contexts_withheld',
        'intents': entry.get('intents') or [],
        'is_influential': bool(entry.get('isInfluential')),
        'n_contexts': len(kept),
        'n_contexts_total': total,
        'n_dropped_as_noise': dropped,
        'contexts': kept,
    }
    if not kept:
        out['note'] = ("Semantic Scholar has this citation but no usable context "
                       "sentences (often withheld for the publisher's content)."
                       if not total else
                       f"All {total} context(s) were page furniture or lacked a citation marker.")
    return out


class ContextFinder:
    """Resolves papers and caches reference and citation lists across pairs."""

    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.papers: Dict[str, Optional[Dict[str, Any]]] = {}
        self.refs: Dict[str, Tuple[Optional[List[Dict[str, Any]]], bool]] = {}
        self.citers: Dict[str, Tuple[Optional[List[Dict[str, Any]]], bool]] = {}

    async def paper(self, ident: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = ident.get('key') or repr(sorted(ident.items()))
        if key not in self.papers:
            self.papers[key] = await resolve_paper(self.http, ident)
        return self.papers[key]

    async def context(self, citing: Dict[str, Any], cited: Dict[str, Any],
                      terms: Sequence[str] = (), limit: int = DEFAULT_MAX_CONTEXTS) -> Dict[str, Any]:
        a, b = await self.paper(citing), await self.paper(cited)
        routes = {'citing_route': a and a['route'], 'cited_route': b and b['route'],
                  's2_citing_id': a and a['paperId'], 's2_cited_id': b and b['paperId']}
        if a is None:
            return {'status': 'citing_paper_unresolved', **routes,
                    'note': 'No DOI, MAG or title match for the citing paper in Semantic Scholar.'}
        if b is None:
            return {'status': 'cited_paper_unresolved', **routes,
                    'note': 'No DOI, MAG or title match for the cited paper in Semantic Scholar.'}
        if a['paperId'] not in self.refs:
            self.refs[a['paperId']] = await _paged(self.http, f"paper/{a['paperId']}/references",
                                                   MAX_REFERENCES)
        refs, _ = self.refs[a['paperId']]
        hit = next((r for r in refs or []
                    if (r.get('citedPaper') or {}).get('paperId') == b['paperId']), None)
        via = 'citing paper references'
        complete = refs is not None
        if hit is None:
            # Wiley and Taylor & Francis have S2 elide reference lists; the
            # cited paper's citation list still carries the contexts.
            if b['paperId'] not in self.citers:
                self.citers[b['paperId']] = await _paged(
                    self.http, f"paper/{b['paperId']}/citations", MAX_CITATIONS)
            citers, citers_complete = self.citers[b['paperId']]
            hit = next((c for c in citers or []
                        if (c.get('citingPaper') or {}).get('paperId') == a['paperId']), None)
            via = 'cited paper citations'
            complete = complete or (citers is not None and citers_complete)
        if hit is None:
            out = {'status': 'edge_absent_in_s2', **routes,
                   'note': 'Both papers are in Semantic Scholar, but it records no citation between them.'}
            if not complete:
                out['note'] = (f"Both papers resolved; the citing paper's references are withheld "
                               f"and only the cited paper's first {MAX_CITATIONS} citations were searched.")
            return out
        return {**describe(hit, b, terms, limit), 'via': via, **routes}


async def citation_contexts(pairs: List[Dict[str, Any]], terms: Sequence[str] = (),
                            max_contexts: int = DEFAULT_MAX_CONTEXTS) -> List[Dict[str, Any]]:
    """Adds a 'context' dict to each {'citing_ident', 'cited_ident', ...}."""
    async with httpx.AsyncClient(timeout=30.0, headers=_headers(), follow_redirects=True) as http:
        finder = ContextFinder(http)
        for p in pairs:
            try:
                p['context'] = await finder.context(p['citing_ident'], p['cited_ident'],
                                                    terms, max_contexts)
            except Exception as exc:
                p['context'] = {'status': 'error', 'detail': str(exc)[:200]}
    return pairs
