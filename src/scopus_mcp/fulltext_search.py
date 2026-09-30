"""
ScienceDirect full-text search: request building, result cleaning, and
mention analysis of retrieved full text.

Scopus searches titles, abstracts and keywords; ScienceDirect searches the
whole article body, so it finds papers that use a construct without naming
it up front. Coverage is Elsevier-published content only.

Full-text layout (ScienceDirect originalText, checked live 2026-09-30): a
front block (metadata, table of contents, and a compressed reference index
in run-together capitals), then the body, then back matter ending with the
full reference list. Mentions inside the reference list are usually cited
titles, not usage, so they are counted separately.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

PAGE_SIZE = 100          # API maximum per request
SNIPPETS = 3             # example sentences per article
SNIPPET_CHARS = 360

_BOOLEAN = {'AND', 'OR', 'NOT', 'W', 'PRE'}
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z"“(])')


def build_request(query: str, journal: Optional[str] = None,
                  from_year: Optional[int] = None, to_year: Optional[int] = None,
                  open_access_only: bool = False, offset: int = 0,
                  show: int = PAGE_SIZE, sort: str = 'relevance') -> Dict[str, Any]:
    """JSON body for PUT content/search/sciencedirect."""
    body: Dict[str, Any] = {'qs': query, 'display': {'offset': offset, 'show': show,
                                                     'sortBy': sort}}
    if journal:
        body['pub'] = journal
    if from_year or to_year:
        body['date'] = f"{from_year or 1823}-{to_year or 2100}"
    if open_access_only:
        body['filters'] = {'openAccess': True}
    return body


def clean_result(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten one search result to the shared record shape."""
    authors = entry.get('authors') or []
    names = [a.get('name') for a in authors if isinstance(a, dict) and a.get('name')]
    return {
        'title': entry.get('title'),
        'creator': names[0] if names else None,
        'authors': names,
        'publication_name': entry.get('sourceTitle'),
        'cover_date': entry.get('publicationDate'),
        'doi': entry.get('doi'),
        'pii': entry.get('pii'),
        'open_access': bool(entry.get('openAccess')),
        'url': entry.get('uri'),
        'source': 'sciencedirect',
    }


def query_terms(query: str) -> Tuple[List[str], bool]:
    """Terms to look for in retrieved text, and whether they are phrases.

    Quoted phrases are matched as phrases (any of them). Without quotes, a
    sentence must contain all remaining words of three or more letters.
    """
    phrases = [p.strip() for p in re.findall(r'"([^"]+)"', query) if p.strip()]
    if phrases:
        return phrases, True
    words = [w for w in re.findall(r"[\w'-]+", query.replace('*', ''))
             if len(w) >= 3 and w.upper() not in _BOOLEAN]
    return words, False


def _pattern(term: str) -> re.Pattern:
    return re.compile(r'\s+'.join(re.escape(part) for part in term.split()), re.IGNORECASE)


def split_body(text: str) -> Tuple[str, str]:
    """(body, reference list). The reference list starts at the last
    'References' heading in the second half of the text; without one, the
    whole text counts as body."""
    cut = None
    for m in re.finditer(r'\bReferences\b', text):
        if m.start() >= len(text) * 0.5:
            cut = m.start()
    if cut is None:
        return text, ''
    return text[:cut], text[cut:]


def _snippet(sentence: str, match_at: int) -> str:
    sentence = ' '.join(sentence.split())
    if len(sentence) <= SNIPPET_CHARS:
        return sentence
    start = max(0, min(match_at - SNIPPET_CHARS // 2, len(sentence) - SNIPPET_CHARS))
    return ('…' if start else '') + sentence[start:start + SNIPPET_CHARS] + '…'


def analyze_mentions(text: str, query: str) -> Dict[str, Any]:
    """Mention count in the body and in the reference list, body positions
    (percent through the body) and up to SNIPPETS example sentences spread
    across the body."""
    terms, as_phrases = query_terms(query)
    body, references = split_body(text or '')
    if not terms or not body:
        return {'body_mentions': 0, 'reference_list_mentions': 0,
                'positions_pct': [], 'snippets': []}

    patterns = [_pattern(t) for t in terms]
    hits: List[Tuple[int, str, int]] = []  # (offset in body, sentence, offset in sentence)
    offset = 0
    for sentence in _SENTENCE_END.split(body):
        start = body.find(sentence, offset)
        offset = start + len(sentence) if start >= 0 else offset
        if as_phrases:
            for pat in patterns:
                for m in pat.finditer(sentence):
                    hits.append((start + m.start(), sentence, m.start()))
        elif all(p.search(sentence) for p in patterns):
            first = min(p.search(sentence).start() for p in patterns)
            hits.append((start + first, sentence, first))
    hits.sort(key=lambda h: h[0])

    if as_phrases:
        ref_mentions = sum(len(p.findall(references)) for p in patterns)
    else:
        ref_mentions = sum(1 for s in _SENTENCE_END.split(references)
                           if all(p.search(s) for p in patterns))

    # Example sentences spread across the article: first, middle, last.
    picks: List[Tuple[int, str, int]] = []
    if hits:
        for i in sorted({0, len(hits) // 2, len(hits) - 1}):
            if all(hits[i][1] is not p[1] for p in picks):
                picks.append(hits[i])
    return {
        'body_mentions': len(hits),
        'reference_list_mentions': ref_mentions,
        'positions_pct': [round(100 * h[0] / len(body)) for h in hits],
        'snippets': [{'position_pct': round(100 * at / len(body)), 'text': _snippet(s, m)}
                     for at, s, m in picks[:SNIPPETS]],
    }
