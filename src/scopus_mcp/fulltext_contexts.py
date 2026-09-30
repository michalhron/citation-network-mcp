"""Citation contexts from a citing paper's full text, for edges Semantic
Scholar cannot describe (no contexts, or no record of the citation).

Steps: split the text into body and reference list; find the cited work's
entry (first-author surname, year, title words); then return the body
sentences that carry a marker for it: author-year ("Swanson and Ramiller
2004", "(Swanson & Ramiller, 2004; ...)", "Ramiller et al. (2008)") or the
entry's number ("[36]").
"""
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .openalex import normalize_title

_REF_HEADING = re.compile(
    r'\n\s*(?:\d+\.?\s*)?(References|REFERENCES|Bibliography|BIBLIOGRAPHY|Literature Cited|'
    r'Works Cited|Reference List)\s*\n')
_SENTENCE_END = re.compile(r'(?<=[.!?])["”’)]?\s+(?=[A-Z(“"])')
_ENTRY_NUMBER = re.compile(r'^\s*\[?(\d{1,3})[\].)]\s')
_YEAR = re.compile(r'\b(?:19|20)\d{2}[a-z]?\b')
MAX_SENTENCE = 600


def split_references(text: str) -> Tuple[str, str]:
    """(body, reference list); the list starts at the last References
    heading. No heading: the whole text is body."""
    matches = list(_REF_HEADING.finditer(text or ''))
    if not matches:
        return text or '', ''
    cut = matches[-1].start()
    return text[:cut], text[cut:]


def _entries(refs_text: str) -> List[str]:
    """Reference entries: numbered entries, else one per paragraph/line
    group starting with a capitalised surname."""
    lines = [ln.strip() for ln in refs_text.splitlines() if ln.strip()]
    entries: List[str] = []
    for ln in lines[1:]:  # skip the heading
        starts_new = bool(_ENTRY_NUMBER.match(ln)) or bool(re.match(r"^[A-Z][A-Za-z'’\-]+,\s", ln))
        if starts_new or not entries:
            entries.append(ln)
        else:
            entries[-1] += ' ' + ln
    return entries


def find_reference_entry(refs_text: str, surnames: Sequence[str], year, title: Optional[str]
                         ) -> Tuple[Optional[str], Optional[str]]:
    """(entry text, entry number or None) for the cited work."""
    if not surnames:
        return None, None
    first = surnames[0].lower()
    year = str(year or '')[:4]
    words = [w for w in normalize_title(title).split() if len(w) > 3][:5]
    best, best_score = None, 0
    for entry in _entries(refs_text):
        low = entry.lower()
        if first not in low or (year and year not in entry):
            continue
        score = 1 + sum(w in normalize_title(entry) for w in words) + \
            sum(s.lower() in low for s in surnames[1:3])
        if score > best_score:
            best, best_score = entry, score
    if best is None:
        return None, None
    m = _ENTRY_NUMBER.match(best)
    return best, (m.group(1) if m else None)


_HEADING_LINE = re.compile(r'^\s*(?:\d+(?:\.\d+)*\.?\s+)?[A-Z][^.!?]{0,48}$')


def _drop_headings(body: str) -> str:
    """Remove short title-like lines between a sentence end (or blank line)
    and a capitalised line: section headings, which would otherwise glue
    onto the next sentence. A wrapped line mid-sentence never qualifies."""
    lines = body.splitlines()
    out, prev = [], ''
    for i, line in enumerate(lines):
        stripped = line.strip()
        nxt = next((ln.strip() for ln in lines[i + 1:] if ln.strip()), '')
        after_break = not prev.strip() or prev.rstrip().endswith(('.', '!', '?', ':'))
        if stripped and after_break and _HEADING_LINE.match(stripped) and nxt[:1].isupper():
            out.append('')
            prev = ''
            continue
        out.append(line)
        prev = line
    return '\n'.join(out)


def _sentences(body: str) -> List[Tuple[int, int, str]]:
    body = re.sub(r'-\n(?=[a-z])', '', body)       # re-join hyphenated line breaks
    body = _drop_headings(body)
    flat = re.sub(r'\s+', ' ', body)
    out, start = [], 0
    for m in _SENTENCE_END.finditer(flat):
        out.append((start, m.start(), flat[start:m.start()].strip()))
        start = m.end()
    out.append((start, len(flat), flat[start:].strip()))
    return out


def _marker_patterns(surnames: Sequence[str], year, number: Optional[str]) -> List[re.Pattern]:
    pats = []
    year = str(year or '')[:4]
    if surnames and year:
        first = re.escape(surnames[0])
        pats.append(re.compile(rf"\b{first}\b[^.!?]{{0,90}}?\b{year}[a-z]?\b"))
    if number:
        pats.append(re.compile(rf"\[(?:[\d\s,–-]*[,\s–-])?{number}(?:[,\s–-][\d\s,–-]*)?\]"))
    return pats


_INITIALS = re.compile(r'\b[A-Z]\.(?:\s?[A-Z]\.)*(?=\s|$)')
_BARE_PAGES = re.compile(r'\b\d{1,4}\s\d{1,4}\b')


def looks_like_reference(sentence: str) -> bool:
    """Bibliography text that leaked into the body (ScienceDirect's raw text
    appends the reference list without a heading): several initials
    ("E.B.", "N.C.") or bare page ranges ("267 287") next to a year."""
    if not _YEAR.search(sentence):
        return False
    return len(_INITIALS.findall(sentence)) >= 2 or bool(_BARE_PAGES.search(sentence))


def citing_sentences(body: str, surnames: Sequence[str], year, number: Optional[str] = None) -> List[str]:
    """Body sentences carrying a citation marker for the cited work."""
    pats = _marker_patterns(surnames, year, number)
    if not pats:
        return []
    found = []
    for _, _, sentence in _sentences(body):
        if looks_like_reference(sentence):
            continue
        if any(p.search(sentence) for p in pats):
            s = sentence if len(sentence) <= MAX_SENTENCE else sentence[:MAX_SENTENCE] + '…'
            if s not in found:
                found.append(s)
    return found


_CITE_PAREN = re.compile(r'\([^()]{0,400}?\b(?:19|20)\d{2}[a-z]?\b[^()]{0,400}?\)|\[[\d\s,–-]+\]')


def is_list_citation(context: str, surnames: Sequence[str], year, number: Optional[str] = None,
                     threshold: int = 3) -> bool:
    """True when the cited work is one of a list: a parenthesis (or
    bracket) with at least `threshold` works, or a sentence enumerating at
    least `threshold` separately cited works ("institutional factors (King
    1994), standards (Yoo 2005), fashions (Wang 2010), ...")."""
    first = surnames[0] if surnames else None
    year = str(year or '')[:4]
    for group in re.findall(r'\(([^()]{0,400})\)', context):
        if first and first in group and year in group and len(_YEAR.findall(group)) >= threshold:
            return True
    if number:
        for group in re.findall(r'\[([\d\s,–-]+)\]', context):
            if re.search(rf'\b{number}\b', group) and len(re.findall(r'\d+', group)) >= threshold:
                return True
    return len(_CITE_PAREN.findall(context)) >= threshold


def construct_near_marker(context: str, surnames: Sequence[str], year, terms: Sequence[str],
                          number: Optional[str] = None) -> bool:
    """True when a construct term sits in the cited work's own stretch of
    the sentence: between the neighbouring citations of other works, not
    beside them. "fashions (Wang, 2010), and organizing visions (Ramiller &
    Swanson, 2003)" names the construct for Ramiller & Swanson, not Wang."""
    low_terms = [t.lower() for t in terms if t]
    pats = _marker_patterns(surnames, year, number)
    if not low_terms:
        return False
    first = (surnames or [''])[0]
    marker = next((m for p in pats for m in [p.search(context)] if m), None)
    if marker is None and first:
        # Numeric styles give no year: "Swanson and Ramiller [36], who ..."
        marker = re.search(rf'\b{re.escape(first)}\b', context)
    if marker is None:
        # Semantic Scholar extracted this sentence for the citation, but no
        # marker is readable: count it unless it enumerates several works.
        return any(t in context.lower() for t in low_terms) and \
            len(_CITE_PAREN.findall(context)) < 3
    own = next((m for m in _CITE_PAREN.finditer(context)
                if m.start() <= marker.start() < m.end() or m.start() < marker.end() <= m.end()), None)
    start, end = 0, len(context)
    for m in _CITE_PAREN.finditer(context):
        if m is own or (own and m.span() == own.span()) or (first and first in m.group(0)):
            continue
        if m.group(0).startswith('[') and 0 <= m.start() - marker.end() <= 30:
            continue  # "Swanson and Ramiller [36]": the work's own number
        if m.end() <= marker.start():
            start = max(start, m.end())
        elif m.start() >= marker.end():
            end = min(end, m.start())
    if own is not None and first and first in own.group(0):
        # Parenthetical "... fashions (Wang, 2010)": the words before the
        # parenthesis are what it is cited for; what follows belongs to the
        # next citation.
        end = own.end()
    window = context[start:end].lower()
    return any(t in window for t in low_terms)


def contexts_from_text(text: str, cited: Dict) -> Dict:
    """{'contexts', 'reference_entry', 'reference_number', 'reason'} for
    the cited work ({'surnames', 'year', 'title'}) in a full text."""
    body, refs = split_references(text)
    entry, number = find_reference_entry(refs, cited.get('surnames') or [], cited.get('year'),
                                         cited.get('title'))
    contexts = citing_sentences(body, cited.get('surnames') or [], cited.get('year'), number)
    reason = None
    if not refs:
        reason = 'no reference list found in the text'
    elif entry is None:
        reason = 'cited work not found in the reference list'
    elif not contexts:
        reason = 'reference found, but no in-text marker located'
    return {'contexts': contexts, 'reference_entry': entry, 'reference_number': number,
            'reason': reason}
