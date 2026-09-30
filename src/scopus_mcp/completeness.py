"""Reference-list completeness: compare a retrieved reference list with an
independent count of the paper's references.

Scopus sometimes indexes only part of a paper's reference list (Miranda et
al. 2015, MISQ, has 32 references in Scopus, with long alphabetical gaps). A
paper whose list is short drops out of a citation network without any
error. The comparison count comes from the first source that has one:

1. Crossref `reference-count`, deposited by the publisher (the full list);
2. OpenAlex `referenced_works_count` (references it matched to works: a
   lower bound);
3. Semantic Scholar `referenceCount` (likewise a lower bound).

Crossref has no count for many IS journals (MISQ's 10.25300 and JSTOR's
10.2307 DOIs, AIS eLibrary), hence the fallbacks.
"""
import asyncio
import logging
import os
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx

from . import USER_AGENT
from .config import contact_user_agent, get_contact_email, resolve_semantic_scholar_key

logger = logging.getLogger(__name__)

CROSSREF_WORKS = 'https://api.crossref.org/works/'
S2_BATCH = 'https://api.semanticscholar.org/graph/v1/paper/batch'
S2_BATCH_SIZE = 500
CROSSREF_CONCURRENCY = 4


def _env_number(name: str, default: float, cast=float):
    try:
        return cast(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# A list is SHORT when BOTH hold: it holds less than SHORT_RATIO of the
# comparison count, and it misses at least SHORT_MIN_MISSING references.
# Small differences are normal: Crossref counts footnotes and data
# citations, and OpenAlex/Semantic Scholar count only what they matched.
SHORT_RATIO = _env_number('SCOPUS_COMPLETENESS_RATIO', 0.9)
SHORT_MIN_MISSING = _env_number('SCOPUS_COMPLETENESS_MIN_MISSING', 5, int)
RULE_TEXT = (f"short = fewer than {SHORT_RATIO:.0%} of the comparison count and at "
             f"least {SHORT_MIN_MISSING} references missing (SCOPUS_COMPLETENESS_RATIO, "
             "SCOPUS_COMPLETENESS_MIN_MISSING)")

# Found counts do not change within a session; repeated builds reuse them.
_CACHE: Dict[str, Tuple[Optional[int], Optional[str]]] = {}


def assess(retrieved: Optional[int], external: Optional[int]) -> str:
    """'ok', 'short' or 'unknown' (no DOI, or no count anywhere)."""
    if retrieved is None or not external:
        return 'unknown'
    missing = external - retrieved
    if missing >= SHORT_MIN_MISSING and retrieved < SHORT_RATIO * external:
        return 'short'
    return 'ok'


async def crossref_reference_counts(dois: Iterable[str]) -> Dict[str, Optional[int]]:
    """DOI (lower-case) -> Crossref reference-count; None when unavailable.

    Crossref reports 0 when the publisher deposited no references; that is
    returned as None (unknown), not as an empty list.
    """
    dois = [d.lower() for d in dict.fromkeys(d for d in dois if d)]
    out: Dict[str, Optional[int]] = {}
    if not dois:
        return out
    params = {}
    email = get_contact_email()
    if email:
        params['mailto'] = email  # Crossref's polite pool
    semaphore = asyncio.Semaphore(CROSSREF_CONCURRENCY)
    async with httpx.AsyncClient(
        timeout=20.0, follow_redirects=True,
        headers={'User-Agent': contact_user_agent(USER_AGENT)},
    ) as http:
        async def one(doi: str):
            async with semaphore:
                try:
                    r = await http.get(CROSSREF_WORKS + doi, params=params)
                    if r.status_code != 200:
                        out[doi] = None
                        return
                    msg: Dict[str, Any] = r.json().get('message') or {}
                    count = msg.get('reference-count') or msg.get('references-count')
                    out[doi] = int(count) if count else None
                except Exception as exc:  # network trouble must not sink a network build
                    logger.info(f"Crossref reference count failed for {doi}: {exc}")
                    out[doi] = None
        await asyncio.gather(*(one(d) for d in dois))
    return out


async def semantic_scholar_reference_counts(dois: Iterable[str]) -> Dict[str, Optional[int]]:
    """DOI (lower-case) -> Semantic Scholar referenceCount, in batches."""
    dois = [d.lower() for d in dict.fromkeys(d for d in dois if d)]
    out: Dict[str, Optional[int]] = {}
    headers = {'User-Agent': contact_user_agent(USER_AGENT)}
    key, _ = resolve_semantic_scholar_key()
    if key:
        headers['x-api-key'] = key
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as http:
        for i in range(0, len(dois), S2_BATCH_SIZE):
            chunk = dois[i:i + S2_BATCH_SIZE]
            try:
                r = await http.post(S2_BATCH, params={'fields': 'referenceCount'},
                                    json={'ids': [f'DOI:{d}' for d in chunk]})
                if r.status_code != 200:
                    continue
                for doi, paper in zip(chunk, r.json()):
                    if paper:
                        out[doi] = paper.get('referenceCount') or None
            except Exception as exc:
                logger.info(f"Semantic Scholar reference counts failed: {exc}")
    return out


async def external_reference_counts(dois: Iterable[str], openalex=None
                                    ) -> Dict[str, Tuple[Optional[int], Optional[str]]]:
    """DOI (lower-case) -> (count, source) from Crossref, then OpenAlex
    (when a client is given), then Semantic Scholar. (None, None) when no
    source has a count."""
    wanted = [d.lower() for d in dict.fromkeys(d for d in dois if d)]
    out = {d: _CACHE[d] for d in wanted if d in _CACHE}
    todo = [d for d in wanted if d not in out]
    if todo:
        for d, n in (await crossref_reference_counts(todo)).items():
            if n:
                out[d] = (n, 'crossref')
        todo = [d for d in todo if d not in out]
    if todo and openalex is not None:
        try:
            for d, n in (await openalex.reference_counts(todo)).items():
                if n:
                    out[d] = (n, 'openalex')
        except Exception as exc:
            logger.info(f"OpenAlex reference counts failed: {exc}")
        todo = [d for d in todo if d not in out]
    if todo:
        for d, n in (await semantic_scholar_reference_counts(todo)).items():
            if n:
                out[d] = (n, 'semantic_scholar')
    for d in wanted:
        out.setdefault(d, (None, None))
        if out[d][0]:  # misses may be transient; retry them next time
            _CACHE[d] = out[d]
    return out
