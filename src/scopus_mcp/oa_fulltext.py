"""Open-access full text: find a copy via OpenAlex and extract its text."""
import logging
from typing import Any, Dict, List, Optional

import httpx

from . import USER_AGENT

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


async def fetch_oa_fulltext(doi: str) -> Dict[str, Any]:
    """Query OpenAlex for an OA copy of *doi*, fetch it, and extract text.

    Returns a dict with keys:
      text       – extracted plain text, or None if unavailable
      source_url – the OA URL attempted (or None if no OA copy found)

    Never raises; failures are logged and surfaced as text=None.
    """
    POLITE_HEADERS = {
        'User-Agent': f'{USER_AGENT} (mailto:hron@hey.com)',
        'Accept': '*/*',
    }

    oa_url: Optional[str] = None

    # 1. OpenAlex lookup
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as http:
            r = await http.get(
                f'https://api.openalex.org/works/doi:{doi}',
                params={'mailto': 'hron@hey.com'},
                headers=POLITE_HEADERS,
            )
        if r.status_code == 200:
            data = r.json()
            oa = data.get('open_access') or {}
            if oa.get('is_oa'):
                oa_url = (
                    oa.get('oa_url')
                    or (data.get('best_oa_location') or {}).get('pdf_url')
                    or (data.get('best_oa_location') or {}).get('url')
                )
    except Exception as exc:
        logger.warning(f'OpenAlex lookup failed for doi={doi}: {exc}')

    if not oa_url:
        return {'text': None, 'source_url': None}

    # 2. Fetch OA URL and extract text
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
            r = await http.get(oa_url, headers=POLITE_HEADERS)
        r.raise_for_status()
        content_type = r.headers.get('content-type', '').lower()
        if 'pdf' in content_type or oa_url.lower().endswith('.pdf'):
            text = _extract_pdf_text(r.content)
        else:
            text = _extract_html_text(r.text)
        return {'text': text, 'source_url': oa_url}
    except Exception as exc:
        logger.warning(f'OA fetch/extract failed for doi={doi} url={oa_url}: {exc}')
        return {'text': None, 'source_url': oa_url}
