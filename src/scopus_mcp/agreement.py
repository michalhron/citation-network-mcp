"""Inter-coder agreement for a filled-in coding sheet (Cohen 1960).

Reads the sheet path_transmission writes once two coders have filled their
columns, and reports Cohen's kappa with a 95% interval, agreement per
label, the confusion matrix and the disagreeing rows. It also scores the
draft labels against each coder and against the coders' consensus: the
validation the heuristic needs before its labels can be reported.
"""
import csv
import io
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Upper bounds, inclusive (Landis & Koch 1977): 0-0.20 slight, 0.21-0.40 fair, ...
LANDIS_KOCH = [(0.2, 'slight'), (0.4, 'fair'), (0.6, 'moderate'), (0.8, 'substantial'),
               (1.0, 'almost perfect')]


def norm_label(value: Optional[str]) -> str:
    return ' '.join(str(value or '').strip().lower().replace('_', ' ').replace('-', ' ').split())


def read_sheet(path: str) -> List[Dict[str, str]]:
    p = Path(path).expanduser()
    if p.suffix.lower() not in ('.csv', '.tsv', '.txt'):
        raise ValueError("Give the coding sheet as .csv (save it as CSV from Excel).")
    text = p.read_bytes().decode('utf-8-sig', errors='replace')
    # The delimiter comes from the header line alone: Excel writes ';' in
    # many European locales, and the data rows hold quoted citation text
    # full of commas and semicolons that would mislead a sniffer.
    header = text.splitlines()[0] if text else ''
    delimiter = max((',', ';', '\t'), key=header.count)
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter, quotechar='"', doublequote=True))


def interpret(kappa: Optional[float]) -> str:
    if kappa is None:
        return 'undefined'
    if kappa < 0:
        return 'worse than chance'
    return next((name for bound, name in LANDIS_KOCH if kappa <= bound), 'almost perfect')


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> Dict[str, Any]:
    """Kappa, observed and chance agreement, and an approximate 95% interval
    (standard error after Cohen 1960)."""
    n = len(a)
    if n == 0:
        return {'n': 0, 'kappa': None, 'observed': None, 'expected': None, 'ci95': None}
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if pe == 1:
        return {'n': n, 'kappa': None, 'observed': po, 'expected': pe, 'ci95': None}
    kappa = (po - pe) / (1 - pe)
    se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2)) if n else 0
    return {'n': n, 'kappa': round(kappa, 3), 'observed': round(po, 3), 'expected': round(pe, 3),
            'ci95': (round(kappa - 1.96 * se, 3), round(kappa + 1.96 * se, 3))}


def per_label(a: Sequence[str], b: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Specific (positive) agreement 2x/(n_a + n_b) and one-versus-rest kappa per label."""
    out = {}
    for label in sorted(set(a) | set(b)):
        both = sum(1 for x, y in zip(a, b) if x == label and y == label)
        na, nb = sum(1 for x in a if x == label), sum(1 for y in b if y == label)
        binary = cohen_kappa([x == label for x in a], [y == label for y in b])
        out[label] = {'coder_a': na, 'coder_b': nb, 'both': both,
                      'specific_agreement': round(2 * both / (na + nb), 3) if na + nb else None,
                      'kappa': binary['kappa']}
    return out


def confusion(a: Sequence[str], b: Sequence[str]) -> Tuple[List[str], List[List[int]]]:
    labels = sorted(set(a) | set(b))
    idx = {l: i for i, l in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    for x, y in zip(a, b):
        m[idx[x]][idx[y]] += 1
    return labels, m


def analyse(rows: List[Dict[str, str]], coder_a: str, coder_b: str,
            reference: Optional[str] = 'draft_label', id_column: str = 'edge') -> Dict[str, Any]:
    missing = [c for c in (coder_a, coder_b) if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"Column(s) not in the sheet: {', '.join(missing)}. "
                         f"Columns: {', '.join(rows[0]) if rows else 'none'}.")
    coded = [r for r in rows if norm_label(r.get(coder_a)) and norm_label(r.get(coder_b))]
    a = [norm_label(r[coder_a]) for r in coded]
    b = [norm_label(r[coder_b]) for r in coded]
    result = {'rows': len(rows), 'coded': len(coded), 'coder_a': coder_a, 'coder_b': coder_b,
              'agreement': cohen_kappa(a, b), 'per_label': per_label(a, b)}
    result['confusion'] = confusion(a, b)
    result['disagreements'] = [{'id': r.get(id_column), 'a': x, 'b': y,
                                'citing': r.get('citing_label'), 'cited': r.get('cited_label')}
                               for r, x, y in zip(coded, a, b) if x != y]
    if reference and coded and reference in coded[0]:
        ref = [norm_label(r.get(reference)) for r in coded]
        consensus = [(x, rf) for x, y, rf in zip(a, b, ref) if x == y]
        result['reference'] = {
            'column': reference,
            'vs_coder_a': cohen_kappa(ref, a),
            'vs_coder_b': cohen_kappa(ref, b),
            'vs_consensus': cohen_kappa([rf for _, rf in consensus], [x for x, _ in consensus]),
            'per_label_vs_consensus': per_label([rf for _, rf in consensus], [x for x, _ in consensus]),
        }
    return result
