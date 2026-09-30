"""Named journal baskets and ISSN scoping for searches and citation walks.

Journal names are unreliable filters in Scopus (the same journal appears as
"MIS Quarterly: Management Information Systems" and "MIS Quarterly
Management Information Systems"), so scoping always goes through ISSNs.
Print and electronic ISSNs are both listed: ISSN() matches either, and
OpenAlex source ISSNs include both.
"""
import re
from typing import Dict, List, Optional, Union

# AIS Senior Scholars' Basket of Eight.
BASKET_OF_EIGHT = {
    'European Journal of Information Systems': ['0960-085X', '1476-9344'],
    'Information Systems Journal': ['1350-1917', '1365-2575'],
    'Information Systems Research': ['1047-7047', '1526-5536'],
    'Journal of the Association for Information Systems': ['1536-9323'],
    'Journal of Information Technology': ['0268-3962', '1466-4437'],
    'Journal of Management Information Systems': ['0742-1222', '1557-928X'],
    'Journal of Strategic Information Systems': ['0963-8687', '1873-1198'],
    'MIS Quarterly': ['0276-7783', '2162-9730'],
}

BASKETS: Dict[str, Dict[str, List[str]]] = {
    'basket_of_eight': BASKET_OF_EIGHT,
}
ALIASES = {
    'ais8': 'basket_of_eight',
    'basket_of_8': 'basket_of_eight',
    'senior_scholars_basket': 'basket_of_eight',
    'ais_basket': 'basket_of_eight',
}

SCOPE_SCHEMA = {
    "description": (
        "Restrict to journals, by ISSN: a list of ISSNs, or a basket name "
        "('basket_of_eight' / 'ais8': the AIS Senior Scholars' Basket of "
        "Eight). ISSNs are used rather than journal names, which Scopus "
        "spells inconsistently."
    ),
    "anyOf": [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ],
}

_ISSN = re.compile(r'^(\d{4})-?(\d{3}[\dXx])$')


def normalize_issn(issn: str) -> str:
    m = _ISSN.match(str(issn).strip())
    if not m:
        raise ValueError(f"Not an ISSN: {issn!r} (expected NNNN-NNNN).")
    return f"{m.group(1)}-{m.group(2).upper()}"


def resolve_scope(scope: Union[None, str, List[str]]) -> Optional[List[str]]:
    """ISSN list for a scope argument; None when unscoped.

    A string is a basket name or a comma-separated ISSN list."""
    if scope is None or scope == '' or scope == []:
        return None
    if isinstance(scope, str):
        key = scope.strip().lower().replace(' ', '_').replace('-', '_')
        key = ALIASES.get(key, key)
        if key in BASKETS:
            return [i for issns in BASKETS[key].values() for i in issns]
        scope = [s for s in re.split(r'[,\s;]+', scope) if s]
    try:
        issns = [normalize_issn(s) for s in scope]
    except ValueError as exc:
        raise ValueError(
            f"{exc} Known baskets: {', '.join(sorted(BASKETS) + sorted(ALIASES))}."
        ) from None
    return list(dict.fromkeys(issns))


def scopus_issn_clause(issns: List[str]) -> str:
    """'(ISSN(0276-7783) OR ISSN(1047-7047) ...)' for a Scopus query."""
    return '(' + ' OR '.join(f'ISSN({i.replace("-", "")})' for i in issns) + ')'


def scope_scopus_query(query: str, issns: Optional[List[str]]) -> str:
    if not issns:
        return query
    return f'({query}) AND {scopus_issn_clause(issns)}'


def openalex_issn_filter(issns: List[str]) -> str:
    """OpenAlex filter fragment: primary_location.source.issn:a|b|..."""
    return 'primary_location.source.issn:' + '|'.join(issns)
