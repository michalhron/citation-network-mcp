import logging
import asyncio
import math
import random
import time
import httpx
from typing import Optional, Dict, Any
from urllib.parse import urljoin

from . import USER_AGENT
from .config import (
    get_api_key,
    get_cache_config,
    get_max_retries,
    get_page_size,
    get_proxy,
    proxy_scheme,
    resolve_api_key,
    resolve_insttoken,
)
from .cache import CacheManager
from .utils import to_scopus_id, to_eid

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.elsevier.com/"

# Elsevier's misleading off-network failure: the Search API rejects every
# query (even trivially valid ones) with this 400 when the key authenticates
# but lacks subscriber entitlement (no institutional IP and no insttoken).
ENTITLEMENT_400_STATUS_TEXT = "Error translating query"
ENTITLEMENT_NOTE = (
    " Note: if your query is valid Scopus syntax, this error usually means "
    "the request lacks subscriber entitlement. Off-network access requires "
    "the institutional VPN, SCOPUS_PROXY, or SCOPUS_INSTTOKEN."
)
INSTTOKEN_NOTE = (
    " An insttoken is configured: Elsevier also rejects requests whose "
    "insttoken is revoked or not associated with this API key, so the "
    "token may be the cause rather than the key."
)

# Transport failures worth retrying; other request errors are deterministic.
RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)

# Concurrent per-year requests in yearly_counts.
YEARLY_CONCURRENCY = 4
# ISSNs per Serial Title request (the API pages at 25 entries).
SERIAL_BATCH = 25

# The REF view returns at most this many references per response.
REF_PAGE = 40
# Upper bound on REF pages per document (2,000 references).
REF_MAX_PAGES = 50
REF_RANGE_ERROR = "'startref' or 'refcount' parameter missing or invalid"

# Stable, existing record used by diagnose_connection as a metadata canary.
CANARY_SCOPUS_ID = "85007305299"
CANARY_QUERY = "ALL(gene)"
# Capability canaries. The full-text canary must be a subscription (non-OA)
# ScienceDirect article, so full text proves entitlement rather than open
# access: Hummon & Doreian (1989), Social Networks, ~53k chars when entitled.
CANARY_FULLTEXT_DOI = "10.1016/0378-8733(89)90017-8"
CANARY_ISSN = "0276-7783"  # MIS Quarterly
# Unentitled article requests can return 200 with abstract-length text only.
FULLTEXT_MIN_CHARS = 5000

# Which tools each capability gates, so diagnostics can say what will fail.
CAPABILITY_TOOLS = {
    'search': ['search_scopus', 'search_all', 'get_citing_papers',
               'co_citation', 'citation_lineage (forward)',
               'publication_counts (Scopus)', 'search_authors (Scopus)'],
    'references': ['get_references', 'bibliographic_coupling',
                   'citation_lineage (backward)'],
    'fulltext': ['get_fulltext (ScienceDirect step; falls back to OA/abstract)'],
    'serial_title': ['get_journal_metrics (Scopus)'],
}

class ScopusClient:
    """
    Async client for interacting with the Elsevier Scopus API.
    Handles authentication, caching, rate limiting, and retries.
    """
    def __init__(self):
        self.api_key = get_api_key()
        self.cache_config = get_cache_config()
        # Per-request page size for search_all; 25 by default (see get_page_size).
        self.page_size = get_page_size()
        insttoken, self.insttoken_source = resolve_insttoken()
        self.has_insttoken = bool(insttoken)
        # Proxy applies to api.elsevier.com only; it is how an off-network
        # machine borrows an institutional IP without a full VPN.
        self.proxy = get_proxy()
        self.max_retries = get_max_retries()
        self.headers = {
            'X-ELS-APIKey': self.api_key,
            'Accept': 'application/json',
            'User-Agent': USER_AGENT,
        }
        if insttoken:
            self.headers['X-ELS-Insttoken'] = insttoken
        # Log where credentials came from, never their values.
        logger.info(
            "Scopus client ready (insttoken: %s, proxy: %s)",
            f"from {self.insttoken_source}" if insttoken
            else "not set, subscriber features require institutional network",
            proxy_scheme(self.proxy) or "none",
        )
        # Initialize CacheManager with default expiration
        self.cache = CacheManager(expiration_seconds=self.cache_config['default'])
        self.client = httpx.AsyncClient(
            headers=self.headers,
            timeout=30.0,
            follow_redirects=True,
            proxy=self.proxy,
        )
        self.quota_info = {} # Store latest quota headers

    async def close(self):
        """Closes the underlying HTTP client."""
        await self.client.aclose()

    async def get_quota_status(self) -> Dict[str, Any]:
        """Returns the latest known quota status."""
        return self.quota_info

    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, use_cache: bool = True, ttl: Optional[int] = None) -> Dict[str, Any]:
        """
        Internal method to handle API requests with caching, rate limiting, and retries.
        """
        url = urljoin(BASE_URL, endpoint)
        
        # Check cache (Synchronous cache access is fast enough)
        if use_cache and method.upper() == 'GET':
            cached = self.cache.get(url, params)
            if cached:
                logger.debug(f"Cache hit for {url}")
                return cached

        # Retries apply to GET only (all Scopus endpoints here are GET);
        # POSTs would not be safe to replay.
        can_retry = method.upper() == 'GET'
        attempt = 1
        max_attempts = (1 + self.max_retries) if can_retry else 1

        while True:
            try:
                response = await self.client.request(method, url, params=params)
            except RETRYABLE_TRANSPORT_ERRORS as e:
                if attempt >= max_attempts:
                    raise Exception(
                        f"Network error contacting Scopus API for {endpoint} "
                        f"after {attempt} attempt(s): {type(e).__name__}: {e}"
                    ) from e
                delay = self._backoff_delay(attempt)
                # Log endpoint only — the query string can be long and noisy.
                logger.info(
                    f"Retrying {endpoint} (attempt {attempt + 1}/{max_attempts}) "
                    f"after {type(e).__name__}; sleeping {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue
            except httpx.RequestError as e:
                raise Exception(f"Request to Scopus API failed for {endpoint}: {e}") from e

            # Update Quota Info from Headers
            self._update_quota_info(response.headers)

            status = response.status_code
            if status == 429 or 500 <= status < 600:
                if attempt < max_attempts:
                    if status == 429:
                        delay = self._retry_after_delay(response.headers) or self._backoff_delay(attempt)
                    else:
                        delay = self._backoff_delay(attempt)
                    logger.info(
                        f"Retrying {endpoint} (attempt {attempt + 1}/{max_attempts}) "
                        f"after HTTP {status}; sleeping {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                if status == 429:
                    quota_snap = {k: response.headers.get(k, '') for k in (
                        'X-RateLimit-Remaining', 'X-RateLimit-Reset', 'X-ELS-Status')}
                    raise Exception(
                        f"Rate limit exceeded (429) after retries. "
                        f"Quota headers: {quota_snap}"
                    )
                raise Exception(
                    f"Scopus API server error {status} for {endpoint} "
                    f"after {attempt} attempt(s)"
                )

            try:
                response.raise_for_status()
                data = response.json()

                # Save to cache if GET
                if use_cache and method.upper() == 'GET':
                    self.cache.set(url, data, params, ttl=ttl)

                return data

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 401:
                    logger.error("Authentication failed. Check your API key.")
                    is_ref = params is not None and params.get('view') == 'REF'
                    quota_snap = {k: e.response.headers.get(k, '') for k in (
                        'X-ELS-Status', 'X-RateLimit-Remaining',
                        'X-RateLimit-Reset', 'X-ELS-Quota-Remaining-Weekly')}
                    quota_str = ', '.join(f'{k}={v}' for k, v in quota_snap.items() if v)
                    if is_ref:
                        msg = (
                            "REF-view fetch failed: Invalid API Key — likely a "
                            "REF-view entitlement or quota limit, not a bad key "
                            "(key works for other endpoints)."
                        )
                    else:
                        msg = "Authentication failed: Invalid API Key"
                    if quota_str:
                        msg += f" Quota/rate headers: [{quota_str}]"
                    if self.has_insttoken:
                        msg += INSTTOKEN_NOTE
                    raise Exception(msg) from e
                elif status == 404:
                    logger.info(f"Resource not found: {url}")
                    return {} 
                else:
                    # Surface the response body and the query so malformed-syntax
                    # 400s (bad REF()/field syntax) are distinguishable from
                    # entitlement 403s without guesswork.
                    body = ''
                    try:
                        body = e.response.text[:500]
                    except Exception:
                        pass
                    q = params.get('query') if params else None
                    msg = (
                        f"Scopus API error {status} for {url} "
                        f"(query={q!r}): {body}"
                    )
                    if status == 400 and self._is_entitlement_400(e.response):
                        msg += ENTITLEMENT_NOTE
                    raise Exception(msg) from e
            except ValueError:
                logger.error("Failed to parse JSON response")
                raise Exception("Invalid JSON response from Scopus API")

    @staticmethod
    def _is_entitlement_400(response: httpx.Response) -> bool:
        """True when a 400 body carries Elsevier's translating-query signature.

        Off-network (unentitled) keys get this exact error for every search
        query, valid or not, so it cannot be trusted as a syntax error alone.
        """
        try:
            status = response.json().get('service-error', {}).get('status', {})
            return status.get('statusText') == ENTITLEMENT_400_STATUS_TEXT
        except Exception:
            return False

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with full jitter: uniform(0, 1s * 2^(attempt-1))."""
        return random.uniform(0, 2 ** (attempt - 1))

    @staticmethod
    def _retry_after_delay(headers: httpx.Headers) -> Optional[float]:
        """Parses a numeric Retry-After header, capped at 10 s."""
        raw = headers.get('Retry-After')
        if raw is None:
            return None
        try:
            return min(max(float(raw), 0.0), 10.0)
        except (TypeError, ValueError):
            return None

    def _update_quota_info(self, headers: httpx.Headers):
        """Updates internal quota state from response headers."""
        self.quota_info = {
            'limit': headers.get('X-RateLimit-Limit', 'unknown'),
            'remaining': headers.get('X-RateLimit-Remaining', 'unknown'),
            'reset': headers.get('X-RateLimit-Reset', 'unknown'),
            'status': 'OK'
        }

    async def search_scopus(self, query: str, count: int = 25, start: int = 0, sort: str = 'coverDate') -> Dict[str, Any]:
        """
        Searches Scopus API.
        Endpoint: content/search/scopus
        """
        params = {
            'query': query,
            'count': count,
            'start': start,
            'sort': sort,
            'view': 'STANDARD'
        }
        return await self._request('GET', 'content/search/scopus', params, ttl=self.cache_config['search'])

    async def get_abstract(self, scopus_id: str) -> Dict[str, Any]:
        """
        Retrieves abstract details, trying progressively less-privileged views.
        Returns the best available response plus diagnostic metadata.
        Endpoint: content/abstract/scopus_id/{id}
        """
        clean_id = scopus_id.replace('SCOPUS_ID:', '')
        endpoint = f'content/abstract/scopus_id/{clean_id}'
        last_els_status: str = ''
        last_http_status: int = 0

        for view in ('FULL', 'META_ABS', 'META'):
            try:
                data = await self._request(
                    'GET', endpoint,
                    params={'view': view},
                    ttl=self.cache_config['abstract'],
                )
                # Attach diagnostic info so callers can see what arrived
                data['_view_used'] = view
                data['_els_status'] = 'OK'
                return data
            except Exception as exc:
                msg = str(exc)
                # Extract ELS-Status from the error message if present
                import re as _re
                is_auth_error = any(s in msg for s in (
                    'AUTHORIZATION_ERROR', 'Authentication failed', 'not authorized',
                    '401', '403',
                ))
                if is_auth_error:
                    last_els_status = 'AUTHORIZATION_ERROR'
                    last_http_status = 401
                    logger.info(f"Abstract view={view} denied, trying next view. Reason: {msg[:120]}")
                    continue
                raise

        # All views failed — return a minimal error sentinel
        return {
            '_view_used': None,
            '_els_status': last_els_status or 'AUTHORIZATION_ERROR',
            '_http_status': last_http_status,
        }

    async def get_author(self, author_id: str) -> Dict[str, Any]:
        """
        Retrieves author profile.
        Endpoint: content/author/author_id/{id}
        """
        clean_id = author_id.replace('AUTHOR_ID:', '')
        return await self._request('GET', f'content/author/author_id/{clean_id}', ttl=self.cache_config['author'])

    async def get_abstract_by(self, value: str, id_type: str = 'scopus_id') -> Dict[str, Any]:
        """
        Retrieves an abstract record by any supported identifier type.
        Endpoint: content/abstract/{id_type}/{value}

        id_type is one of: 'scopus_id', 'eid', 'doi', 'pii'.
        Used by resolve_identifier to cross-reference IDs (Scopus ID / EID / DOI).
        """
        id_type = (id_type or 'scopus_id').lower()
        if id_type == 'scopus_id':
            endpoint = f"content/abstract/scopus_id/{to_scopus_id(value)}"
        elif id_type == 'eid':
            endpoint = f"content/abstract/eid/{str(value).strip()}"
        elif id_type == 'doi':
            endpoint = f"content/abstract/doi/{str(value).strip()}"
        elif id_type == 'pii':
            endpoint = f"content/abstract/pii/{str(value).strip()}"
        else:
            raise ValueError(f"Unsupported id_type: {id_type}")
        return await self._request('GET', endpoint, ttl=self.cache_config['abstract'])

    async def get_references(self, scopus_id: str) -> Dict[str, Any]:
        """
        Retrieves the cited-reference list (backward citations) for a document
        via the Abstract Retrieval REF view.
        Endpoint: content/abstract/scopus_id/{id}?view=REF

        Note: the REF view requires an entitled (subscriber) key; an
        unentitled key returns 403, surfaced as an error by _request.

        The REF view returns REF_PAGE references per response, whatever the
        list length, so this pages with 'startref'/'refcount' until
        '@total-references' is reached and merges every page into the first
        response. Without it, long reference lists were silently cut at 40.
        """
        endpoint = f"content/abstract/scopus_id/{to_scopus_id(scopus_id)}"
        ttl = self.cache_config['abstract']
        data = await self._request('GET', endpoint, {'view': 'REF'}, ttl=ttl)
        refs_block = ((data.get('abstracts-retrieval-response') or {})
                      .get('references'))
        if not isinstance(refs_block, dict):
            return data
        refs = refs_block.get('reference') or []
        if isinstance(refs, dict):
            refs = [refs]
        try:
            total = int(refs_block.get('@total-references') or 0)
        except (TypeError, ValueError):
            total = 0
        pages = 1
        shrunk = False
        while len(refs) < total and pages < REF_MAX_PAGES:
            # refcount past the end of the list is a 400, so the last page
            # asks for the remainder only.
            try:
                page = await self._request(
                    'GET', endpoint,
                    {'view': 'REF', 'startref': len(refs) + 1,
                     'refcount': min(REF_PAGE, total - len(refs))},
                    ttl=ttl,
                )
            except Exception as exc:
                # Scopus can report one more reference than it serves
                # (@total-references 172, last retrievable 171), so a page
                # reaching the phantom entry 400s. Retry once, one shorter.
                if shrunk or REF_RANGE_ERROR not in str(exc):
                    raise
                shrunk = True
                total -= 1
                continue
            more = (((page.get('abstracts-retrieval-response') or {})
                     .get('references') or {}).get('reference') or [])
            if isinstance(more, dict):
                more = [more]
            if not more:
                break
            refs.extend(more)
            pages += 1
        refs_block['reference'] = refs
        return data

    async def yearly_counts(self, query: str, from_year: int, to_year: int) -> Dict[int, int]:
        """
        Scopus hits per publication year for a query: one count=1 search per
        year, reading only opensearch:totalResults. Requests only the
        identifier field to keep responses small; runs YEARLY_CONCURRENCY at
        a time. Costs one search request per year.
        """
        semaphore = asyncio.Semaphore(YEARLY_CONCURRENCY)

        async def count(year: int):
            async with semaphore:
                data = await self._request(
                    'GET', 'content/search/scopus',
                    {'query': f'({query}) AND PUBYEAR = {year}', 'count': 1,
                     'field': 'dc:identifier'},
                    ttl=self.cache_config['search'],
                )
            total = (data.get('search-results') or {}).get('opensearch:totalResults')
            return year, int(total or 0)

        pairs = await asyncio.gather(*(count(y) for y in range(from_year, to_year + 1)))
        return dict(pairs)

    async def search_authors(self, query: str, count: int = 10) -> Dict[str, Any]:
        """
        Scopus Author Search (content/search/author); results come ranked by
        document count. Needs subscriber entitlement. The endpoint throttles
        quickly; 429s go through the usual retries.
        """
        return await self._request(
            'GET', 'content/search/author', {'query': query, 'count': count},
            ttl=self.cache_config['author'],
        )

    async def serial_titles(self, issns: list) -> list:
        """
        Serial Title entries for compact ISSNs, SERIAL_BATCH per request.
        ISSNs Serial Title does not know are simply absent from the result
        (a batch of only unknown ISSNs is a 404, returned by _request as {}).
        Never pass Scopus source IDs here: the API ignores them and returns
        an unrelated alphabetical list.
        """
        entries = []
        for i in range(0, len(issns), SERIAL_BATCH):
            data = await self._request(
                'GET', 'content/serial/title',
                {'issn': ','.join(issns[i:i + SERIAL_BATCH])},
                ttl=self.cache_config['default'],
            )
            batch = (data.get('serial-metadata-response') or {}).get('entry') or []
            entries.extend(e for e in batch if isinstance(e, dict))
        return entries

    async def source_id_issns(self, source_id: str) -> Optional[Dict[str, Any]]:
        """
        Print and electronic ISSN of a Scopus source ID, read from one
        count=1 search (SRCID(...)); None when the source has no records.
        Needs search entitlement.
        """
        data = await self._request(
            'GET', 'content/search/scopus',
            {'query': f'SRCID({source_id})', 'count': 1,
             'field': 'prism:issn,prism:eIssn,prism:publicationName,source-id'},
            ttl=self.cache_config['default'],
        )
        entries = (data.get('search-results') or {}).get('entry') or []
        entry = entries[0] if entries else {}
        if not entry or entry.get('error'):
            return None
        return {'issn': entry.get('prism:issn'), 'eissn': entry.get('prism:eIssn'),
                'name': entry.get('prism:publicationName')}

    async def search_all(self, query: str, max_results: int = 200, sort: str = 'coverDate') -> Dict[str, Any]:
        """
        Pages through a Scopus search and returns aggregated results up to max_results.

        Uses start-based paging (ceiling 5,000) when max_results <= 5,000.  Switches to
        cursor=* deep paging when max_results > 5,000 — the two modes are mutually
        exclusive per Scopus API rules.  Deduplicates across pages by dc:identifier.

        Page size is self.page_size — 25 by default, the per-request 'count' ceiling for
        non-institutional keys; see config.get_page_size for raising it.

        Paging is guaranteed to terminate: each loop stops on an empty page, a short
        page, a page that adds no new identifiers, a cursor that fails to advance, or
        a hard page cap.  Without those guards a misbehaving API — one returning a full
        batch alongside an unchanging @next cursor — would spin forever.
        """
        page_size = max(1, int(self.page_size))
        CURSOR_CEILING = 5000

        all_entries: list = []
        seen_ids: set = set()
        total_available: int = 0
        note: Optional[str] = None

        # Backstop for pathological paging.  Honest paging needs at most
        # ceil(max_results / page_size) requests; double that plus slack so
        # duplicate-heavy result sets still page through normally, and treat
        # anything beyond it as the API failing to make progress.
        max_pages = math.ceil(max_results / page_size) * 2 + 10
        pages = 0
        hit_page_cap = False

        use_cursor = max_results > CURSOR_CEILING

        if use_cursor:
            cursor: str = '*'
            while len(all_entries) < max_results:
                if pages >= max_pages:
                    hit_page_cap = True
                    break
                pages += 1
                batch = min(page_size, max_results - len(all_entries))
                params: Dict[str, Any] = {
                    'query': query,
                    'count': batch,
                    'cursor': cursor,
                    'sort': sort,
                    'view': 'STANDARD',
                }
                data = await self._request(
                    'GET', 'content/search/scopus', params,
                    use_cache=True, ttl=self.cache_config['search'],
                )
                sr = data.get('search-results', {})
                if not total_available:
                    try:
                        total_available = int(sr.get('opensearch:totalResults', 0))
                    except (ValueError, TypeError):
                        pass
                entries = sr.get('entry', [])
                if not entries:
                    break
                added = 0
                for e in entries:
                    uid = e.get('dc:identifier') or e.get('eid') or ''
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        all_entries.append(e)
                        added += 1
                next_cursor = (sr.get('cursor') or {}).get('@next')
                if not next_cursor:
                    break
                if len(entries) < batch:
                    break  # API returned fewer than requested — results exhausted
                if next_cursor == cursor:
                    # The cursor has stopped advancing: following it again would
                    # re-fetch this exact page forever.
                    logger.warning(
                        f"Deep paging stopped: @next cursor did not advance past {cursor!r}."
                    )
                    break
                if added == 0:
                    break  # Page contained only records already seen
                cursor = next_cursor
        else:
            start = 0
            while len(all_entries) < max_results and start < CURSOR_CEILING:
                if pages >= max_pages:
                    hit_page_cap = True
                    break
                pages += 1
                batch = min(page_size, max_results - len(all_entries), CURSOR_CEILING - start)
                data = await self.search_scopus(query, count=batch, start=start, sort=sort)
                sr = data.get('search-results', {})
                if not total_available:
                    try:
                        total_available = int(sr.get('opensearch:totalResults', 0))
                    except (ValueError, TypeError):
                        pass
                entries = sr.get('entry', [])
                if not entries:
                    break
                added = 0
                for e in entries:
                    uid = e.get('dc:identifier') or e.get('eid') or ''
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        all_entries.append(e)
                        added += 1
                start += len(entries)
                if len(entries) < batch or added == 0:
                    break  # Exhausted or only duplicates returned

        fetched = len(all_entries)
        truncated = bool(total_available and fetched < total_available)
        if truncated:
            note = (
                f"Result set capped: fetched {fetched} of {total_available} total "
                f"(max_results={max_results})."
            )
        if hit_page_cap:
            cap_note = (
                f"Paging stopped at the {max_pages}-page safety cap without reaching "
                f"max_results={max_results}: the API kept returning pages that added "
                f"few or no new records."
            )
            logger.warning(cap_note)
            note = f"{note} {cap_note}" if note else cap_note

        return {
            'search-results': {'entry': all_entries},
            '_meta': {
                'total_fetched': fetched,
                'total_available': total_available,
                'truncated': truncated,
                'pages_fetched': pages,
                'hit_page_cap': hit_page_cap,
                'note': note,
            },
        }

    async def get_sciencedirect_fulltext(self, doi: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the ScienceDirect full-text-retrieval-response for a DOI.
        Returns None when the caller lacks entitlement (401/403) or the article
        is not on ScienceDirect (404).  Off-network, requires SCOPUS_INSTTOKEN or
        SCOPUS_PROXY for most full-text content; without either the response is
        typically abstract-only.
        Endpoint: content/article/doi/{doi}
        """
        try:
            return await self._request(
                'GET',
                f'content/article/doi/{doi.strip()}',
                use_cache=False,
            )
        except Exception as exc:
            msg = str(exc).lower()
            if any(code in msg for code in ('401', '403', 'authentication', 'entitlement')):
                logger.info(f"ScienceDirect fulltext not entitled for doi={doi}: {exc}")
                return None
            raise

    async def diagnose_connection(self) -> Dict[str, Any]:
        """
        Runs four ordered health checks (config, reachability, metadata
        entitlement, search entitlement) and returns a structured report
        with a one-line verdict.  Never reports credential values.
        """
        report: Dict[str, Any] = {}

        # 1. Config (no network): presence and source only, never values.
        # Reflects what this running client uses, so a token added to the
        # Keychain after startup shows as absent until the server restarts.
        _, api_key_source = resolve_api_key()
        report['config'] = {
            'api_key_present': bool(self.api_key),
            'api_key_source': api_key_source,
            'insttoken_present': self.has_insttoken,
            'insttoken_source': self.insttoken_source,
            'proxy': proxy_scheme(self.proxy),
        }

        # 2. Reachability: any HTTP status counts as reachable; only
        # transport errors/timeouts do not.  Behind a proxy the direct TCP
        # probe would time a path requests never take, so it is skipped.
        reachability: Dict[str, Any] = {
            'reachable': False,
            'connect_seconds': None,
            'total_seconds': None,
            'via_proxy': bool(self.proxy),
        }
        if not self.proxy:
            try:
                start = time.monotonic()
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection('api.elsevier.com', 443), timeout=8.0
                )
                reachability['connect_seconds'] = round(time.monotonic() - start, 3)
                writer.close()
            except Exception:
                pass
        try:
            start = time.monotonic()
            await self.client.get(BASE_URL, timeout=8.0)
            reachability['total_seconds'] = round(time.monotonic() - start, 3)
            reachability['reachable'] = True
        except Exception as exc:
            reachability['error'] = type(exc).__name__
        report['reachability'] = reachability

        # 3. Metadata entitlement: canary abstract via the existing code path.
        metadata: Dict[str, Any] = {'status': 'ok', 'canary_id': CANARY_SCOPUS_ID}
        try:
            await self._request(
                'GET',
                f'content/abstract/scopus_id/{CANARY_SCOPUS_ID}',
                use_cache=False,
            )
        except Exception as exc:
            msg = str(exc)
            if any(m in msg for m in ('401', '403', 'Authentication failed')):
                metadata['status'] = 'auth_failed'
            else:
                metadata['status'] = 'error'
            metadata['detail'] = msg[:300]
        report['metadata'] = metadata

        # 4. Search entitlement: canary query via the existing search path.
        search: Dict[str, Any] = {'status': 'ok', 'canary_query': CANARY_QUERY}
        try:
            await self._request(
                'GET',
                'content/search/scopus',
                {'query': CANARY_QUERY, 'count': 1, 'start': 0,
                 'sort': 'coverDate', 'view': 'STANDARD'},
                use_cache=False,
            )
        except Exception as exc:
            msg = str(exc)
            if ENTITLEMENT_400_STATUS_TEXT in msg:
                search['status'] = 'entitlement_missing'
            elif any(m in msg for m in ('401', '403', 'Authentication failed')):
                search['status'] = 'auth_failed'
            elif 'Network error' in msg or 'Timeout' in msg or 'timed out' in msg:
                search['status'] = 'network'
            else:
                search['status'] = 'error'
            search['detail'] = msg[:300]
        report['search'] = search

        # 5. Per-API capabilities beyond search. Elsevier entitles endpoints
        # separately, so a working search says nothing about REF view or full
        # text. Probed concurrently to keep the diagnosis quick.
        references, fulltext, serial_title = await asyncio.gather(
            self._probe_references(), self._probe_fulltext(), self._probe_serial_title()
        )
        report['capabilities'] = {
            'references': references,
            'fulltext': fulltext,
            'serial_title': serial_title,
        }
        report['unavailable_tools'] = self._unavailable_tools(report)

        report['entitlement_via'] = self._entitlement_route(report)
        report['verdict'] = self._build_verdict(report)
        return report

    @staticmethod
    def _classify_probe_error(msg: str) -> str:
        if any(m in msg for m in ('401', '403', 'Authentication failed',
                                  'REF-view fetch failed', 'AUTHORIZATION')):
            return 'entitlement_missing'
        if 'Network error' in msg or 'Timeout' in msg or 'timed out' in msg:
            return 'network'
        return 'error'

    async def _probe_references(self) -> Dict[str, Any]:
        """REF view on the canary record: are reference lists entitled?"""
        result: Dict[str, Any] = {'status': 'ok'}
        try:
            data = await self._request(
                'GET', f'content/abstract/scopus_id/{CANARY_SCOPUS_ID}',
                {'view': 'REF', 'count': 1}, use_cache=False,
            )
            refs = (data.get('abstracts-retrieval-response') or {}).get('references') or {}
            if not refs:
                result['status'] = 'empty'
        except Exception as exc:
            result['status'] = self._classify_probe_error(str(exc))
            result['detail'] = str(exc)[:300]
        return result

    async def _probe_fulltext(self) -> Dict[str, Any]:
        """Subscription article: full body, abstract only, or refused?"""
        result: Dict[str, Any] = {'status': 'ok', 'canary_doi': CANARY_FULLTEXT_DOI}
        try:
            data = await self._request(
                'GET', f'content/article/doi/{CANARY_FULLTEXT_DOI}', use_cache=False,
            )
            root = data.get('full-text-retrieval-response') or {}
            chars = len(str(root.get('originalText') or ''))
            result['chars'] = chars
            if chars < FULLTEXT_MIN_CHARS:
                result['status'] = 'abstract_only'
        except Exception as exc:
            result['status'] = self._classify_probe_error(str(exc))
            result['detail'] = str(exc)[:300]
        return result

    async def _probe_serial_title(self) -> Dict[str, Any]:
        """Serial Title API: journal metrics (SJR, SNIP, CiteScore)."""
        result: Dict[str, Any] = {'status': 'ok', 'canary_issn': CANARY_ISSN}
        try:
            data = await self._request(
                'GET', f'content/serial/title/issn/{CANARY_ISSN}', use_cache=False,
            )
            if not (data.get('serial-metadata-response') or {}).get('entry'):
                result['status'] = 'empty'
        except Exception as exc:
            result['status'] = self._classify_probe_error(str(exc))
            result['detail'] = str(exc)[:300]
        return result

    @staticmethod
    def _unavailable_tools(report: Dict[str, Any]) -> list:
        """Tools gated by a capability that is not 'ok', in a stable order."""
        statuses = {'search': report['search']['status']}
        statuses.update({k: v['status'] for k, v in report.get('capabilities', {}).items()})
        tools = []
        for capability, status in statuses.items():
            if status != 'ok':
                tools.extend(CAPABILITY_TOOLS.get(capability, []))
        return tools

    @staticmethod
    def _entitlement_route(report: Dict[str, Any]) -> Optional[str]:
        """Names the configured route that entitles search, if search works.

        Elsevier does not say which credential it honoured, so this reports
        the strongest configured route: an insttoken works from anywhere, a
        proxy lends an institutional IP, otherwise it is this machine's IP.
        """
        if report['search']['status'] != 'ok':
            return None
        config = report.get('config', {})
        if config.get('insttoken_present'):
            return 'insttoken'
        if config.get('proxy'):
            return 'proxy'
        return 'network_ip'

    @staticmethod
    def _build_verdict(report: Dict[str, Any]) -> str:
        """Collapses the checks into a one-line, user-relayable verdict.

        The list of affected tools is in report['unavailable_tools']."""
        metadata = report['metadata']['status']
        search = report['search']['status']
        reachability = report['reachability']

        config = report.get('config', {})
        if 'auth_failed' in (metadata, search):
            verdict = "API key rejected; check SCOPUS_API_KEY."
            if config.get('insttoken_present'):
                verdict += (
                    " An insttoken is also configured; a revoked token, or one "
                    "not associated with this API key, fails the same way. "
                    "Remove it temporarily to tell the two apart."
                )
        elif search == 'entitlement_missing' and metadata == 'ok' and config.get('insttoken_present'):
            verdict = (
                "An insttoken is configured but Scopus Search still lacks "
                "subscriber entitlement. The token is probably not associated "
                "with this API key, or has been revoked; ask Elsevier support "
                "to link it to the key."
            )
        elif search == 'entitlement_missing' and metadata == 'ok' and config.get('proxy'):
            verdict = (
                "Requests go through SCOPUS_PROXY but Scopus Search lacks "
                "subscriber entitlement. The proxy's exit IP is not in your "
                "institution's subscribed range, or the tunnel is down."
            )
        elif search == 'entitlement_missing' and metadata == 'ok':
            verdict = (
                "API key authenticates but Scopus Search lacks subscriber "
                "entitlement. You are likely off your institution's network. "
                "Connect the institutional VPN, route Elsevier traffic through "
                "an on-campus host with SCOPUS_PROXY, or set SCOPUS_INSTTOKEN "
                "(request an institutional token via your library / Elsevier support)."
            )
        elif metadata == 'ok' and search == 'ok':
            verdict = "Connection and entitlement healthy."
            capabilities = report.get('capabilities', {})
            limited = [k for k, v in capabilities.items() if v.get('status') != 'ok']
            if limited:
                verdict = (
                    "Search works, but not every API is entitled: "
                    f"{', '.join(limited)} unavailable."
                )
        elif not reachability['reachable'] or search == 'network':
            verdict = "api.elsevier.com is not reachable; check your network connection."
        else:
            verdict = (
                "One or more checks failed for an unrecognized reason; "
                "see per-check details."
            )

        connect = reachability.get('connect_seconds')
        if reachability.get('via_proxy'):
            # No direct TCP probe behind a proxy; judge by the full request.
            total = reachability.get('total_seconds')
            degraded = reachability['reachable'] and (total is None or total > 4.0)
        else:
            degraded = reachability['reachable'] and (connect is None or connect > 2.0)
        if degraded:
            verdict += (
                " Network path to api.elsevier.com is degraded; retries "
                "are enabled but expect failures."
            )
        return verdict

    async def get_citing_papers(self, scopus_id: str, count: int = 25, start: int = 0, sort: str = 'coverDate') -> Dict[str, Any]:
        """
        Retrieves forward citations via a Search API REF() query.
        Builds REF(2-s2.0-<id>) using the centralized EID normalization,
        which the Search API accepts (the bare-id REFEID(<id>) form 400s).
        """
        query = f"REF({to_eid(scopus_id)})"
        return await self.search_scopus(query, count=count, start=start, sort=sort)
