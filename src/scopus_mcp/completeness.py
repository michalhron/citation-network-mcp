"""Reference-list completeness: compare a retrieved reference list with the
count the publisher deposited at Crossref.

Scopus sometimes indexes only part of a paper's reference list (Miranda et
al. 2015 has 32 references in Scopus, with long alphabetical gaps). A paper
whose list is short drops out of a citation network without any error; the
Crossref `reference-count` is an independent count to flag it.
"""
import asyncio
import logging
from typing import Any, Dict, Iterable, Optional

import httpx

from . import USER_AGENT
from .config import contact_user_agent, get_contact_email

logger = logging.getLogger(__name__)

CROSSREF_WORKS = 'https://api.crossref.org/works/'
CROSSREF_CONCURRENCY = 4
# A list is flagged short when it holds less than this share of the
# Crossref count and misses at least SHORT_MIN_MISSING references (small
# differences are normal: Crossref counts footnotes and data citations).
SHORT_RATIO = 0.8
SHORT_MIN_MISSING = 5


def assess(retrieved: Optional[int], crossref_count: Optional[int]) -> str:
    """'ok', 'short' or 'unknown' (no DOI, or no count deposited)."""
    if retrieved is None or not crossref_count:
        return 'unknown'
    missing = crossref_count - retrieved
    if missing >= SHORT_MIN_MISSING and retrieved < SHORT_RATIO * crossref_count:
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
