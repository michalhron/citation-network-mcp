"""
OpenAlex backend: search, citing works, references and yearly counts without
Scopus subscriber entitlement.

Records come back in the same shape as the Scopus cleaners (title, creator,
publication_name, cover_date, doi, cited_by_count) plus 'openalex_id' and
'source': 'openalex', so callers never mistake one backend's data for the
other's. Identifiers are OpenAlex work IDs ('W2155046806').

OpenAlex bills usage per request against a daily budget: single-work lookups
are free, a list or filter page costs 1 credit, a search page 10. Searches
match title and abstract (not full text, which is what OpenAlex's plain
`search=` does and which is far too broad for literature work).
"""
import asyncio
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import USER_AGENT
from .cache import CacheManager
from .config import resolve_openalex_key

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org/"
WORK_FIELDS = (
    "id,doi,title,publication_year,publication_date,cited_by_count,type,"
    "primary_location,authorships,referenced_works,ids"
)
PER_PAGE = 200  # OpenAlex maximum
ID_BATCH = 50   # IDs per `openalex:` filter request
MAX_RETRIES = 2
CACHE_TTL = 86400

SORTS = {
    'coverDate': 'publication_date:desc',
    'date': 'publication_date:desc',
    'citedby': 'cited_by_count:desc',
    'citedby-count': 'cited_by_count:desc',
    'cited_by_count': 'cited_by_count:desc',
    'relevancy': 'relevance_score:desc',
    'relevance': 'relevance_score:desc',
}

_WORK_ID = re.compile(r'(?:https?://openalex\.org/)?(W\d+)$', re.IGNORECASE)
_DOI = re.compile(r'(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/\S+)$', re.IGNORECASE)


class OpenAlexError(Exception):
    """An OpenAlex request failed in a way the caller should report."""


def openalex_work_key(identifier: str) -> Optional[str]:
    """Path key for a work lookup: 'W123' or 'doi:10.x/y'; None if neither.

    Scopus IDs and EIDs are not OpenAlex identifiers; resolve those to a DOI
    through Scopus metadata first.
    """
    value = (identifier or '').strip()
    m = _WORK_ID.match(value)
    if m:
        return m.group(1).upper()
    m = _DOI.match(value)
    if m:
        return f"doi:{m.group(1).lower()}"
    return None


def short_id(openalex_url: Optional[str]) -> Optional[str]:
    """'https://openalex.org/W123' -> 'W123'."""
    if not openalex_url:
        return None
    return openalex_url.rstrip('/').rsplit('/', 1)[-1]


def bare_doi(doi_url: Optional[str]) -> Optional[str]:
    """'https://doi.org/10.1/X' -> '10.1/x'."""
    if not doi_url:
        return None
    m = _DOI.match(doi_url.strip())
    return m.group(1).lower() if m else None


def _search_value(query: str) -> str:
    # In filter values, commas separate filters and '|' means OR; '*' and '?'
    # are wildcards that stemmed search fields reject with a 400. Titles such
    # as "Should ChatGPT be Banned at Schools?" hit that, so all four go.
    return ' '.join(re.sub(r'[,|*?]', ' ', query).split())


def normalize_title(title: Optional[str]) -> str:
    """Lowercase alphanumerics only, single-spaced, for exact title matching."""
    return ' '.join(re.sub(r'[^0-9a-z]+', ' ', (title or '').lower()).split())


def indexed_name(display_name: Optional[str]) -> Optional[str]:
    """'Norman P. Hummon' -> 'Hummon N.P.', matching Scopus's dc:creator form
    so node labels ('Surname YYYY') read the same for both backends."""
    parts = (display_name or '').split()
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    initials = ''.join(f"{p[0]}." for p in parts[:-1] if p[0].isalpha())
    return f"{parts[-1]} {initials}".strip()


def _and(filter_expr: str, extra: Optional[str]) -> str:
    return f'{filter_expr},{extra}' if extra else filter_expr


def clean_openalex_work(work: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an OpenAlex work to the Scopus-cleaner record shape."""
    authorships = work.get('authorships') or []
    first = (authorships[0].get('author') or {}) if authorships else {}
    source = ((work.get('primary_location') or {}).get('source') or {})
    year = work.get('publication_year')
    return {
        'openalex_id': short_id(work.get('id')),
        'title': work.get('title'),
        'creator': indexed_name(first.get('display_name')),
        'publication_name': source.get('display_name'),
        'cover_date': work.get('publication_date'),
        'year': str(year) if year else None,
        'doi': bare_doi(work.get('doi')),
        'cited_by_count': work.get('cited_by_count'),
        'type': work.get('type'),
        'source': 'openalex',
    }


class OpenAlexClient:
    """Async OpenAlex client with disk caching, retries and budget reporting."""

    def __init__(self, cache: Optional[CacheManager] = None):
        key, self.key_source = resolve_openalex_key()
        self.has_key = bool(key)
        headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
        if key:
            # A header, not the api_key query parameter, keeps the key out of
            # URLs, logs and cache keys.
            headers['Authorization'] = f'Bearer {key}'
        self.client = httpx.AsyncClient(
            base_url=BASE_URL, headers=headers, timeout=30.0, follow_redirects=True,
        )
        self.cache = cache or CacheManager(expiration_seconds=CACHE_TTL)
        self.budget: Dict[str, Any] = {}

    async def close(self):
        await self.client.aclose()

    def _update_budget(self, headers: httpx.Headers) -> None:
        self.budget = {
            'remaining_usd': headers.get('x-ratelimit-remaining-usd'),
            'limit_usd': headers.get('x-ratelimit-limit-usd'),
            'reset_seconds': headers.get('x-ratelimit-reset'),
        }

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """GET with cache and retries. Returns None on 404."""
        url = BASE_URL + path
        cached = self.cache.get(url, params)
        if cached:
            return cached
        attempt = 0
        while True:
            try:
                response = await self.client.get(path, params=params)
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError,
                    httpx.RemoteProtocolError) as exc:
                if attempt >= MAX_RETRIES:
                    raise OpenAlexError(
                        f"Network error contacting OpenAlex after {attempt + 1} "
                        f"attempt(s): {type(exc).__name__}"
                    ) from exc
                attempt += 1
                await asyncio.sleep(random.uniform(0, 2 ** attempt))
                continue

            self._update_budget(response.headers)
            status = response.status_code
            if status == 404:
                return None
            if status == 401:
                raise OpenAlexError(
                    "OpenAlex rejected the API key (401). Check OPENALEX_API_KEY, "
                    "or remove it to use the anonymous budget."
                )
            if status == 429:
                # Retrying cannot help once the daily budget is spent.
                if self.budget.get('remaining_usd') in ('0', '0.0', '0.00'):
                    raise OpenAlexError(self._budget_message())
                if attempt < MAX_RETRIES:
                    attempt += 1
                    await asyncio.sleep(random.uniform(0, 2 ** attempt))
                    continue
                raise OpenAlexError(self._budget_message())
            if status >= 500 and attempt < MAX_RETRIES:
                attempt += 1
                await asyncio.sleep(random.uniform(0, 2 ** attempt))
                continue
            if status >= 400:
                raise OpenAlexError(
                    f"OpenAlex error {status} for {path}: {response.text[:300]}"
                )
            data = response.json()
            self.cache.set(url, data, params, ttl=CACHE_TTL)
            return data

    def _budget_message(self) -> str:
        msg = (
            "OpenAlex rate limit or daily budget reached "
            f"(remaining ${self.budget.get('remaining_usd', '?')} of "
            f"${self.budget.get('limit_usd', '?')}, resets in "
            f"{self.budget.get('reset_seconds', '?')} s)."
        )
        if not self.has_key:
            msg += (" A free OpenAlex account key (OPENALEX_API_KEY) raises the "
                    "daily budget tenfold.")
        return msg

    async def search_authors(self, name: str, count: int = 10) -> List[Dict[str, Any]]:
        """Authors matching a name (OpenAlex relevance order). One search
        request (10 credits)."""
        data = await self._get('authors', {
            'search': _search_value(name),
            'per-page': count,
            'select': 'id,display_name,orcid,works_count,cited_by_count,'
                      'last_known_institutions,summary_stats,topics',
        }) or {}
        return data.get('results') or []

    async def get_source_by_issn(self, issn: str) -> Optional[Dict[str, Any]]:
        """Journal (OpenAlex 'source') by ISSN, '0276-7783' form; free, cached."""
        return await self._get(f'sources/issn:{issn}', {
            'select': 'id,display_name,host_organization_name,issn_l,issn,'
                      'summary_stats,works_count,cited_by_count',
        })

    async def get_work(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Raw work by OpenAlex ID or DOI; None when not found."""
        key = openalex_work_key(identifier)
        if key is None:
            raise ValueError(
                f"{identifier!r} is not an OpenAlex work ID (W...) or a DOI."
            )
        return await self._get(f'works/{key}', {'select': WORK_FIELDS})

    async def find_by_title(self, title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Work whose title matches exactly (after normalize_title), within one
        year of `year` when given; None when nothing matches exactly.

        For records without a DOI, such as AIS conference papers. Fuzzy
        matches are never accepted: a wrong seed silently corrupts a network.
        Among several exact matches (e.g. preprint and proceedings), the one
        with the longest reference list wins, as references drive the network
        tools. Costs one search request.
        """
        wanted = normalize_title(title)
        if not wanted:
            return None
        filters = [f'title.search:{_search_value(title)}']
        if year:
            filters.append(f'publication_year:{int(year) - 1}-{int(year) + 1}')
        data = await self._get('works', {
            'filter': ','.join(filters), 'per-page': 10, 'select': WORK_FIELDS,
        }) or {}
        exact = [w for w in data.get('results') or []
                 if normalize_title(w.get('title')) == wanted]
        if not exact:
            return None
        return max(exact, key=lambda w: len(w.get('referenced_works') or []))

    async def list_works(self, filter_expr: str, max_results: int,
                         sort: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Cursor-paged works for a filter. Returns (raw works, meta)."""
        works: List[Dict[str, Any]] = []
        cursor = '*'
        total = None
        pages = 0
        while cursor and len(works) < max_results:
            params = {
                'filter': filter_expr,
                'per-page': min(PER_PAGE, max_results - len(works)),
                'cursor': cursor,
                'select': WORK_FIELDS,
            }
            if sort:
                params['sort'] = SORTS.get(sort, sort)
            data = await self._get('works', params) or {}
            pages += 1
            meta = data.get('meta') or {}
            if total is None:
                total = meta.get('count')
            batch = data.get('results') or []
            if not batch:
                break
            works.extend(batch)
            cursor = meta.get('next_cursor')
        works = works[:max_results]
        return works, {
            'total_available': total,
            'total_fetched': len(works),
            'truncated': total is not None and len(works) < total,
            'pages_fetched': pages,
            'source': 'openalex',
        }

    async def search(self, query: str, max_results: int = 200,
                     sort: str = 'relevance',
                     extra_filter: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Title-and-abstract search; extra_filter is ANDed (e.g. an ISSN scope)."""
        return await self.list_works(
            _and(f'title_and_abstract.search:{_search_value(query)}', extra_filter),
            max_results, sort,
        )

    async def citing(self, work_id: str, max_results: int = 200,
                     sort: str = 'coverDate',
                     extra_filter: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Works that cite `work_id` (an OpenAlex W-id)."""
        return await self.list_works(_and(f'cites:{work_id}', extra_filter), max_results, sort)

    async def works_by_ids(self, work_ids: List[str], select: str = WORK_FIELDS) -> List[Dict[str, Any]]:
        """Hydrate W-ids in batches of ID_BATCH (1 credit per batch)."""
        found: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(work_ids), ID_BATCH):
            chunk = work_ids[i:i + ID_BATCH]
            data = await self._get('works', {
                'filter': 'openalex:' + '|'.join(chunk),
                'per-page': ID_BATCH,
                'select': select,
            }) or {}
            for w in data.get('results') or []:
                found[short_id(w.get('id'))] = w
        # Keep the caller's (reference-list) order; drop IDs OpenAlex lacks.
        return [found[w] for w in work_ids if w in found]

    async def reference_counts(self, dois: List[str]) -> Dict[str, Optional[int]]:
        """DOI (lower-case) -> referenced_works_count, ID_BATCH DOIs per
        request (1 credit each). OpenAlex counts only references it matched
        to a work, so the figure is a lower bound; 0 comes back as None."""
        out: Dict[str, Optional[int]] = {}
        dois = [d.lower() for d in dict.fromkeys(d for d in dois if d)]
        for i in range(0, len(dois), ID_BATCH):
            chunk = dois[i:i + ID_BATCH]
            data = await self._get('works', {
                'filter': 'doi:' + '|'.join(chunk),
                'per-page': ID_BATCH,
                'select': 'doi,referenced_works_count',
            }) or {}
            for w in data.get('results') or []:
                doi = bare_doi(w.get('doi'))
                if doi:
                    out[doi.lower()] = w.get('referenced_works_count') or None
        return out

    async def references(self, work: Dict[str, Any], limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Hydrated reference list of a raw work (from get_work)."""
        ref_ids = [short_id(r) for r in (work.get('referenced_works') or [])]
        if limit is not None:
            ref_ids = ref_ids[:limit]
        return await self.works_by_ids(ref_ids)

    async def yearly_counts(self, query: str, from_year: Optional[int] = None,
                            to_year: Optional[int] = None) -> Tuple[Dict[int, int], int]:
        """Works per publication year for a title-and-abstract query.

        One grouped request (1 credit). Returns ({year: count}, total).
        """
        filters = [f'title_and_abstract.search:{_search_value(query)}']
        if from_year or to_year:
            filters.append(f"publication_year:{from_year or ''}-{to_year or ''}")
        data = await self._get('works', {
            'filter': ','.join(filters), 'group_by': 'publication_year',
        }) or {}
        counts = {}
        for group in data.get('group_by') or []:
            try:
                counts[int(group['key'])] = int(group['count'])
            except (KeyError, TypeError, ValueError):
                continue
        return dict(sorted(counts.items())), int((data.get('meta') or {}).get('count') or 0)
