"""
BibTeX for DOIs via DOI content negotiation, plus generated entries for
records without a DOI.

doi.org hands `Accept: application/x-bibtex` to the registration agency
(Crossref, DataCite, ...), so entries carry the publisher's own metadata,
including its errors. Records without a DOI, such as AIS eLibrary papers,
get a minimal entry built from Scopus or OpenAlex metadata, marked in a
`note` field so it is never mistaken for publisher metadata.
"""
import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import USER_AGENT

logger = logging.getLogger(__name__)

DOI_RESOLVER = "https://doi.org/"
CONCURRENCY = 4

_ENTRY_HEAD = re.compile(r'^\s*@(\w+)\s*\{\s*([^,\s]*)\s*,', re.DOTALL)
_PAGES = re.compile(r'(pages\s*=\s*\{[^}]*)–([^}]*\})')


def normalize_entry(text: str) -> str:
    """Trim, and turn en-dash page ranges into BibTeX's '--'."""
    text = text.strip()
    while True:
        fixed = _PAGES.sub(r'\1--\2', text)
        if fixed == text:
            return text
        text = fixed


def entry_key(entry: str) -> Optional[str]:
    m = _ENTRY_HEAD.match(entry)
    return m.group(2) if m else None


def make_keys_unique(entries: List[str]) -> List[str]:
    """Suffix repeated citation keys with a, b, c ... in order of appearance
    (the first occurrence also gets 'a' once a repeat exists)."""
    keys = [entry_key(e) for e in entries]
    counts: Dict[str, int] = {}
    for k in keys:
        if k:
            counts[k] = counts.get(k, 0) + 1
    seen: Dict[str, int] = {}
    out = []
    for entry, key in zip(entries, keys):
        if not key or counts[key] == 1:
            out.append(entry)
            continue
        n = seen.get(key, 0)
        seen[key] = n + 1
        suffix = chr(ord('a') + n) if n < 26 else str(n)
        out.append(_ENTRY_HEAD.sub(
            lambda m: f"@{m.group(1)}{{{key}{suffix},", entry, count=1))
    return out


def _escape(value: str) -> str:
    return value.replace('{', '').replace('}', '').strip()


def generated_entry(meta: Dict[str, Any], origin: str) -> Optional[str]:
    """Minimal entry from metadata: title, authors ('Surname, Initials'),
    year, venue. None without a title."""
    title = (meta.get('title') or '').strip()
    if not title:
        return None
    authors = [a for a in (meta.get('authors') or []) if a]
    year = str(meta.get('year') or '').strip()
    venue = (meta.get('venue') or '').strip()
    first = re.sub(r'[^A-Za-z]', '', authors[0].split(',')[0]) if authors else 'Anon'
    key = f"{first or 'Anon'}_{year or 'nd'}"
    is_proc = bool(re.search(r'conference|proceedings|symposium|workshop', venue, re.I))
    kind = 'inproceedings' if is_proc else 'article'
    fields = [f"  title = {{{_escape(title)}}}"]
    if authors:
        fields.append(f"  author = {{{' and '.join(_escape(a) for a in authors)}}}")
    if year:
        fields.append(f"  year = {{{year}}}")
    if venue:
        fields.append(f"  {'booktitle' if is_proc else 'journal'} = {{{_escape(venue)}}}")
    fields.append(f"  note = {{Generated from {origin} metadata; no DOI}}")
    return f"@{kind}{{{key},\n" + ",\n".join(fields) + "\n}"


async def fetch_bibtex(dois: List[str]) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """{doi: (entry, error)} for each DOI; exactly one of the pair is None."""
    semaphore = asyncio.Semaphore(CONCURRENCY)
    headers = {
        'Accept': 'application/x-bibtex; charset=utf-8',
        'User-Agent': USER_AGENT,
    }

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as http:
        async def one(doi: str):
            async with semaphore:
                try:
                    r = await http.get(DOI_RESOLVER + doi)
                except httpx.HTTPError as exc:
                    return doi, (None, f"network error: {type(exc).__name__}")
            ctype = r.headers.get('content-type', '')
            # An unknown DOI answers with an HTML error page, sometimes 200.
            if r.status_code != 200 or 'bibtex' not in ctype:
                return doi, (None, f"no BibTeX (HTTP {r.status_code})")
            entry = normalize_entry(r.text)
            if not entry.startswith('@'):
                return doi, (None, "malformed BibTeX")
            return doi, (entry, None)

        pairs = await asyncio.gather(*(one(d) for d in dois))
    return dict(pairs)
