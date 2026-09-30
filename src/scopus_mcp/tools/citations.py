"""Backward and forward citations of one document."""
import mcp.types as types

from ..completeness import RULE_TEXT, assess, external_reference_counts
from ..openalex import bare_doi, clean_openalex_work, short_id
from ..records import (
    clean_abstract_details,
    clean_references,
    clean_search_results,
    reported_reference_total,
    to_scopus_id,
)
from .common import SOURCE_SCHEMA, _resolve_openalex_work, _source, server_module


TOOLS = [
    types.Tool(
        name="get_references",
        description=(
            "Retrieve the cited-reference list of a document (Backward Citations) "
            "via the Abstract Retrieval REF view. Complements get_citing_papers, "
            "which returns forward citations. Scopus requires an entitled "
            "(subscriber) key; source='openalex' does not."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scopus_id": {
                    "type": "string",
                    "description": (
                        "The Scopus ID (or EID) of the document whose references to "
                        "retrieve. With source='openalex', a DOI or OpenAlex work ID also works."
                    )
                },
                "source": SOURCE_SCHEMA,
                "count": {
                    "type": "integer",
                    "description": (
                        "Maximum number of references to return (default 25). The "
                        "reply always states how many the document has, and whether "
                        "the list was cut."
                    ),
                    "default": 25
                },
                "filter_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Return only references whose Scopus ID, EID, DOI (or, with "
                        "source='openalex', OpenAlex ID) is in this list: the "
                        "within-set edges of a corpus without the full reference "
                        "records. count does not apply."
                    )
                },
                "check_completeness": {
                    "type": "boolean",
                    "description": (
                        "Compare the retrieved list with the reference count the "
                        "publisher deposited at Crossref, and flag lists that look "
                        "short (default false; one Crossref request)."
                    ),
                    "default": False
                }
            },
            "required": ["scopus_id"]
        }
    ),
    types.Tool(
        name="get_citing_papers",
        description="Retrieve a list of papers that have cited the specified document (Forward Citations).",
        inputSchema={
            "type": "object",
            "properties": {
                "scopus_id": {
                    "type": "string",
                    "description": (
                        "The Scopus ID of the document to find citations for. With "
                        "source='openalex', a DOI or OpenAlex work ID also works."
                    )
                },
                "source": SOURCE_SCHEMA,
                "count": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 25).",
                    "default": 5,
                    "maximum": 25
                },
                "sort": {
                    "type": "string",
                    "description": "Sort order (e.g., 'coverDate', 'relevancy').",
                    "default": "coverDate"
                }
            },
            "required": ["scopus_id"]
        }
    ),
]


def _id_matcher(filter_ids):
    """Normalized ID set for filter_ids: Scopus IDs, EIDs, DOIs, OpenAlex IDs."""
    wanted = set()
    for raw in filter_ids or []:
        v = str(raw).strip()
        if not v:
            continue
        low = v.lower()
        for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
            if low.startswith(prefix):
                low = low[len(prefix):]
        if low.startswith('10.'):
            wanted.add(low)
            continue
        oa = short_id(v) if 'openalex.org' in low else v
        if oa and oa[:1] in 'Ww' and oa[1:].isdigit():
            wanted.add(oa.upper())
            continue
        wanted.add(to_scopus_id(v))
    return wanted


def ref_matches(ref: dict, wanted: set) -> bool:
    for key in ('scopus_id', 'openalex_id'):
        if ref.get(key) and str(ref[key]).upper() in wanted:
            return True
    return bool(ref.get('doi')) and ref['doi'].lower() in wanted


def _reference_header(shown, available, reported, count, filtered, n_filter):
    if filtered:
        return (f"{shown} of the document's {available} references match "
                f"filter_ids ({n_filter} IDs).")
    truncated = shown < available
    head = (f"Returned {shown} of {available} references "
            f"(truncated: {str(truncated).lower()}")
    head += f"; raise count above {count} for the rest)." if truncated else ")."
    if reported is not None and reported > available:
        head += (f" Scopus reports {reported} references but serves "
                 f"{available}; the difference cannot be retrieved.")
    return head


async def _completeness_line(doi, retrieved):
    if not doi:
        return "Completeness: unknown (the document has no DOI to look up a comparison count)."
    counts = await external_reference_counts([doi], openalex=server_module().openalex)
    ext, src = counts.get(doi.lower(), (None, None))
    verdict = assess(retrieved, ext)
    if verdict == 'unknown':
        return ("Completeness: unknown (no reference count at Crossref, OpenAlex or "
                "Semantic Scholar).")
    if verdict == 'short':
        return (f"Completeness: SHORT. {src} lists {ext} references; this list has "
                f"{retrieved} ({RULE_TEXT}). Edges from this paper will be missing in "
                "a citation network; cross-check with source='openalex'.")
    return f"Completeness: ok ({src} lists {ext} references)."


async def _get_references(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    scopus_id = arguments.get("scopus_id")
    count = int(arguments.get("count", 25))
    filter_ids = arguments.get("filter_ids") or []
    wanted = _id_matcher(filter_ids)
    check = bool(arguments.get("check_completeness"))
    if not scopus_id:
        raise ValueError("scopus_id is required")

    if _source(arguments) == 'openalex':
        work = await _resolve_openalex_work(scopus_id)
        ref_ids = [short_id(r) for r in (work.get('referenced_works') or [])]
        available = len(ref_ids)
        if wanted:
            # Matching OpenAlex IDs needs no hydration; DOIs do.
            by_id = [w for w in ref_ids if w.upper() in wanted]
            if any(w.startswith('10.') for w in wanted):
                refs = [clean_openalex_work(w) for w in await openalex.references(work)]
                references = [r for r in refs if ref_matches(r, wanted)]
            else:
                references = [clean_openalex_work(w)
                              for w in await openalex.works_by_ids(by_id)]
        else:
            refs = await openalex.references(work, limit=count)
            references = [clean_openalex_work(w) for w in refs]
        if not available:
            return [types.TextContent(
                type="text",
                text=("OpenAlex lists no references for this work. Reference "
                      "coverage varies by publisher; try source='scopus'.")
            )]
        lines = [_reference_header(len(references), available, None, count,
                                   bool(wanted), len(wanted))]
        if check:
            lines.append(await _completeness_line(bare_doi(work.get('doi')), available))
        lines.append(str(references))
        return [types.TextContent(type="text", text="\n".join(lines))]

    raw_data = await client.get_references(scopus_id)
    all_refs = clean_references(raw_data)
    if not all_refs:
        return [types.TextContent(
            type="text",
            text=("No references returned. The document may have no indexed "
                  "references, or your API key may lack REF-view entitlement.")
        )]
    references = ([r for r in all_refs if ref_matches(r, wanted)] if wanted
                  else all_refs[:count])
    lines = [_reference_header(len(references), len(all_refs),
                               reported_reference_total(raw_data), count,
                               bool(wanted), len(wanted))]
    if check:
        details = clean_abstract_details(await client.get_abstract(to_scopus_id(scopus_id)))
        lines.append(await _completeness_line(details.get('doi'), len(all_refs)))
    lines.append(str(references))
    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_citing_papers(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    scopus_id = arguments.get("scopus_id")
    count = arguments.get("count", 5)
    sort = arguments.get("sort", "coverDate")

    if not scopus_id:
        raise ValueError("scopus_id is required")

    if _source(arguments) == 'openalex':
        work = await _resolve_openalex_work(scopus_id)
        works, _ = await openalex.citing(short_id(work['id']), max_results=count, sort=sort)
        results = [clean_openalex_work(w) for w in works]
    else:
        # Forward citations via centralized REF(2-s2.0-<id>) construction.
        raw_data = await client.get_citing_papers(scopus_id, count=count, sort=sort)
        results = clean_search_results(raw_data)

    return [types.TextContent(type="text", text=str(results))]


HANDLERS = {
    'get_references': _get_references,
    'get_citing_papers': _get_citing_papers,
}
