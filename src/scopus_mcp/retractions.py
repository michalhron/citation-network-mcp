"""Retractions, withdrawals, expressions of concern and corrections, from
Crossref (which carries the Retraction Watch database since 2023).

Crossref lists the notices that update a work: a query with
filter=updates:<doi>,updates:<doi>,... returns every notice pointing at
those DOIs, each with an update-to entry naming its type.
"""
import asyncio
import logging
from typing import Any, Dict, Iterable, List

import httpx

from . import USER_AGENT
from .config import contact_user_agent, get_contact_email

logger = logging.getLogger(__name__)

CROSSREF_WORKS = 'https://api.crossref.org/works'
BATCH = 20
SEVERE = {'retraction', 'withdrawal', 'removal', 'partial_retraction'}
CONCERN = {'expression_of_concern'}


def status_of(notices: List[Dict[str, Any]]) -> str:
    """'retracted', 'concern', 'corrected' or 'ok' (most severe notice wins)."""
    types = {n['type'] for n in notices}
    if types & SEVERE:
        return 'retracted'
    if types & CONCERN:
        return 'concern'
    return 'corrected' if types else 'ok'


async def check_dois(dois: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """DOI (lower-case) -> notices [{'type', 'label', 'date', 'notice_doi',
    'source'}]; [] when none. DOIs with commas cannot go into a filter and
    are skipped."""
    dois = [d.lower() for d in dict.fromkeys(d for d in dois if d and ',' not in d)]
    out: Dict[str, List[Dict[str, Any]]] = {d: [] for d in dois}
    params_base = {'rows': 200, 'select': 'DOI,update-to'}
    email = get_contact_email()
    if email:
        params_base['mailto'] = email
    async with httpx.AsyncClient(timeout=30.0, headers={'User-Agent': contact_user_agent(USER_AGENT)}) as http:
        async def batch(chunk):
            params = dict(params_base, filter=','.join(f'updates:{d}' for d in chunk))
            try:
                r = await http.get(CROSSREF_WORKS, params=params)
                r.raise_for_status()
                items = r.json().get('message', {}).get('items') or []
            except Exception as exc:
                logger.info(f"Crossref update check failed: {exc}")
                for d in chunk:
                    out[d] = None  # unknown
                return
            for item in items:
                for upd in item.get('update-to') or []:
                    target = (upd.get('DOI') or '').lower()
                    if target in out and out[target] is not None:
                        parts = ((upd.get('updated') or {}).get('date-parts') or [[None]])[0]
                        out[target].append({
                            'type': upd.get('type'), 'label': upd.get('label'),
                            'date': '-'.join(str(p) for p in parts if p) or None,
                            'notice_doi': (item.get('DOI') or '').lower(),
                            'source': upd.get('source')})
        await asyncio.gather(*(batch(dois[i:i + BATCH]) for i in range(0, len(dois), BATCH)))
    return out
