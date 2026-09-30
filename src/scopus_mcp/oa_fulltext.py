"""Open-access full text from legitimate open sources.

Candidates come from OpenAlex (every open location, not only the best),
Semantic Scholar (open PDF and arXiv ID), arXiv (exact title match),
Europe PMC (open-access full-text XML), and, when configured, Unpaywall
(needs CONTACT_EMAIL) and CORE (needs CORE_API_KEY). They are tried
published version first, PDFs and XML before landing pages, until one
yields real full text. ResearchGate and similar sites without an API are
deliberately not used: their terms forbid automated downloading.
"""
import logging
import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

from . import USER_AGENT
from .config import (
    contact_user_agent,
    get_contact_email,
    resolve_core_key,
    resolve_semantic_scholar_key,
)
from .openalex import normalize_title

logger = logging.getLogger(__name__)


def _extract_pdf_text(content: bytes) -> Optional[str]:
    """Extract plain text from PDF bytes via pymupdf (fitz)."""
    try:
        import fitz  # pymupdf
        with fitz.open(stream=content, filetype='pdf') as doc:
            parts = [page.get_text() for page in doc]
        text = '\n'.join(parts).strip()
        return text or None
    except Exception as exc:
        logger.warning(f'PDF text extraction failed: {exc}')
        return None


def _extract_html_text(html: str) -> Optional[str]:
    """Extract readable text from HTML via stdlib html.parser."""
    from html.parser import HTMLParser

    class _Extractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._chunks: List[str] = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style', 'nav', 'header', 'footer', 'aside'):
                self._skip += 1

        def handle_endtag(self, tag):
            if tag in ('script', 'style', 'nav', 'header', 'footer', 'aside'):
                self._skip = max(0, self._skip - 1)

        def handle_data(self, data):
            if not self._skip:
                stripped = data.strip()
                if stripped:
                    self._chunks.append(stripped)

    parser = _Extractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = ' '.join(parser._chunks).strip()
    return text or None


# Same bar as the ScienceDirect tier and diagnose_connection: shorter text is
# usually a landing page (abstract plus metadata), not the article.
OA_MIN_CHARS = 5000
MAX_ATTEMPTS = 6
FETCH_TIMEOUT = 30.0
# Open-access links are sometimes attached to the wrong record: live, the
# repository copy linked to He et al. (2016), "Deep Residual Learning for
# Image Recognition", was a 2022 PhD thesis on lightning that cites it. A
# text counts only if the paper's title appears in its opening.
TITLE_WINDOW = 6000

_VERSIONS = {
    'publishedVersion': 'published', 'acceptedVersion': 'accepted manuscript',
    'submittedVersion': 'preprint',
}
# Unknown versions (e.g. a publisher's open PDF) rank before preprints.
_VERSION_RANK = {'published': 0, 'accepted manuscript': 1, None: 2, 'preprint': 3}


def _candidate(url, source, version=None, kind='pdf'):
    return {'url': url, 'source': source, 'version': version, 'kind': kind}


def _kind(url: str) -> str:
    return 'pdf' if url.lower().split('?')[0].endswith('.pdf') else 'html'


async def _openalex(http, doi, email):
    """Open locations and the title (for the arXiv step)."""
    params = {'select': 'title,locations'}
    if email:
        params['mailto'] = email
    r = await http.get(f'https://api.openalex.org/works/doi:{doi}', params=params)
    if r.status_code != 200:
        return [], None
    data = r.json()
    found = []
    for loc in data.get('locations') or []:
        if not loc.get('is_oa'):
            continue
        source = (loc.get('source') or {}).get('display_name') or 'repository'
        version = _VERSIONS.get(loc.get('version'))
        if loc.get('pdf_url'):
            found.append(_candidate(loc['pdf_url'], f'OpenAlex: {source}', version, 'pdf'))
        elif loc.get('landing_page_url'):
            found.append(_candidate(loc['landing_page_url'], f'OpenAlex: {source}', version, 'html'))
    return found, data.get('title')


async def _semantic_scholar(http, doi):
    """Open PDF link and arXiv ID. Unauthenticated calls share a rate limit,
    so a 429 simply means no candidates from this source."""
    headers = {}
    key, _ = resolve_semantic_scholar_key()
    if key:
        headers['x-api-key'] = key
    r = await http.get(f'https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}',
                       params={'fields': 'title,openAccessPdf,externalIds'}, headers=headers)
    if r.status_code != 200:
        return [], None, None
    data = r.json()
    found = []
    pdf = ((data.get('openAccessPdf') or {}).get('url') or '').strip()
    if pdf:
        found.append(_candidate(pdf, 'Semantic Scholar', None, _kind(pdf)))
    arxiv_id = (data.get('externalIds') or {}).get('ArXiv')
    if arxiv_id:
        found.append(_candidate(f'https://arxiv.org/pdf/{arxiv_id}', 'arXiv', 'preprint', 'pdf'))
    return found, data.get('title'), arxiv_id


async def _arxiv_by_title(http, title):
    """arXiv preprint whose title matches exactly (after normalisation)."""
    clean = re.sub(r'["\\]', ' ', title)
    r = await http.get('https://export.arxiv.org/api/query',
                       params={'search_query': f'ti:"{clean}"', 'max_results': 5})
    if r.status_code != 200:
        return []
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    wanted = normalize_title(title)
    for entry in ET.fromstring(r.text).findall('a:entry', ns):
        if normalize_title(entry.findtext('a:title', '', ns)) == wanted:
            abs_url = entry.findtext('a:id', '', ns).strip()
            pdf_url = re.sub(r'^http://', 'https://', abs_url.replace('/abs/', '/pdf/'))
            return [_candidate(pdf_url, 'arXiv', 'preprint', 'pdf')]
    return []


async def _europe_pmc(http, doi):
    r = await http.get('https://www.ebi.ac.uk/europepmc/webservices/rest/search',
                       params={'query': f'DOI:"{doi}"', 'format': 'json', 'resultType': 'core'})
    if r.status_code != 200:
        return []
    for hit in ((r.json().get('resultList') or {}).get('result') or []):
        pmcid = hit.get('pmcid')
        if pmcid and hit.get('isOpenAccess') == 'Y' and hit.get('inEPMC') == 'Y':
            return [_candidate(f'https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML',
                               'Europe PMC', 'published', 'jats')]
    return []


async def _unpaywall(http, doi, email):
    r = await http.get(f'https://api.unpaywall.org/v2/{doi}', params={'email': email})
    if r.status_code != 200:
        return []
    found = []
    for loc in r.json().get('oa_locations') or []:
        url = loc.get('url_for_pdf') or loc.get('url')
        if url:
            source = loc.get('repository_institution') or loc.get('host_type') or 'Unpaywall'
            found.append(_candidate(url, f'Unpaywall: {source}', _VERSIONS.get(loc.get('version')),
                                    'pdf' if loc.get('url_for_pdf') else _kind(url)))
    return found


async def _core(http, doi, key):
    r = await http.get('https://api.core.ac.uk/v3/search/works',
                       params={'q': f'doi:"{doi}"', 'limit': 3},
                       headers={'Authorization': f'Bearer {key}'})
    if r.status_code != 200:
        return []
    return [_candidate(w['downloadUrl'], 'CORE', None, _kind(w['downloadUrl']))
            for w in (r.json().get('results') or []) if w.get('downloadUrl')]


async def _safely(coro, label):
    """A source that errors or answers garbage contributes nothing."""
    try:
        return await coro
    except Exception as exc:
        logger.info(f'OA source {label} unavailable: {type(exc).__name__}: {exc}')
        return None


def _rank(candidates):
    """Published before accepted before preprint; PDF/XML before landing pages."""
    seen, ordered = set(), []
    for c in sorted(candidates, key=lambda c: (c['kind'] == 'html',
                                               _VERSION_RANK.get(c['version'], 2))):
        key = c['url'].rstrip('/').lower()
        if key not in seen:
            seen.add(key)
            ordered.append(c)
    return ordered


def _extract_jats_text(xml_text: str) -> Optional[str]:
    """Body text of a JATS article (Europe PMC fullTextXML)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    body = root.find('.//body')
    # Join element texts with spaces: adjacent paragraphs must not glue words.
    text = ' '.join(' '.join((body if body is not None else root).itertext()).split())
    return text or None


def _squash(text: str) -> str:
    """Letters and digits only, lower-cased: immune to line breaks,
    hyphenation and punctuation differences between metadata and PDF text."""
    return re.sub(r'[^0-9a-z]', '', (text or '').lower())


def title_in_opening(title: str, text: str) -> bool:
    """Whether the paper's title appears within the text's opening."""
    wanted = _squash(title)
    return bool(wanted) and wanted in _squash(text[:TITLE_WINDOW])


async def find_oa_candidates(doi: str, http: httpx.AsyncClient):
    """(ranked open-access candidates for a DOI, the paper's title)."""
    email = get_contact_email()
    core_key, _ = resolve_core_key()
    jobs = {
        'openalex': _openalex(http, doi, email),
        'semantic_scholar': _semantic_scholar(http, doi),
        'europe_pmc': _europe_pmc(http, doi),
    }
    if email:
        jobs['unpaywall'] = _unpaywall(http, doi, email)
    if core_key:
        jobs['core'] = _core(http, doi, core_key)
    results = dict(zip(jobs, await asyncio.gather(*(_safely(c, k) for k, c in jobs.items()))))

    candidates: List[Dict[str, Any]] = []
    oa = results.get('openalex') or ([], None)
    s2 = results.get('semantic_scholar') or ([], None, None)
    candidates += oa[0] + s2[0]
    for key in ('europe_pmc', 'unpaywall', 'core'):
        candidates += results.get(key) or []
    title = oa[1] or s2[1]
    if title and not s2[2]:  # no arXiv ID from Semantic Scholar: try arXiv itself
        candidates += (await _safely(_arxiv_by_title(http, title), 'arxiv')) or []
    return _rank(candidates), title


async def fetch_oa_fulltext(doi: str) -> Dict[str, Any]:
    """Find and extract open-access full text for *doi*.

    Returns text (None if nothing qualified), source_url (the successful
    URL, else the first one tried), source and version of the text,
    title_verified (whether the paper's title was found in the text's
    opening; False only when no title was known), and attempts: every
    candidate tried and its outcome. Never raises.
    """
    headers = {'User-Agent': contact_user_agent(USER_AGENT), 'Accept': '*/*'}
    attempts: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True,
                                     headers=headers) as http:
            candidates, title = await find_oa_candidates(doi, http)
            for cand in candidates[:MAX_ATTEMPTS]:
                outcome = await _safely(_fetch_one(http, cand), cand['url'])
                text, note = outcome if outcome else (None, 'error')
                # Europe PMC's XML is matched by DOI and its body excludes the
                # title page, so it is exempt from the title check.
                if text and title and cand['kind'] != 'jats' and not title_in_opening(title, text):
                    text, note = None, 'different document: title not in its opening'
                attempts.append({'url': cand['url'], 'source': cand['source'],
                                 'version': cand['version'], 'outcome': note})
                if text:
                    return {'text': text, 'source_url': cand['url'], 'source': cand['source'],
                            'version': cand['version'], 'title_verified': bool(title),
                            'attempts': attempts}
    except Exception as exc:
        logger.warning(f'OA full-text search failed for doi={doi}: {exc}')
    return {'text': None, 'source_url': attempts[0]['url'] if attempts else None,
            'source': None, 'version': None, 'title_verified': False, 'attempts': attempts}


async def _fetch_one(http, cand):
    """(text, outcome) for one candidate; text only when it passes OA_MIN_CHARS."""
    r = await http.get(cand['url'])
    if r.status_code != 200:
        return None, f'HTTP {r.status_code}'
    ctype = r.headers.get('content-type', '').lower()
    if cand['kind'] == 'jats':
        text = _extract_jats_text(r.text)
    elif 'pdf' in ctype or cand['kind'] == 'pdf' and r.content[:4] == b'%PDF':
        text = _extract_pdf_text(r.content)
    else:
        text = _extract_html_text(r.text)
    n = len(text or '')
    if n < OA_MIN_CHARS:
        return None, f'too short ({n} chars): landing page or abstract'
    return text, f'ok ({n} chars)'
