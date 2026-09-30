"""Shared tool helpers: source switch, OpenAlex resolution, CSV output."""
from datetime import datetime as _today_datetime

from ..openalex import openalex_work_key
from ..output import _output_dir
from ..records import clean_abstract_details, to_scopus_id


def server_module():
    """The running server module. Tools read client, openalex and the
    patchable helpers from it at call time, so one module owns them."""
    from .. import server
    return server


SOURCE_SCHEMA = {
    "type": "string",
    "enum": ["scopus", "openalex"],
    "default": "scopus",
    "description": (
        "Data source. 'scopus' (default) needs subscriber entitlement for "
        "search, citations and references. 'openalex' needs none: IDs may be "
        "DOIs, OpenAlex work IDs (W...), or Scopus IDs (resolved to a DOI via "
        "Scopus metadata), and results carry OpenAlex IDs. Never mix sources "
        "within one analysis."
    ),
}


def _source(arguments: dict) -> str:
    source = (arguments.get("source") or "scopus").lower()
    if source not in ("scopus", "openalex"):
        raise ValueError(f"source must be 'scopus' or 'openalex', not {source!r}")
    return source


async def _resolve_openalex_work(identifier: str) -> dict:
    """Raw OpenAlex work for a DOI, OpenAlex ID, or Scopus ID/EID.

    Scopus IDs go through Scopus metadata to get a DOI; that lookup needs no
    subscriber entitlement, so it keeps working off campus. Records without
    a DOI (common for AIS conference papers) fall back to an exact title
    match within one year.
    """
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    identifier = str(identifier).strip()
    if openalex_work_key(identifier) is None:
        details = clean_abstract_details(await client.get_abstract(to_scopus_id(identifier)))
        doi = details.get('doi')
        if not doi:
            title = details.get('title')
            year = (details.get('cover_date') or '')[:4]
            work = await openalex.find_by_title(title, int(year) if year.isdigit() else None)
            if not work:
                raise ValueError(
                    f"Scopus record {identifier} has no DOI and no exact title match "
                    f"in OpenAlex ({title!r}); pass a DOI or OpenAlex work ID instead."
                )
            return work
        identifier = doi
    work = await openalex.get_work(identifier)
    if not work:
        raise ValueError(f"OpenAlex has no work for {identifier!r}.")
    return work


def _flatten_row(row: dict) -> dict:
    """CSV-friendly: {'year', 'value'} metrics become value + _year columns,
    lists become '; '-joined strings."""
    flat = {}
    for k, v in row.items():
        if isinstance(v, dict) and set(v) == {'year', 'value'}:
            flat[k] = v['value']
            flat[f'{k}_year'] = v['year']
        elif isinstance(v, list):
            flat[k] = '; '.join(str(x) for x in v)
        else:
            flat[k] = v
    return flat


def _write_rows_csv(rows: list, prefix: str):
    import csv
    flat = [_flatten_row(r) for r in rows]
    columns = []
    for r in flat:
        columns.extend(k for k in r if k not in columns)
    ts = _today_datetime.now().strftime('%Y%m%dT%H%M%S')
    path = _output_dir() / f'{prefix}-{len(rows)}-{ts}.csv'
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(flat)
    return path
