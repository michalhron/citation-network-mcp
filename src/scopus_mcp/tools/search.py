"""Search and records: Scopus, ScienceDirect full text, OpenAlex search, authors."""
import logging
from typing import Optional
import json

import mcp.types as types

from ..authors import clean_openalex_author, clean_scopus_author, scopus_author_query
from ..client import FULLTEXT_MIN_CHARS
from ..fulltext_search import PAGE_SIZE as SD_PAGE_SIZE, analyze_mentions, build_request, clean_result as clean_sd_result
from ..openalex import clean_openalex_work
from ..output import should_write_to_disk, write_fulltext_to_disk, write_results_to_disk
from ..records import _fetch_abstract_crossref, _fetch_abstract_openalex, clean_abstract_details, clean_author_profile, clean_identifiers, clean_search_results, detect_id_type
from ..baskets import SCOPE_SCHEMA, openalex_issn_filter, resolve_scope, scope_scopus_query
from .common import SOURCE_SCHEMA, _source, server_module

logger = logging.getLogger("scopus-plus-mcp")


# Cap on authors per search_authors call (Scopus Author Search page size).
MAX_AUTHORS = 25


# search_fulltext: results per call, and articles whose full text is analysed.
MAX_FULLTEXT_RESULTS = 1000


MAX_CONTEXT = 25


TOOLS = [
    types.Tool(
        name="search_scopus",
        description="Search for documents in Scopus using a query string.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The Scopus search query (e.g., 'TITLE(AI) AND PUBYEAR > 2020')."
                },
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
            "required": ["query"]
        }
    ),
    types.Tool(
        name="search_all",
        description=(
            "Search Scopus (or OpenAlex with source='openalex') and page through "
            "results automatically, returning up to max_results entries in one call. "
            "Scopus pages hold SCOPUS_PAGE_SIZE records (default 25) and switch to "
            "cursor paging beyond 5,000; OpenAlex pages hold 200. Results over 50 "
            "records are written to disk as JSON and CSV. Large max_results values "
            "consume significant quota — use conservatively."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query. Scopus: Scopus syntax (e.g., 'TITLE(AI) AND "
                        "PUBYEAR > 2020'). OpenAlex: plain words matched against title "
                        "and abstract; quote phrases (e.g., '\"organizing vision\"')."
                    )
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum total results to fetch across all pages (default 200). Large values consume quota.",
                    "default": 200
                },
                "sort": {
                    "type": "string",
                    "description": (
                        "Sort order (e.g., 'coverDate', 'relevancy', 'citedby'). "
                        "Defaults to 'coverDate' for Scopus, 'relevance' for OpenAlex."
                    )
                },
                "source": SOURCE_SCHEMA,
                "scope": SCOPE_SCHEMA,
                "inline": {
                    "type": "string",
                    "enum": ["sample", "compact", "full"],
                    "default": "sample",
                    "description": (
                        "What comes back in the reply when results go to files "
                        "(over 50 records). 'sample' (default): the first 10. "
                        "'compact': every record as one JSON line of key fields "
                        "(IDs, DOI, year, first author, title, venue, ISSN, "
                        "citations): use it when the caller cannot read the "
                        "server's files, e.g. from a cloud session. 'full': "
                        "every record in full (large)."
                    )
                }
            },
            "required": ["query"]
        }
    ),
    types.Tool(
        name="search_fulltext",
        description=(
            "Search the full text of Elsevier (ScienceDirect) journal articles, "
            "not just titles and abstracts: finds papers that use a construct in "
            "their body without naming it up front. Needs Scopus/ScienceDirect "
            "subscriber access; covers Elsevier-published content only. With "
            "context=true, the top results' full texts are retrieved to count "
            "mentions in the body (separately from the reference list), give "
            "their positions through the article, and quote example sentences: "
            "how a paper uses the construct, not just that it does. "
            f"Up to {MAX_FULLTEXT_RESULTS} results; over 50 are written to JSON and CSV."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "ScienceDirect query; quote phrases, e.g. '\"organizing vision\"'. AND, OR, NOT allowed."
                },
                "journal": {"type": "string", "description": "Restrict to a journal title, e.g. 'Information and Organization'."},
                "from_year": {"type": "integer", "description": "First publication year."},
                "to_year": {"type": "integer", "description": "Last publication year."},
                "open_access_only": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": 100,
                                "description": f"Results to fetch (default 100, max {MAX_FULLTEXT_RESULTS})."},
                "sort": {"type": "string", "enum": ["relevance", "date"], "default": "relevance"},
                "context": {"type": "boolean", "default": False,
                            "description": "Analyse mentions in the top results' full texts."},
                "max_context": {"type": "integer", "default": 10,
                                "description": f"Articles to analyse when context=true (default 10, max {MAX_CONTEXT}); one full-text request each."}
            },
            "required": ["query"]
        }
    ),
    types.Tool(
        name="get_abstract_details",
        description="Retrieve full details for a specific document by Scopus ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "scopus_id": {
                    "type": "string",
                    "description": "The Scopus ID of the document."
                }
            },
            "required": ["scopus_id"]
        }
    ),
    types.Tool(
        name="resolve_identifier",
        description=(
            "Resolve any document identifier (Scopus ID, EID, DOI, or PII) to "
            "the full cross-reference set (scopus_id, eid, doi, pii, title). "
            "Use this to obtain a DOI for cross-linking with OpenAlex/Crossref, "
            "or to normalize an ID before calling other tools."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "The identifier value (e.g. '0031512927', '2-s2.0-0031512927', or a DOI)."
                },
                "id_type": {
                    "type": "string",
                    "description": "Optional override of the identifier type.",
                    "enum": ["scopus_id", "eid", "doi", "pii"]
                }
            },
            "required": ["identifier"]
        }
    ),
    types.Tool(
        name="search_authors",
        description=(
            "Find authors by name, optionally narrowed by affiliation. Scopus "
            "(default, needs subscriber entitlement): author IDs for "
            "get_author_profile, document counts, current affiliation, subject "
            "areas and name variants, ranked by document count. OpenAlex: "
            "OpenAlex author IDs, ORCID, works and citation counts, h-index, "
            "institution and topics. Common surnames need an affiliation or "
            "given name to be useful."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "'Surname, Given names' or 'Given names Surname', e.g. 'Swanson, E. Burton'."
                },
                "affiliation": {
                    "type": "string",
                    "description": "Optional affiliation words to narrow the match, e.g. 'Los Angeles'."
                },
                "count": {
                    "type": "integer",
                    "description": f"Number of authors to return (default 10, max {MAX_AUTHORS}).",
                    "default": 10
                },
                "source": SOURCE_SCHEMA
            },
            "required": ["name"]
        }
    ),
    types.Tool(
        name="get_author_profile",
        description="Retrieve an author's profile by Author ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "author_id": {
                    "type": "string",
                    "description": "The Scopus Author ID."
                }
            },
            "required": ["author_id"]
        }
    ),
    types.Tool(
        name="get_fulltext",
        description=(
            "Retrieve the full text of a paper via a provider waterfall: "
            "(1) ScienceDirect full text (requires SCOPUS_INSTTOKEN or institutional IP), "
            "(2) open-access copy: every open location in OpenAlex, Semantic Scholar's "
            "open PDF and arXiv ID, arXiv by exact title, Europe PMC, and Unpaywall "
            "or CORE when configured; published versions first, and the result names "
            "the source and version (preprint, accepted manuscript, published), "
            "(3) Scopus abstract fallback. "
            "Returns provenance, character count, file path, and a ~1500-char sample. "
            "Full body is written to disk — never returned inline. "
            "ToS note: retrieval is for the user's own non-commercial text-and-data-mining; "
            "content written to local disk must not be redistributed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "doi": {
                    "type": "string",
                    "description": "The DOI of the paper (e.g. '10.1016/j.infoandorg.2026.100608')."
                },
                "prefer": {
                    "type": "string",
                    "description": "Skip straight to a tier for testing: 'sciencedirect', 'oa', 'abstract'.",
                    "enum": ["sciencedirect", "oa", "abstract"]
                }
            },
            "required": ["doi"]
        }
    ),
]


async def _search_scopus(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    query = arguments.get("query")
    count = arguments.get("count", 5)
    sort = arguments.get("sort", "coverDate")

    if not query:
        raise ValueError("Query is required")

    # Await the async client method
    raw_data = await client.search_scopus(query, count=count, sort=sort)
    results = clean_search_results(raw_data)

    return [types.TextContent(type="text", text=str(results))]


COMPACT_FIELDS = ('scopus_id', 'openalex_id', 'doi', 'year', 'creator', 'title',
                  'publication_name', 'issn', 'cited_by_count')


def compact_lines(records: list) -> str:
    """One JSON object per record with only the key fields (None dropped)."""
    import json
    out = []
    for r in records:
        row = {k: r.get(k) for k in COMPACT_FIELDS if r.get(k) not in (None, '')}
        if not row.get('year') and r.get('cover_date'):
            row['year'] = str(r['cover_date'])[:4]
        out.append(json.dumps(row, ensure_ascii=False))
    return '\n'.join(out)


async def _search_all(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    query = arguments.get("query")
    max_results = arguments.get("max_results", 200)
    source = _source(arguments)

    if not query:
        raise ValueError("query is required")

    inline = arguments.get("inline") or "sample"
    if inline not in ("sample", "compact", "full"):
        raise ValueError("inline must be 'sample', 'compact' or 'full'")
    issns = resolve_scope(arguments.get("scope"))

    if source == 'openalex':
        sort = arguments.get("sort", "relevance")
        kwargs = {'extra_filter': openalex_issn_filter(issns)} if issns else {}
        works, meta = await openalex.search(query, max_results=max_results, sort=sort, **kwargs)
        results = [clean_openalex_work(w) for w in works]
    else:
        sort = arguments.get("sort", "coverDate")
        query = scope_scopus_query(query, issns)
        raw_data = await client.search_all(query, max_results=max_results, sort=sort)
        results = clean_search_results(raw_data)
        meta = raw_data.get('_meta', {})

    if should_write_to_disk(results):
        paths = write_results_to_disk(results, query)
        head = (
            f"Fetched {meta.get('total_fetched', len(results))} records "
            f"(total available: {meta.get('total_available', 'unknown')}, "
            f"truncated: {meta.get('truncated', False)}).\n"
            f"Full results written to disk:\n"
            f"  JSON: {paths['json_path']}\n"
            f"  CSV:  {paths['csv_path']}\n\n"
        )
        if inline == 'compact':
            body = (f"All {len(results)} records, one JSON object per line "
                    f"(fields: {', '.join(COMPACT_FIELDS)}):\n"
                    + compact_lines(results))
        elif inline == 'full':
            body = f"All {len(results)} records:\n{results}"
        else:
            body = (f"First 10 records (inline='compact' returns all of them):\n"
                    f"{results[:10]}")
        text = head + body
    else:
        text = str(results)

    if source == 'openalex':
        text = "Source: OpenAlex (title and abstract search).\n" + text
    if meta.get('note'):
        text += f"\n\nNote: {meta['note']}"

    return [types.TextContent(type="text", text=text)]


async def _search_fulltext(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    query = (arguments.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    max_results = max(1, min(int(arguments.get("max_results", 100)), MAX_FULLTEXT_RESULTS))
    sort = arguments.get("sort") or "relevance"
    if sort not in ("relevance", "date"):
        raise ValueError("sort must be 'relevance' or 'date'")
    filters = dict(
        journal=(arguments.get("journal") or "").strip() or None,
        from_year=arguments.get("from_year"), to_year=arguments.get("to_year"),
        open_access_only=bool(arguments.get("open_access_only", False)),
    )

    results, total = [], None
    while len(results) < max_results:
        page = await client.search_sciencedirect(build_request(
            query, offset=len(results),
            show=min(SD_PAGE_SIZE, max_results - len(results)), sort=sort, **filters))
        if total is None:
            total = page.get('resultsFound')
        batch = [clean_sd_result(e) for e in (page.get('results') or [])]
        if not batch:
            break
        results.extend(batch)
    results = results[:max_results]

    summary = {
        'source': 'sciencedirect', 'query': query, 'total_available': total,
        'fetched': len(results),
        'note': "Full-text matches in Elsevier-published articles only.",
    }
    if arguments.get("context"):
        limit = max(1, min(int(arguments.get("max_context", 10)), MAX_CONTEXT))
        analysed = []
        for rec in results[:limit]:
            entry = {'doi': rec['doi'], 'title': rec['title']}
            try:
                sd = await client.get_sciencedirect_fulltext(rec['doi']) if rec['doi'] else None
            except Exception as exc:
                sd = None
                entry['error'] = str(exc)[:200]
            text = ((sd or {}).get('full-text-retrieval-response') or {}).get('originalText') or ''
            if len(text.strip()) >= FULLTEXT_MIN_CHARS:
                entry.update(analyze_mentions(text, query))
            else:
                entry.setdefault('error', "full text not available with this access")
            analysed.append(entry)
        summary['context'] = analysed
        summary['context_note'] = (
            "body_mentions exclude the reference list; positions_pct run "
            "0-100 through the article before its references. The lowest "
            "positions can be the abstract or keyword list rather than the "
            "text; 0 body mentions with reference-list mentions means the "
            "paper cites the work without using the term.")

    if should_write_to_disk(results):
        paths = write_results_to_disk(results, f"fulltext {query}")
        summary.update({'json_path': paths['json_path'], 'csv_path': paths['csv_path'],
                        'sample': results[:10]})
    else:
        summary['results'] = results
    return [types.TextContent(type="text", text=json.dumps(summary, indent=2, ensure_ascii=False))]


async def _get_abstract_details(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    scopus_id = arguments.get("scopus_id")
    if not scopus_id:
        raise ValueError("scopus_id is required")

    raw_data = await client.get_abstract(scopus_id)
    details = clean_abstract_details(raw_data)

    # Fallback: fetch abstract from OpenAlex then Crossref when Scopus withholds it
    if not details.get('description') and details.get('doi'):
        doi = details['doi']
        abstract = await _fetch_abstract_openalex(doi)
        if abstract:
            details['description'] = abstract
            details['abstract_source'] = 'openalex'
        else:
            abstract = await _fetch_abstract_crossref(doi)
            if abstract:
                details['description'] = abstract
                details['abstract_source'] = 'crossref'

    return [types.TextContent(type="text", text=str(details))]


async def _resolve_identifier(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    identifier = arguments.get("identifier")
    if not identifier:
        raise ValueError("identifier is required")

    id_type = arguments.get("id_type") or detect_id_type(identifier)
    raw_data = await client.get_abstract_by(identifier, id_type=id_type)
    ids = clean_identifiers(raw_data)
    if not ids:
        return [types.TextContent(
            type="text",
            text=f"No record found for {identifier!r} (resolved as id_type={id_type})."
        )]
    return [types.TextContent(type="text", text=str(ids))]


async def _search_authors(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    openalex = srv.openalex
    author_name = (arguments.get("name") or "").strip()
    if not author_name:
        raise ValueError("name is required")
    affiliation = (arguments.get("affiliation") or "").strip() or None
    count = max(1, min(int(arguments.get("count", 10)), MAX_AUTHORS))

    if _source(arguments) == 'openalex':
        # OpenAlex cannot filter authors by institution name, so fetch a
        # full page and keep those whose last known institution matches.
        found = await openalex.search_authors(
            author_name, count=MAX_AUTHORS if affiliation else count)
        authors = [clean_openalex_author(a) for a in found]
        if affiliation:
            words = affiliation.lower().split()
            authors = [a for a in authors
                       if all(w in (a['affiliation'] or '').lower() for w in words)]
        authors = authors[:count]
        query = author_name + (f" (affiliation contains {affiliation!r})" if affiliation else "")
    else:
        query = scopus_author_query(author_name, affiliation)
        raw = await client.search_authors(query, count=count)
        entries = (raw.get('search-results') or {}).get('entry') or []
        authors = [clean_scopus_author(e) for e in entries
                   if isinstance(e, dict) and e.get('dc:identifier')]
    result = {'source': _source(arguments), 'query': query, 'authors': authors}
    if not authors and _source(arguments) == 'openalex' and affiliation:
        result['note'] = (
            f"None of the top {MAX_AUTHORS} OpenAlex matches for the name has "
            f"that affiliation (OpenAlex cannot filter authors by institution "
            f"name). Add given names to the name.")
    elif not authors:
        result['note'] = "No matching authors; try fewer given names or a broader affiliation."
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _get_author_profile(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    author_id = arguments.get("author_id")
    if not author_id:
        raise ValueError("author_id is required")

    raw_data = await client.get_author(author_id)
    profile = clean_author_profile(raw_data)

    return [types.TextContent(type="text", text=str(profile))]


async def _get_fulltext(arguments: dict) -> list:
    srv = server_module()
    client = srv.client
    fetch_oa_fulltext = srv.fetch_oa_fulltext
    doi = (arguments.get("doi") or "").strip()
    if not doi:
        raise ValueError("doi is required")
    prefer = arguments.get("prefer")  # None → full waterfall

    text_body: Optional[str] = None
    provenance: str = "none"
    source_url: Optional[str] = None
    oa_source = oa_version = None
    oa_attempts: list = []

    # ── Tier 1: ScienceDirect ────────────────────────────────────────
    if prefer in (None, "sciencedirect"):
        try:
            sd_data = await client.get_sciencedirect_fulltext(doi)
            if sd_data:
                root = sd_data.get('full-text-retrieval-response') or {}
                candidate = (root.get('originalText') or '').strip()
                # Same bar as diagnose_connection's full-text probe: an
                # abstract plus metadata can exceed a few hundred
                # characters, and abstract-grade text must never be
                # labelled full text. Shorter bodies fall through to OA.
                if len(candidate) >= FULLTEXT_MIN_CHARS:
                    text_body = candidate
                    provenance = "sciencedirect-fulltext"
        except Exception as exc:
            logger.warning(f"get_fulltext: SD tier error for doi={doi}: {exc}")

    # ── Tier 2: Open-access ──────────────────────────────────────────
    if text_body is None and prefer in (None, "oa"):
        oa_result = await fetch_oa_fulltext(doi)
        source_url = oa_result.get('source_url')
        oa_attempts = oa_result.get('attempts') or []
        if oa_result.get('text'):
            text_body = oa_result['text']
            provenance = "oa-fulltext"
            oa_source, oa_version = oa_result.get('source'), oa_result.get('version')

    # ── Tier 3: Abstract fallback ────────────────────────────────────
    if text_body is None and prefer in (None, "abstract"):
        try:
            raw_abs = await client.get_abstract_by(doi, id_type='doi')
            details = clean_abstract_details(raw_abs)
            description = details.get('description') or ''
            if description:
                text_body = description
                provenance = "scopus-abstract"
        except Exception as exc:
            logger.warning(f"get_fulltext: abstract tier error for doi={doi}: {exc}")

    # ── Build response ───────────────────────────────────────────────
    char_count = len(text_body) if text_body else 0
    file_path: Optional[str] = None

    # SD and OA full text always written to disk (can be large).
    # Short abstracts (<= 3000 chars) are returned inline.
    ABSTRACT_INLINE_MAX = 3000
    write_to_disk = text_body and provenance in ('sciencedirect-fulltext', 'oa-fulltext')
    if not write_to_disk and text_body and char_count > ABSTRACT_INLINE_MAX:
        write_to_disk = True
    if write_to_disk:
        file_path = write_fulltext_to_disk(doi, text_body)
        sample = text_body[:1500]
    else:
        sample = text_body or ''

    summary: dict = {
        'doi': doi,
        'provenance': provenance,
        'char_count': char_count,
    }
    if source_url:
        summary['source_url'] = source_url
    if oa_source:
        # Which open copy, and which version: a preprint may differ from the
        # published article, so quote accordingly.
        summary['oa_source'] = oa_source
        summary['oa_version'] = oa_version or 'unknown'
        summary['oa_title_verified'] = bool(oa_result.get('title_verified'))
    if oa_attempts:
        summary['oa_attempts'] = oa_attempts
    if file_path:
        summary['file_path'] = file_path
    summary['sample'] = sample

    import json as _json
    return [types.TextContent(type="text", text=_json.dumps(summary, ensure_ascii=False, indent=2))]


HANDLERS = {
    'search_scopus': _search_scopus,
    'search_all': _search_all,
    'search_fulltext': _search_fulltext,
    'get_abstract_details': _get_abstract_details,
    'resolve_identifier': _resolve_identifier,
    'search_authors': _search_authors,
    'get_author_profile': _get_author_profile,
    'get_fulltext': _get_fulltext,
}
