"""Parsers for saved bibliographic exports, so a corpus can be fixed from a
search run once and reanalysed later without depending on the API on the
day: Scopus CSV, RIS and BibTeX exports, and Web of Science plain-text and
tab-delimited exports. Records from several files are deduplicated."""
import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .openalex import normalize_title

CORPUS_KIND = 'scopus-plus-mcp corpus'
ALLOWED_SUFFIXES = {'.csv', '.txt', '.ris', '.bib', '.tsv', '.tab'}
MAX_BYTES = 50 * 1024 * 1024
_EID = re.compile(r'2-s2\.0-(\d+)')


def _record(**kw) -> Dict[str, Any]:
    rec = {'scopus_id': None, 'wos_id': None, 'doi': None, 'title': None, 'year': None,
           'authors': [], 'source': None, 'author_keywords': [], 'index_keywords': [],
           'abstract': None, 'format': None}
    rec.update({k: v for k, v in kw.items() if v not in (None, '', [])})
    if rec['doi']:
        rec['doi'] = re.sub(r'^https?://(dx\.)?doi\.org/', '', rec['doi'].strip(), flags=re.I).lower()
    if rec['year']:
        m = re.search(r'(19|20)\d{2}', str(rec['year']))
        rec['year'] = m.group(0) if m else None
    return rec


def _split_kw(value: Optional[str]) -> List[str]:
    return [k.strip() for k in re.split(r'[;]', value or '') if k.strip()]


def _eid(*values) -> Optional[str]:
    for v in values:
        m = _EID.search(str(v or ''))
        if m:
            return m.group(1)
    return None


def parse_scopus_csv(text: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text.lstrip('﻿')))
    out = []
    for row in reader:
        row = {(k or '').strip().lstrip('﻿'): v for k, v in row.items()}
        out.append(_record(
            scopus_id=_eid(row.get('EID'), row.get('Link')), doi=row.get('DOI'),
            title=row.get('Title'), year=row.get('Year'),
            authors=[a.strip() for a in re.split(r';|,(?= [A-Z])', row.get('Authors') or '') if a.strip()],
            source=row.get('Source title'), author_keywords=_split_kw(row.get('Author Keywords')),
            index_keywords=_split_kw(row.get('Index Keywords')), abstract=row.get('Abstract'),
            format='scopus_csv'))
    return out


def parse_ris(text: str) -> List[Dict[str, Any]]:
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r'^([A-Z][A-Z0-9])  - ?(.*)$', line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == 'TY':
            cur = {'AU': [], 'KW': []}
        elif cur is None:
            continue
        elif tag == 'ER':
            out.append(_record(
                scopus_id=_eid(cur.get('UR'), cur.get('M3'), cur.get('AN')), doi=cur.get('DO'),
                title=cur.get('TI') or cur.get('T1'), year=cur.get('PY') or cur.get('Y1'),
                authors=cur['AU'], source=cur.get('T2') or cur.get('JO') or cur.get('JF'),
                author_keywords=cur['KW'], abstract=cur.get('AB') or cur.get('N2'), format='ris'))
            cur = None
        elif tag in ('AU', 'A1'):
            cur['AU'].append(val)
        elif tag == 'KW':
            cur['KW'].append(val)
        else:
            cur.setdefault(tag, val)
    return out


def parse_bibtex(text: str) -> List[Dict[str, Any]]:
    out = []
    for entry in re.split(r'\n(?=@\w+\s*\{)', text):
        if not entry.strip().startswith('@'):
            continue
        fields = {}
        for m in re.finditer(r'(\w[\w-]*)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|"[^"]*"|\d+)', entry):
            fields[m.group(1).lower()] = re.sub(r'[{}"]', '', m.group(2)).strip()
        if not fields:
            continue
        out.append(_record(
            scopus_id=_eid(fields.get('url'), fields.get('note'), fields.get('eid')), doi=fields.get('doi'),
            title=fields.get('title'), year=fields.get('year'),
            authors=[a.strip() for a in re.split(r'\s+and\s+', fields.get('author', '')) if a.strip()],
            source=fields.get('journal') or fields.get('booktitle'),
            author_keywords=_split_kw(fields.get('author_keywords') or fields.get('keywords')),
            abstract=fields.get('abstract'), format='bibtex'))
    return out


def parse_wos_plain(text: str) -> List[Dict[str, Any]]:
    out, cur, tag = [], None, None
    for line in text.splitlines():
        if line.startswith('ER'):
            if cur:
                out.append(_wos_record(cur, 'wos_plain'))
            cur, tag = None, None
            continue
        if re.match(r'^[A-Z][A-Z0-9] ', line):
            tag, val = line[:2], line[3:].strip()
            if tag == 'PT':
                cur = {}
            if cur is not None:
                cur.setdefault(tag, []).append(val)
        elif line.startswith('   ') and cur is not None and tag:
            cur.setdefault(tag, []).append(line.strip())
    return out


def parse_wos_tab(text: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text.lstrip('﻿')), delimiter='\t', quoting=csv.QUOTE_NONE)
    out = []
    for row in reader:
        row = {(k or '').strip().lstrip('﻿'): v for k, v in row.items()}
        cur = {k: [v] for k, v in row.items() if v}
        cur['AU'] = [a.strip() for a in (row.get('AU') or '').split(';') if a.strip()]
        out.append(_wos_record(cur, 'wos_tab'))
    return out


def _wos_record(cur: Dict[str, List[str]], fmt: str) -> Dict[str, Any]:
    def one(tag, sep=' '):
        return sep.join(cur.get(tag) or []) or None
    return _record(
        wos_id=(one('UT') or '').replace('WOS:', '') or None, doi=one('DI'), title=one('TI'),
        year=one('PY'), authors=cur.get('AU') or [], source=one('SO'),
        author_keywords=_split_kw(one('DE', ' ')), index_keywords=_split_kw(one('ID', ' ')),
        abstract=one('AB'), format=fmt)


def sniff(text: str, suffix: str) -> str:
    head = text.lstrip('﻿')[:4000]
    if suffix == '.bib' or re.match(r'\s*@\w+\s*\{', head):
        return 'bibtex'
    if re.search(r'^TY  - ', head, re.M):
        return 'ris'
    if re.match(r'(FN |PT )', head) and re.search(r'^ER\s*$', text, re.M):
        return 'wos_plain'
    first = head.splitlines()[0] if head else ''
    if first.startswith('PT\t') or '\tUT' in first:
        return 'wos_tab'
    if 'EID' in first or ('Title' in first and 'Source title' in first):
        return 'scopus_csv'
    raise ValueError("Unrecognised export: expected Scopus CSV/RIS/BibTeX or Web of Science "
                     "plain text or tab-delimited.")


PARSERS = {'scopus_csv': parse_scopus_csv, 'ris': parse_ris, 'bibtex': parse_bibtex,
           'wos_plain': parse_wos_plain, 'wos_tab': parse_wos_tab}


def read_export(path: str) -> List[Dict[str, Any]]:
    p = Path(path).expanduser()
    if p.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"{p.name}: only {', '.join(sorted(ALLOWED_SUFFIXES))} export files are read.")
    if not p.is_file():
        raise ValueError(f"No such file: {p}")
    if p.stat().st_size > MAX_BYTES:
        raise ValueError(f"{p.name} is larger than {MAX_BYTES // 2**20} MB.")
    raw = p.read_bytes()
    for enc in ('utf-8-sig', 'utf-16', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    fmt = sniff(text, p.suffix.lower())
    recs = PARSERS[fmt](text)
    for r in recs:
        r['file'] = p.name
    return recs


def _key(r):
    t = normalize_title(r.get('title'))
    return f"t:{t}:{r.get('year')}" if t else None


def deduplicate(records: List[Dict[str, Any]]):
    """(unique records, number of duplicates) by Scopus ID, WoS ID, DOI, or
    normalised title and year; merged records keep every identifier and the
    longer keyword and abstract fields."""
    unique: List[Dict[str, Any]] = []
    index: Dict[str, int] = {}
    dups = 0
    for r in records:
        keys = [k for k in (r.get('scopus_id') and f"s:{r['scopus_id']}", r.get('wos_id') and f"w:{r['wos_id']}",
                            r.get('doi') and f"d:{r['doi']}", _key(r)) if k]
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            unique.append(dict(r))
            hit = len(unique) - 1
        else:
            dups += 1
            base = unique[hit]
            for field, value in r.items():
                if field in ('author_keywords', 'index_keywords', 'authors'):
                    if len(value or []) > len(base.get(field) or []):
                        base[field] = value
                elif not base.get(field) and value:
                    base[field] = value
        for k in keys:
            index.setdefault(k, hit)
        m = unique[hit]
        for k in (m.get('scopus_id') and f"s:{m['scopus_id']}", m.get('doi') and f"d:{m['doi']}"):
            if k:
                index.setdefault(k, hit)
    return unique, dups


def load_corpus_file(path: str) -> dict:
    p = Path(path).expanduser()
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f"Cannot read corpus file {p}: {exc}") from None
    if data.get('kind') != CORPUS_KIND:
        raise ValueError(f"{p.name} is not a corpus file written by import_records.")
    return data


def apply_corpus_file(arguments: dict, source: str) -> dict:
    """Arguments with ids taken from arguments['corpus_file'], if given:
    Scopus IDs for Scopus, DOIs for OpenAlex."""
    path = arguments.get('corpus_file')
    if not path:
        return arguments
    if arguments.get('ids') or arguments.get('query'):
        raise ValueError("Give corpus_file, ids or query, not several.")
    data = load_corpus_file(path)
    ids = data.get('dois') if source == 'openalex' else data.get('scopus_ids')
    if not ids:
        raise ValueError(f"The corpus file has no {'DOIs' if source == 'openalex' else 'Scopus IDs'}.")
    # corpus_file is consumed, so applying the arguments twice is harmless.
    return {**{k: v for k, v in arguments.items() if k != 'corpus_file'}, 'ids': ids}
