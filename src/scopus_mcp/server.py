import asyncio
import json
from datetime import date as _today_date
from datetime import datetime as _today_datetime
import logging
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from . import __version__
from .authors import clean_openalex_author, clean_scopus_author, scopus_author_query
from .bibtex import fetch_bibtex, generated_entry, make_keys_unique
from .client import FULLTEXT_MIN_CHARS, ScopusClient
from .fulltext_search import PAGE_SIZE as SD_PAGE_SIZE
from .fulltext_search import analyze_mentions, build_request, clean_result as clean_sd_result
from .journals import (
    category_names,
    clean_openalex_source,
    clean_serial_entry,
    format_issn,
    normalize_issn,
    resolve_categories,
    serial_entry_issns,
    srcid_queries,
    subject_ranks,
)
from .openalex import (
    OpenAlexClient,
    clean_openalex_work,
    openalex_work_key,
    short_id,
)
from .utils import (
    _output_dir,
    clean_search_results,
    clean_abstract_details,
    clean_author_profile,
    clean_identifiers,
    clean_references,
    detect_id_type,
    to_scopus_id,
    to_eid,
    write_results_to_disk,
    should_write_to_disk,
    compute_pairwise_edges,
    write_graph_to_disk,
    _make_node_label,
    write_lineage_to_disk,
    compute_main_path,
    render_lineage_html,
    render_lineage_png,
    _query_slug,
    fetch_oa_fulltext,
    write_fulltext_to_disk,
    _fetch_abstract_openalex,
    _fetch_abstract_crossref,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("citation-network-mcp")

SERVER_VERSION = __version__

# Initialize Server
SERVER_INSTRUCTIONS = (
    "Literature and citation-network tools over Scopus (default) or OpenAlex "
    "(source='openalex'). When a Scopus call fails, especially with 'Error "
    "translating query', run diagnose_connection: it names the tools the "
    "current access cannot use. Without Scopus subscriber access, pass "
    "source='openalex'. Keep one source per analysis: IDs and citation graphs "
    "differ between them. Large results are written to files; report the paths."
)

server = Server(
    "citation-network-mcp",
    version=__version__,
    instructions=SERVER_INSTRUCTIONS,
    website_url="https://github.com/michalhron/citation-network-mcp",
)
client = ScopusClient()
# OpenAlex backend: the same analyses without Scopus subscriber entitlement.
openalex = OpenAlexClient()

# Cap on the year span of a Scopus publication_counts call (one request per year).
MAX_SCOPUS_YEARS = 60
# Cap on identifiers per get_bibtex call.
MAX_BIBTEX = 200
# Cap on journals per get_journal_metrics call.
MAX_JOURNALS = 200
# Cap on authors per search_authors call (Scopus Author Search page size).
MAX_AUTHORS = 25
# topic_landscape: papers analysed per call.
MAX_LANDSCAPE_PAPERS = 2000

# search_fulltext: results per call, and articles whose full text is analysed.
MAX_FULLTEXT_RESULTS = 1000
MAX_CONTEXT = 25

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

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
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
        types.Tool(
            name="diagnose_connection",
            description=(
                "Diagnose Scopus connectivity and entitlement. Checks config "
                "presence, api.elsevier.com reachability, metadata and search "
                "entitlement, and per-API capabilities (REF-view references, "
                "ScienceDirect full text, Serial Title journal metrics). Returns "
                "a JSON report with a one-line verdict and 'unavailable_tools', "
                "the tools that cannot work with the current access. Run this "
                "first when Scopus behaves strangely "
                "— especially when valid searches fail with 'Error translating "
                "query', which usually means missing subscriber entitlement "
                "(off-network without SCOPUS_INSTTOKEN), not bad query syntax."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="get_quota_status",
            description="Get the current API quota status (remaining/limit). Note: Values are updated only after making a request.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
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
                    "source": SOURCE_SCHEMA
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="find_journals",
            description=(
                "List the journals in one or more Scopus subject categories at or above "
                "a CiteScore percentile within that category: the quality cut-off for "
                "scoping a literature review (Q1 = 75, top 10% = 90). Categories are ASJC "
                "names or codes, e.g. 'Information Systems' (1710), 'Management "
                "Information Systems' (1404); ambiguous names return the candidates. "
                "Returns each journal's rank, percentile, quartile and CiteScore, a CSV, "
                "and ready-to-use Scopus query fragments SRCID(...) for search_all. "
                "Percentiles are from the latest complete CiteScore year."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "categories": {
                        "type": "array", "items": {"type": "string"},
                        "description": "ASJC category names or 4-digit codes."
                    },
                    "min_percentile": {
                        "type": "integer", "default": 75, "minimum": 0, "maximum": 99,
                        "description": "Keep journals at or above this percentile in the category (75 = Q1, 90 = top 10%)."
                    },
                    "journals_only": {
                        "type": "boolean", "default": True,
                        "description": "Exclude book series, conference proceedings and trade journals."
                    }
                },
                "required": ["categories"]
            }
        ),
        types.Tool(
            name="topic_landscape",
            description=(
                "Where and at what prestige a topic is published. Runs a Scopus query "
                "and reports (1) papers per broad subject area over all results, and "
                "(2) per subject category, how many papers appear in Q1, Q2, Q3 and Q4 "
                "journals of that category, with the main journals. A journal can be "
                "Q1 in one category and Q3 in another, so each paper counts in every "
                "category of its journal. Venues without CiteScore ranks, such as "
                "conference proceedings, are reported separately. Large topics are "
                f"analysed on the most recent max_papers papers (up to {MAX_LANDSCAPE_PAPERS}); "
                "coverage is stated. Needs Scopus search entitlement."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Scopus query, e.g. 'TITLE-ABS-KEY(\"organizing vision\")'."
                    },
                    "from_year": {"type": "integer", "description": "First publication year."},
                    "to_year": {"type": "integer", "description": "Last publication year."},
                    "max_papers": {"type": "integer", "default": 500,
                                   "description": f"Papers to analyse by quartile (default 500, max {MAX_LANDSCAPE_PAPERS})."},
                    "top_categories": {"type": "integer", "default": 15,
                                       "description": "Categories to report, largest first."}
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
            name="get_journal_metrics",
            description=(
                "Journal metrics for a list of journals, e.g. a litbaskets basket. "
                "Scopus (default): SJR, SNIP, CiteScore and CiteScore Tracker with "
                "their years, plus subject areas, from the Serial Title API. Give "
                "ISSNs, or Scopus source IDs (SRCIDs), which are mapped to ISSNs "
                "through one Scopus search each (needs search entitlement). "
                "source='openalex': OpenAlex's own measures (2-year mean citedness, "
                "h-index, i10-index), ISSNs only, no entitlement. Journals not found "
                "are listed, never dropped. Also written to CSV. "
                f"At most {MAX_JOURNALS} journals per call."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issns": {
                        "type": "array", "items": {"type": "string"},
                        "description": "ISSNs, with or without hyphen."
                    },
                    "source_ids": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Scopus source IDs (SRCID), Scopus only."
                    },
                    "source": SOURCE_SCHEMA
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_bibtex",
            description=(
                "BibTeX entries for a list of papers, written to a .bib file and "
                "returned inline. Identifiers may be DOIs, Scopus IDs/EIDs or "
                "OpenAlex work IDs. Entries come from the publisher's metadata via "
                "DOI content negotiation (errors included, so check author names). "
                "Papers without a DOI, such as AIS conference papers, get a minimal "
                "entry built from Scopus or OpenAlex metadata, marked with a note. "
                f"At most {MAX_BIBTEX} identifiers per call."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "identifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "DOIs, Scopus IDs/EIDs, or OpenAlex work IDs (W...)."
                    }
                },
                "required": ["identifiers"]
            }
        ),
        types.Tool(
            name="publication_counts",
            description=(
                "Count publications per year for a query, e.g. to chart how "
                "attention to a topic rose and fell. Scopus: your query in Scopus "
                "syntax, one request per year, so from_year and to_year are required "
                f"(at most {MAX_SCOPUS_YEARS} years). OpenAlex: plain words matched "
                "against title and abstract (quote phrases), one request for all "
                "years. The two sources count differently; compare trends within "
                "one source, not levels across sources."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Scopus: Scopus syntax, e.g. 'TITLE-ABS-KEY(\"organizing "
                            "vision\")'. OpenAlex: e.g. '\"organizing vision\"'."
                        )
                    },
                    "from_year": {"type": "integer", "description": "First year (inclusive)."},
                    "to_year": {"type": "integer", "description": "Last year (inclusive)."},
                    "source": SOURCE_SCHEMA
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="bibliographic_coupling",
            description=(
                "Build a bibliographic-coupling graph for a set of seed papers. "
                "Two seeds are coupled when they share cited references; edge weight = "
                "count of shared references, cosine = Salton index. "
                "Maps the current research front. Scopus needs an entitled (subscriber) "
                "key for REF-view access; source='openalex' does not. "
                "Output: GraphML + CSV edge list written to disk."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "seed_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Seed papers: Scopus IDs (bare numeric or SCOPUS_ID: prefixed). "
                            "With source='openalex', DOIs and OpenAlex work IDs also work."
                        )
                    },
                    "source": SOURCE_SCHEMA,
                    "min_shared": {
                        "type": "integer",
                        "description": "Minimum shared references for an edge to be emitted (default 2).",
                        "default": 2
                    }
                },
                "required": ["seed_ids"]
            }
        ),
        types.Tool(
            name="co_citation",
            description=(
                "Build a co-citation graph for a set of seed papers. "
                "Two seeds are co-cited when a later paper cites both; edge weight = "
                "count of co-citing papers, cosine = Salton index. "
                "Maps the intellectual base of a field. "
                "max_citing_per_seed bounds the API quota used per seed. "
                "Output: GraphML + CSV edge list written to disk."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "seed_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Seed papers: Scopus IDs (bare numeric or SCOPUS_ID: prefixed). "
                            "With source='openalex', DOIs and OpenAlex work IDs also work."
                        )
                    },
                    "source": SOURCE_SCHEMA,
                    "min_shared": {
                        "type": "integer",
                        "description": "Minimum co-citing papers for an edge to be emitted (default 2).",
                        "default": 2
                    },
                    "max_citing_per_seed": {
                        "type": "integer",
                        "description": "Cap on citing papers fetched per seed (default 500). Limits quota usage.",
                        "default": 500
                    }
                },
                "required": ["seed_ids"]
            }
        ),
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
                        "description": "Maximum number of references to return (default 25).",
                        "default": 25
                    }
                },
                "required": ["scopus_id"]
            }
        ),
        types.Tool(
            name="get_fulltext",
            description=(
                "Retrieve the full text of a paper via a provider waterfall: "
                "(1) ScienceDirect full text (requires SCOPUS_INSTTOKEN or institutional IP), "
                "(2) open-access copy via OpenAlex + direct fetch, "
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
        types.Tool(
            name="get_server_info",
            description=(
                "Return the server version and a health summary. "
                "Call this to confirm which build you are talking to."
            ),
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="citation_lineage",
            description=(
                "Walk the citation lineage of a seed paper across multiple generations. "
                "Forward: generation 1 = papers that cite the seed; generation 2 = papers "
                "that cite those; up to 3 generations. Backward: walks cited references. "
                "All papers are deduplicated globally. Output: corpus written to disk as "
                "JSON plus compact inline summary; the corpus is also returned inline as "
                "base64 so sandboxed callers can inspect it. Use sort='citedby' (default "
                "for forward) to collect the most-cited citers first, which gives a "
                "meaningful citation-backbone; sort='coverDate' collects the most recent "
                "citers first (which can produce a recency-dominated walk). "
                "source='openalex' walks OpenAlex instead (no Scopus entitlement; "
                "node IDs are OpenAlex work IDs; reference lists are thinner and "
                "absent for AIS eLibrary papers). "
                "Server version is included in every response."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "seed_id": {
                        "type": "string",
                        "description": (
                            "Scopus ID or EID of the seed paper. With source='openalex', "
                            "a DOI or OpenAlex work ID also works."
                        )
                    },
                    "source": SOURCE_SCHEMA,
                    "generations": {
                        "type": "integer",
                        "description": "Number of generations to walk (default 1, max 3).",
                        "default": 1,
                        "maximum": 3
                    },
                    "max_per_node": {
                        "type": "integer",
                        "description": "Cap on citing papers fetched per paper per generation (default 200).",
                        "default": 200
                    },
                    "min_citing": {
                        "type": "integer",
                        "description": (
                            "Only expand papers that have at least this many citing papers "
                            "(default 0 = expand all up to max_per_node). Pruning high "
                            "values avoids exploding on trivially-cited nodes. "
                            "Ignored for backward direction."
                        ),
                        "default": 0
                    },
                    "direction": {
                        "type": "string",
                        "description": (
                            "'forward' (default): walk citing papers via search_all + REF(). "
                            "Fan-out can be large; use max_per_node to bound quota. "
                            "'backward': walk cited references via get_references, "
                            "up to max_per_node per paper; references with no ID "
                            "are skipped."
                        ),
                        "enum": ["forward", "backward"],
                        "default": "forward"
                    },
                    "sort": {
                        "type": "string",
                        "description": (
                            "How to rank citing papers before the max_per_node cap is "
                            "applied (forward direction only; ignored for backward). "
                            "'citedby' (default): highest citation count first — captures "
                            "the high-flow backbone. "
                            "'coverDate': most recent first — captures the current fringe "
                            "but may produce a recency-dominated walk on high-citation seeds. "
                            "'relevancy': Scopus relevance score (Scopus only)."
                        ),
                        "enum": ["citedby", "coverDate", "relevancy"],
                        "default": "citedby"
                    }
                },
                "required": ["seed_id"]
            }
        )
    ]

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


async def _openalex_seed_sets(seed_ids: list, mode: str, max_citing: int = 500):
    """Per-seed reference sets ('references') or citer sets ('citing').

    Returns (seed_sets, seed_meta, skipped) keyed by OpenAlex work ID, the
    same structure the Scopus branches build.
    """
    seed_sets: dict = {}
    seed_meta: dict = {}
    skipped: list = []
    for raw_id in seed_ids:
        try:
            work = await _resolve_openalex_work(raw_id)
            wid = short_id(work.get('id'))
            rec = clean_openalex_work(work)
            seed_meta[wid] = {
                'title': rec['title'] or wid,
                'creator': rec['creator'],
                'year': rec['year'],
                'venue': rec['publication_name'],
            }
            if mode == 'references':
                keys = {short_id(r) for r in (work.get('referenced_works') or [])}
            else:
                citers, _ = await openalex.citing(wid, max_results=max_citing)
                keys = {short_id(c.get('id')) for c in citers}
            keys.discard(wid)
        except Exception as exc:
            logger.warning(f"openalex {mode} for {raw_id}: {exc}")
            skipped.append(f"{raw_id} ({exc})")
            continue
        if not keys:
            # Common for AIS eLibrary papers: OpenAlex indexes them without
            # reference lists.
            what = 'references' if mode == 'references' else 'citing works'
            skipped.append(f"{raw_id} (OpenAlex {wid} lists no {what})")
            continue
        seed_sets[wid] = keys
    return seed_sets, seed_meta, skipped


def _bibtex_author(display_name: str) -> str:
    """'Norman P. Hummon' -> 'Hummon, Norman P.' (BibTeX 'Last, First')."""
    parts = display_name.split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else display_name


async def _bibtex_target(identifier: str):
    """(doi, fallback metadata, origin) for one identifier.

    DOIs pass straight through. OpenAlex IDs and Scopus IDs are looked up
    for their DOI, keeping their metadata for a generated entry when there
    is none (Scopus metadata needs no subscriber entitlement).
    """
    key = openalex_work_key(identifier)
    if key and key.startswith('doi:'):
        return key[4:], None, None
    if key:
        work = await openalex.get_work(key)
        if not work:
            raise ValueError("not found in OpenAlex")
        rec = clean_openalex_work(work)
        authors = [_bibtex_author(a['author']['display_name'])
                   for a in (work.get('authorships') or [])
                   if (a.get('author') or {}).get('display_name')]
        meta = {'title': rec['title'], 'authors': authors,
                'year': rec['year'], 'venue': rec['publication_name']}
        return rec['doi'], meta, 'OpenAlex'
    details = clean_abstract_details(await client.get_abstract(to_scopus_id(identifier)))
    if not details.get('title'):
        raise ValueError("not found in Scopus")
    authors = []
    for a in details.get('authors') or []:
        surname = a.get('surname') or (a.get('name') or '').split(' ')[0]
        if surname:
            initials = a.get('initials')
            authors.append(f"{surname}, {initials}" if initials else surname)
    meta = {'title': details.get('title'), 'authors': authors,
            'year': (details.get('cover_date') or '')[:4] or None,
            'venue': details.get('publication_name')}
    return details.get('doi'), meta, 'Scopus'


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


def _network_response(label: str, file_prefix: str, seed_sets: dict, seed_meta: dict,
                      n_seeds: int, params_note: str, skipped: list,
                      skipped_reason: str, source: str, min_shared: int):
    """Shared tail of bibliographic_coupling and co_citation."""
    edges = compute_pairwise_edges(seed_sets, min_shared=min_shared)
    nodes = [
        {
            'id': sid,
            'label': _make_node_label(seed_meta.get(sid, {}), node_id=sid),
            'title': seed_meta.get(sid, {}).get('title'),
            'creator': seed_meta.get(sid, {}).get('creator'),
            'year': seed_meta.get(sid, {}).get('year'),
            'venue': seed_meta.get(sid, {}).get('venue'),
        }
        for sid in seed_sets
    ]
    prefix = f'{file_prefix}-openalex' if source == 'openalex' else file_prefix
    paths = write_graph_to_disk(nodes, edges, f'{prefix}-{n_seeds}seeds')

    top10 = edges[:10]
    top10_lines = [
        f"  {seed_meta.get(e['source'], {}).get('title', e['source'])!r} → "
        f"{seed_meta.get(e['target'], {}).get('title', e['target'])!r}: "
        f"weight={e['weight']}, cosine={e['cosine']:.4f}"
        for e in top10
    ]
    text = (
        f"{label}: {len(seed_sets)}/{n_seeds} seeds processed, "
        f"{len(edges)} edges emitted ({params_note}).\n"
    )
    if source == 'openalex':
        text += "Source: OpenAlex (node IDs are OpenAlex work IDs).\n"
    text += (
        f"Graph files:\n"
        f"  GraphML: {paths['graphml_path']}\n"
        f"  CSV:     {paths['csv_path']}\n"
    )
    if paths.get('png_path'):
        text += f"  PNG:     {paths['png_path']}\n"
    text += f"\nTop {len(top10)} edges by weight:\n"
    text += '\n'.join(top10_lines) if top10_lines else '  (none)'
    if skipped:
        text += f"\n\nSkipped ({skipped_reason}): {skipped}"
    return [types.TextContent(type="text", text=text)]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if not arguments:
        arguments = {}

    try:
        if name == "search_scopus":
            query = arguments.get("query")
            count = arguments.get("count", 5)
            sort = arguments.get("sort", "coverDate")
            
            if not query:
                raise ValueError("Query is required")

            # Await the async client method
            raw_data = await client.search_scopus(query, count=count, sort=sort)
            results = clean_search_results(raw_data)
            
            return [types.TextContent(type="text", text=str(results))]

        elif name == "get_abstract_details":
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

        elif name == "get_author_profile":
            author_id = arguments.get("author_id")
            if not author_id:
                raise ValueError("author_id is required")
                
            raw_data = await client.get_author(author_id)
            profile = clean_author_profile(raw_data)
            
            return [types.TextContent(type="text", text=str(profile))]

        elif name == "get_citing_papers":
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

        elif name == "resolve_identifier":
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

        elif name == "get_references":
            scopus_id = arguments.get("scopus_id")
            count = arguments.get("count", 25)
            if not scopus_id:
                raise ValueError("scopus_id is required")

            if _source(arguments) == 'openalex':
                work = await _resolve_openalex_work(scopus_id)
                refs = await openalex.references(work, limit=count)
                references = [clean_openalex_work(w) for w in refs]
                if not references:
                    return [types.TextContent(
                        type="text",
                        text=("OpenAlex lists no references for this work. Reference "
                              "coverage varies by publisher; try source='scopus'.")
                    )]
                return [types.TextContent(type="text", text=str(references))]

            raw_data = await client.get_references(scopus_id)
            references = clean_references(raw_data, limit=count)
            if not references:
                return [types.TextContent(
                    type="text",
                    text=("No references returned. The document may have no indexed "
                          "references, or your API key may lack REF-view entitlement.")
                )]
            return [types.TextContent(type="text", text=str(references))]

        elif name == "search_all":
            query = arguments.get("query")
            max_results = arguments.get("max_results", 200)
            source = _source(arguments)

            if not query:
                raise ValueError("query is required")

            if source == 'openalex':
                sort = arguments.get("sort", "relevance")
                works, meta = await openalex.search(query, max_results=max_results, sort=sort)
                results = [clean_openalex_work(w) for w in works]
            else:
                sort = arguments.get("sort", "coverDate")
                raw_data = await client.search_all(query, max_results=max_results, sort=sort)
                results = clean_search_results(raw_data)
                meta = raw_data.get('_meta', {})

            if should_write_to_disk(results):
                paths = write_results_to_disk(results, query)
                sample = results[:10]
                text = (
                    f"Fetched {meta.get('total_fetched', len(results))} records "
                    f"(total available: {meta.get('total_available', 'unknown')}, "
                    f"truncated: {meta.get('truncated', False)}).\n"
                    f"Full results written to disk:\n"
                    f"  JSON: {paths['json_path']}\n"
                    f"  CSV:  {paths['csv_path']}\n\n"
                    f"First 10 records:\n{sample}"
                )
            else:
                text = str(results)

            if source == 'openalex':
                text = "Source: OpenAlex (title and abstract search).\n" + text
            if meta.get('note'):
                text += f"\n\nNote: {meta['note']}"

            return [types.TextContent(type="text", text=text)]

        elif name == "bibliographic_coupling":
            seed_ids_raw = arguments.get("seed_ids", [])
            min_shared = int(arguments.get("min_shared", 2))
            source = _source(arguments)

            if not seed_ids_raw:
                raise ValueError("seed_ids is required and must be non-empty")

            seed_sets: dict = {}
            seed_meta: dict = {}
            skipped: list = []

            if source == 'openalex':
                seed_sets, seed_meta, skipped = await _openalex_seed_sets(seed_ids_raw, 'references')
            else:
                for raw_id in seed_ids_raw:
                    sid = to_scopus_id(str(raw_id))
                    try:
                        raw = await client.get_references(sid)
                        refs = clean_references(raw)
                        ref_keys: set = set()
                        for r in refs:
                            key = r.get('scopus_id') or r.get('doi')
                            if key and key != sid:
                                ref_keys.add(key)
                        if not ref_keys:
                            skipped.append(sid)
                            logger.info(f"bibliographic_coupling: {sid} has no usable refs, skipping")
                            continue
                        seed_sets[sid] = ref_keys
                        # The REF view's coredata often lacks the title; fall back to
                        # the (cached) abstract so labels are not bare IDs.
                        details = clean_abstract_details(raw)
                        if not details.get('title'):
                            details = clean_abstract_details(await client.get_abstract(sid))
                        authors = details.get('authors') or []
                        seed_meta[sid] = {
                            'title': details.get('title') or sid,
                            'creator': authors[0].get('name') if authors else None,
                            'year': (details.get('cover_date') or '')[:4] or None,
                            'venue': details.get('publication_name'),
                        }
                    except Exception as exc:
                        logger.warning(f"bibliographic_coupling: error for {sid}: {exc}")
                        skipped.append(sid)

            return _network_response(
                'Bibliographic coupling', 'bibcoupling', seed_sets, seed_meta,
                len(seed_ids_raw), f'min_shared={min_shared}', skipped,
                'no usable references or API error', source, min_shared,
            )

        elif name == "co_citation":
            seed_ids_raw = arguments.get("seed_ids", [])
            min_shared = int(arguments.get("min_shared", 2))
            max_citing = int(arguments.get("max_citing_per_seed", 500))
            source = _source(arguments)

            if not seed_ids_raw:
                raise ValueError("seed_ids is required and must be non-empty")

            seed_sets = {}
            seed_meta = {}
            skipped = []

            if source == 'openalex':
                seed_sets, seed_meta, skipped = await _openalex_seed_sets(
                    seed_ids_raw, 'citing', max_citing=max_citing)
            else:
                for raw_id in seed_ids_raw:
                    sid = to_scopus_id(str(raw_id))
                    # Fetch seed metadata (one abstract call per seed)
                    try:
                        raw_meta = await client.get_abstract(sid)
                        details = clean_abstract_details(raw_meta)
                        authors = details.get('authors') or []
                        seed_meta[sid] = {
                            'title': details.get('title') or sid,
                            'creator': authors[0].get('name') if authors else None,
                            'year': (details.get('cover_date') or '')[:4] or None,
                            'venue': details.get('publication_name'),
                        }
                    except Exception as exc:
                        logger.warning(f"co_citation: metadata fetch failed for {sid}: {exc}")
                        seed_meta[sid] = {'title': sid, 'creator': None, 'year': None, 'venue': None}

                    # Fetch citing papers via search_all with REF() query
                    try:
                        raw_citers = await client.search_all(
                            f"REF({to_eid(sid)})", max_results=max_citing
                        )
                        citers = clean_search_results(raw_citers)
                        citer_ids: set = set()
                        for c in citers:
                            cid = c.get('scopus_id')
                            if cid and cid != sid:
                                citer_ids.add(cid)
                        if not citer_ids:
                            skipped.append(sid)
                            logger.info(f"co_citation: {sid} has no citing papers, skipping")
                            continue
                        seed_sets[sid] = citer_ids
                    except Exception as exc:
                        logger.warning(f"co_citation: citing fetch failed for {sid}: {exc}")
                        skipped.append(sid)

            return _network_response(
                'Co-citation', 'cocitation', seed_sets, seed_meta,
                len(seed_ids_raw),
                f'min_shared={min_shared}, max_citing_per_seed={max_citing}',
                skipped, 'no citing papers or API error', source, min_shared,
            )

        elif name == "find_journals":
            wanted = [str(c).strip() for c in (arguments.get("categories") or []) if str(c).strip()]
            if not wanted:
                raise ValueError("categories is required")
            min_pct = int(arguments.get("min_percentile", 75))
            if not 0 <= min_pct <= 99:
                raise ValueError("min_percentile must be between 0 and 99")
            journals_only = bool(arguments.get("journals_only", True))
            asjc = await client.asjc_categories()
            names = category_names(asjc)
            categories = resolve_categories(wanted, asjc)

            rows, per_category = [], []
            for cat in categories:
                entries = await client.journals_in_category(cat['code'])
                listed = kept = unranked = 0
                for e in entries:
                    if journals_only and (e.get('prism:aggregationType') or '').lower() != 'journal':
                        continue
                    listed += 1
                    ranks = subject_ranks(e, names)
                    here = next((r for r in ranks['ranks'] if r['code'] == cat['code']), None)
                    if here is None:
                        unranked += 1
                        continue
                    if here['percentile'] < min_pct:
                        continue
                    kept += 1
                    rows.append({
                        'title': e.get('dc:title'), 'publisher': e.get('dc:publisher'),
                        'issn': format_issn(normalize_issn(e.get('prism:issn'))),
                        'eissn': format_issn(normalize_issn(e.get('prism:eIssn'))),
                        'source_id': e.get('source-id'),
                        'category_code': cat['code'], 'category': cat['category'],
                        'percentile': here['percentile'], 'rank': here['rank'],
                        'quartile': here['quartile'],
                        'citescore': ranks['citescore'], 'citescore_year': ranks['year'],
                    })
                per_category.append({**cat, 'titles_listed': listed, 'without_rank': unranked,
                                     'at_or_above_cutoff': kept})
            rows.sort(key=lambda r: (r['category_code'], -r['percentile']))
            unique_ids = list(dict.fromkeys(
                r['source_id'] for r in sorted(rows, key=lambda r: -r['percentile']) if r['source_id']))
            result = {
                'min_percentile': min_pct,
                'categories': per_category,
                'unique_journals': len(unique_ids),
                'scopus_query_fragments': srcid_queries(unique_ids),
                'note': ("Percentile and quartile are within each category, from the latest "
                         "complete CiteScore year. Combine a fragment with a topic query, e.g. "
                         "TITLE-ABS-KEY(...) AND SRCID(...), in search_all."),
            }
            if rows:
                result['csv_path'] = str(_write_rows_csv(rows, f'journals-p{min_pct}'))
            if len(rows) <= 60:
                result['journals'] = rows
            else:
                result['journals_sample'] = rows[:20]
            return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

        elif name == "topic_landscape":
            query = (arguments.get("query") or "").strip()
            if not query:
                raise ValueError("query is required")
            from_year, to_year = arguments.get("from_year"), arguments.get("to_year")
            full_query = f"({query})"
            if from_year:
                full_query += f" AND PUBYEAR > {int(from_year) - 1}"
            if to_year:
                full_query += f" AND PUBYEAR < {int(to_year) + 1}"
            max_papers = max(1, min(int(arguments.get("max_papers", 500)), MAX_LANDSCAPE_PAPERS))
            top_n = max(1, int(arguments.get("top_categories", 15)))

            # 1. Broad subject areas over the whole result set (one request).
            facet_data = (await client.search_facets(full_query, 'subjarea(count=30)')).get('search-results') or {}
            total = int(facet_data.get('opensearch:totalResults') or 0)
            broad = []
            for f in (facet_data.get('facet') if isinstance(facet_data.get('facet'), list)
                      else [facet_data.get('facet')] if facet_data.get('facet') else []):
                cats = f.get('category')
                for c in (cats if isinstance(cats, list) else [cats] if cats else []):
                    broad.append({'area': (c.get('label') or c.get('name') or '').replace(' (all)', ''),
                                  'papers': int(c.get('hitCount') or 0)})

            # 2. The papers themselves, for per-category quartiles.
            raw = await client.search_all(full_query, max_results=max_papers, sort='coverDate')
            entries = (raw.get('search-results') or {}).get('entry') or []

            # 3. Their journals' per-category percentiles.
            venue = {}
            for e in entries:
                sid = e.get('source-id')
                if sid and sid not in venue:
                    venue[sid] = {'name': e.get('prism:publicationName'),
                                  'issns': [i for i in (normalize_issn(e.get('prism:issn')),
                                                        normalize_issn(e.get('prism:eIssn'))) if i]}
            names = category_names(await client.asjc_categories())
            ranks_by_sid = {}
            issns = sorted({i for v in venue.values() for i in v['issns']})
            for entry in (await client.serial_titles(issns) if issns else []):
                sid = str(entry.get('source-id') or '')
                if sid and sid not in ranks_by_sid:
                    ranks_by_sid[sid] = subject_ranks(entry, names)

            # 4. Count each paper under every category of its journal.
            from collections import Counter
            per_cat, unranked_venues = {}, Counter()
            unranked = 0
            for e in entries:
                sid = e.get('source-id')
                ranks = (ranks_by_sid.get(str(sid)) or {}).get('ranks') or []
                if not ranks:
                    unranked += 1
                    unranked_venues[e.get('prism:publicationName') or 'unknown'] += 1
                    continue
                for r in ranks:
                    c = per_cat.setdefault(r['code'], {
                        'code': r['code'], 'category': r['category'] or names.get(r['code']),
                        'papers': 0, 'Q1': 0, 'Q2': 0, 'Q3': 0, 'Q4': 0, '_journals': Counter()})
                    c['papers'] += 1
                    c[r['quartile']] += 1
                    c['_journals'][(venue.get(sid, {}).get('name'), r['quartile'], r['percentile'])] += 1

            categories = []
            for c in sorted(per_cat.values(), key=lambda c: -c['papers'])[:top_n]:
                journals = c.pop('_journals')
                c['q1_share'] = round(c['Q1'] / c['papers'], 2) if c['papers'] else None
                c['top_journals'] = [{'journal': j, 'quartile': q, 'percentile': p, 'papers': n}
                                     for (j, q, p), n in journals.most_common(3)]
                categories.append(c)

            sampled = len(entries)
            result = {
                'query': full_query,
                'total_papers': total,
                'analysed_papers': sampled,
                'coverage': ('complete' if sampled >= total else
                             f"most recent {sampled} of {total} ({round(100 * sampled / total)}%)"),
                'broad_areas_all_results': broad,
                'categories': categories,
                'unranked': {'papers': unranked,
                             'share': round(unranked / sampled, 2) if sampled else None,
                             'top_venues': [{'venue': v, 'papers': n}
                                            for v, n in unranked_venues.most_common(5)]},
                'notes': [
                    "Quartiles are per category from each journal's latest complete CiteScore "
                    "year (current standing, not standing at publication).",
                    "A paper counts once in every category of its journal, so category "
                    "totals add up to more than the papers analysed.",
                    "Unranked papers appeared in venues without CiteScore ranks, typically "
                    "conference proceedings, books or new journals.",
                ],
            }
            flat = [{'category_code': c['code'], 'category': c['category'], 'papers': c['papers'],
                     'Q1': c['Q1'], 'Q2': c['Q2'], 'Q3': c['Q3'], 'Q4': c['Q4'],
                     'q1_share': c['q1_share']} for c in categories]
            if flat:
                result['csv_path'] = str(_write_rows_csv(flat, 'landscape'))
            return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

        elif name == "search_fulltext":
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

        elif name == "search_authors":
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

        elif name == "get_journal_metrics":
            raw_issns = [str(i).strip() for i in (arguments.get("issns") or []) if str(i).strip()]
            source_ids = [str(i).strip() for i in (arguments.get("source_ids") or []) if str(i).strip()]
            source = _source(arguments)
            if not raw_issns and not source_ids:
                raise ValueError("Give issns and/or source_ids.")
            if len(raw_issns) + len(source_ids) > MAX_JOURNALS:
                raise ValueError(f"At most {MAX_JOURNALS} journals per call.")
            if source == 'openalex' and source_ids:
                raise ValueError("OpenAlex has no Scopus source IDs; pass ISSNs, "
                                 "or use source='scopus'.")

            rows, not_found = [], []
            issn_inputs = []
            for raw in raw_issns:
                compact = normalize_issn(raw)
                if compact:
                    issn_inputs.append((raw, compact))
                else:
                    not_found.append(f"{raw}: not a valid ISSN")

            if source == 'openalex':
                for raw, compact in issn_inputs:
                    src = await openalex.get_source_by_issn(format_issn(compact))
                    if src:
                        rows.append({'input': raw, **clean_openalex_source(src)})
                    else:
                        not_found.append(f"{raw}: not in OpenAlex")
                note = ("OpenAlex measures (2-year mean citedness, h-index, i10-index); "
                        "not comparable with SJR, SNIP or CiteScore.")
            else:
                srcid_issns = {}
                for sid in source_ids:
                    try:
                        found = await client.source_id_issns(sid)
                    except Exception as exc:
                        not_found.append(f"source_id {sid}: lookup failed ({exc})")
                        continue
                    if not found:
                        not_found.append(f"source_id {sid}: no Scopus records")
                        continue
                    srcid_issns[sid] = [c for c in (normalize_issn(found['issn']),
                                                    normalize_issn(found['eissn'])) if c]
                candidates = sorted({c for _, c in issn_inputs}
                                    | {c for cs in srcid_issns.values() for c in cs})
                entries = await client.serial_titles(candidates) if candidates else []
                by_issn, by_srcid = {}, {}
                for entry in entries:
                    cleaned = clean_serial_entry(entry)
                    for c in serial_entry_issns(entry):
                        by_issn.setdefault(c, cleaned)
                    if cleaned['source_id']:
                        by_srcid.setdefault(str(cleaned['source_id']), cleaned)

                for raw, compact in issn_inputs:
                    hit = by_issn.get(compact)
                    if hit:
                        rows.append({'input': raw, **hit})
                    else:
                        not_found.append(
                            f"{raw}: not in Serial Title under this ISSN. Scopus may "
                            f"list the journal under its other (print/electronic) ISSN; "
                            f"try that, or its source_id.")
                for sid, cands in srcid_issns.items():
                    hit = by_srcid.get(sid) or next(
                        (by_issn[c] for c in cands if c in by_issn), None)
                    if hit:
                        rows.append({'input': f"source_id {sid}", **hit})
                    else:
                        not_found.append(f"source_id {sid}: no Serial Title entry for "
                                         f"ISSNs {[format_issn(c) for c in cands]}")
                note = "Latest year of each metric from the Scopus Serial Title API."

            result = {'source': source, 'journals': rows, 'not_found': not_found, 'note': note}
            if rows:
                result['csv_path'] = str(_write_rows_csv(rows, f'journals-{source}'))
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_bibtex":
            identifiers = [str(i).strip() for i in (arguments.get("identifiers") or [])
                           if str(i).strip()]
            if not identifiers:
                raise ValueError("identifiers is required and must be non-empty")
            if len(identifiers) > MAX_BIBTEX:
                raise ValueError(f"At most {MAX_BIBTEX} identifiers per call.")

            targets = []
            failures = []
            for ident in identifiers:
                try:
                    targets.append((ident, *await _bibtex_target(ident)))
                except Exception as exc:
                    failures.append(f"{ident}: {exc}")

            dois = sorted({doi.lower() for _, doi, _, _ in targets if doi})
            fetched = await fetch_bibtex(dois) if dois else {}

            entries = []
            generated = 0
            seen_dois = set()
            for ident, doi, meta, origin in targets:
                if doi:
                    doi = doi.lower()
                    if doi in seen_dois:
                        continue  # same paper given twice
                    seen_dois.add(doi)
                    entry, err = fetched[doi]
                    if entry:
                        entries.append(entry)
                        continue
                    if not meta:
                        failures.append(f"{ident}: {err}")
                        continue
                entry = generated_entry(meta or {}, origin or 'available')
                if entry:
                    entries.append(entry)
                    generated += 1
                else:
                    failures.append(f"{ident}: no DOI and no title to build an entry from")

            entries = make_keys_unique(entries)
            text = f"{len(entries)} BibTeX entries"
            if generated:
                text += f" ({generated} generated from metadata; check them)"
            if entries:
                ts = _today_datetime.now().strftime('%Y%m%dT%H%M%S')
                path = _output_dir() / f'bibtex-{len(entries)}-{ts}.bib'
                path.write_text('\n\n'.join(entries) + '\n', encoding='utf-8')
                text += f". Written to: {path}\n"
            else:
                text += ".\n"
            if failures:
                text += "Not resolved:\n" + '\n'.join(f"  {f}" for f in failures) + "\n"
            if entries:
                shown = entries[:50]
                text += "\n" + '\n\n'.join(shown)
                if len(entries) > len(shown):
                    text += f"\n\n({len(entries) - len(shown)} more in the file.)"
            return [types.TextContent(type="text", text=text)]

        elif name == "publication_counts":
            query = arguments.get("query")
            if not query:
                raise ValueError("query is required")
            source = _source(arguments)
            from_year = arguments.get("from_year")
            to_year = arguments.get("to_year")
            from_year = int(from_year) if from_year is not None else None
            to_year = int(to_year) if to_year is not None else None
            if from_year is not None and to_year is not None and from_year > to_year:
                raise ValueError(f"from_year {from_year} is after to_year {to_year}")

            if source == 'openalex':
                counts, total = await openalex.yearly_counts(query, from_year, to_year)
                note = "OpenAlex title-and-abstract match."
            else:
                if from_year is None or to_year is None:
                    raise ValueError(
                        "Scopus publication_counts needs from_year and to_year "
                        "(one request per year); OpenAlex does not."
                    )
                if to_year - from_year + 1 > MAX_SCOPUS_YEARS:
                    raise ValueError(
                        f"Scopus publication_counts spans at most {MAX_SCOPUS_YEARS} "
                        f"years; split the range or use source='openalex'."
                    )
                counts = await client.yearly_counts(query, from_year, to_year)
                total = sum(counts.values())
                note = "Scopus search hits per PUBYEAR for the query as written."

            # Years with no records are absent from grouped results; show them
            # as zero within the requested range so gaps are visible.
            if counts or (from_year is not None and to_year is not None):
                lo = from_year if from_year is not None else min(counts)
                hi = to_year if to_year is not None else max(counts)
                counts = {y: counts.get(y, 0) for y in range(lo, hi + 1)}
            current_year = _today_date.today().year
            if counts and max(counts) >= current_year:
                note += f" {current_year} is incomplete."
            peak = max(counts, key=counts.get) if any(counts.values()) else None
            result = {
                'source': source,
                'query': query,
                'counts': {str(y): n for y, n in counts.items()},
                'total': total,
                'peak_year': peak,
                'note': note,
            }
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_server_info":
            return [types.TextContent(
                type="text",
                text=(
                    f"citation-network-mcp server\n"
                    f"version: {SERVER_VERSION}\n"
                    f"status: ok\n"
                ),
            )]

        elif name == "citation_lineage":
            seed_id_raw = arguments.get("seed_id")
            if not seed_id_raw:
                raise ValueError("seed_id is required")

            generations = min(int(arguments.get("generations", 1)), 3)
            max_per_node = int(arguments.get("max_per_node", 200))
            min_citing = int(arguments.get("min_citing", 0))
            direction = (arguments.get("direction") or "forward").lower()
            if direction not in ("forward", "backward"):
                raise ValueError("direction must be 'forward' or 'backward'")

            # sort controls ranking of citing papers before the max_per_node cap.
            # Default 'citedby' for forward walks ensures the backbone (high-citation
            # papers) is captured, not the current fringe.  Scopus API sort values:
            # 'citedby-count', 'coverDate', 'relevancy'.
            _SORT_MAP = {
                'citedby':   'citedby-count',
                'coverDate': 'coverDate',
                'relevancy': 'relevancy',
            }
            sort_arg = (arguments.get("sort") or ("citedby" if direction == "forward" else "coverDate"))
            if sort_arg not in _SORT_MAP:
                raise ValueError(f"sort must be one of {list(_SORT_MAP)}")
            api_sort = _SORT_MAP[sort_arg]
            source = _source(arguments)
            if source == 'openalex' and sort_arg == 'relevancy':
                raise ValueError(
                    "sort='relevancy' is Scopus-only; OpenAlex cannot rank citing "
                    "works by relevance. Use 'citedby' or 'coverDate'."
                )

            if source == 'openalex':
                try:
                    seed_work = await _resolve_openalex_work(seed_id_raw)
                except Exception as exc:
                    raise ValueError(
                        f"Could not resolve seed {seed_id_raw} in OpenAlex: {exc}"
                    ) from exc
                seed_rec = clean_openalex_work(seed_work)
                seed_id = seed_rec['openalex_id']
                seed_paper = {
                    'scopus_id': None,
                    'openalex_id': seed_id,
                    'doi': seed_rec['doi'],
                    'title': seed_rec['title'],
                    'year': seed_rec['year'],
                    'venue': seed_rec['publication_name'],
                    'generation': 0,
                    'parents': [],
                    'cited_by_count': seed_rec['cited_by_count'],
                }
            else:
                seed_id = to_scopus_id(str(seed_id_raw))

                # Fetch seed metadata (generation 0)
                try:
                    raw_meta = await client.get_abstract(seed_id)
                    details = clean_abstract_details(raw_meta)
                except Exception as exc:
                    raise ValueError(
                        f"Could not fetch seed metadata for {seed_id}: {exc}"
                    ) from exc

                seed_paper = {
                    'scopus_id': seed_id,
                    'doi': details.get('doi'),
                    'title': details.get('title'),
                    'year': (details.get('cover_date') or '')[:4] or None,
                    'venue': details.get('publication_name'),
                    'generation': 0,
                    'parents': [],
                    'cited_by_count': details.get('cited_by_count'),
                }

            seen: set = {seed_id}
            all_papers: dict = {seed_id: seed_paper}
            to_expand = [seed_id]
            api_calls = 1  # the get_abstract above
            gen_counts: dict = {0: 1}

            async def _fetch_next(parent_id: str) -> list:
                """Return a flat list of candidate-paper dicts for the next generation.

                Each dict carries: key (stable dedup id), scopus_id, doi, title,
                year, venue, cited_by_count.  The only difference between directions
                is which client method is called and how the raw result is mapped.
                """
                if source == 'openalex':
                    if direction == 'forward':
                        works, _ = await openalex.citing(
                            parent_id, max_results=max_per_node, sort=sort_arg)
                    else:
                        # Singleton lookups are free and cached; hydrating the
                        # reference list costs one request per 50 references.
                        parent_work = await openalex.get_work(parent_id)
                        works = (await openalex.references(parent_work, limit=max_per_node)
                                 if parent_work else [])
                    result = []
                    for w in works:
                        rec = clean_openalex_work(w)
                        if not rec['openalex_id']:
                            continue
                        result.append({
                            'key': rec['openalex_id'],
                            'scopus_id': None,
                            'openalex_id': rec['openalex_id'],
                            'doi': rec['doi'],
                            'title': rec['title'],
                            'year': rec['year'],
                            'venue': rec['publication_name'],
                            'cited_by_count': rec['cited_by_count'],
                        })
                    return result
                if direction == 'forward':
                    raw = await client.search_all(
                        f"REF({to_eid(parent_id)})",
                        max_results=max_per_node,
                        sort=api_sort,
                    )
                    return [
                        {
                            'key': p['scopus_id'],
                            'scopus_id': p['scopus_id'],
                            'doi': p.get('doi'),
                            'title': p.get('title'),
                            'year': (p.get('cover_date') or '')[:4] or None,
                            'venue': p.get('publication_name'),
                            'cited_by_count': p.get('cited_by_count') or '0',
                        }
                        for p in clean_search_results(raw)
                        if p.get('scopus_id')
                    ]
                else:  # backward — walk cited references
                    raw = await client.get_references(parent_id)
                    result = []
                    for r in clean_references(raw, limit=max_per_node):
                        sid = r.get('scopus_id')
                        doi = r.get('doi')
                        if not sid and not doi:
                            continue
                        # Prefer scopus_id as key; fall back to doi: prefix to avoid collisions
                        key = sid if sid else f'doi:{doi}'
                        result.append({
                            'key': key,
                            'scopus_id': sid,
                            'doi': doi,
                            'title': r.get('title'),
                            'year': r.get('year'),
                            'venue': r.get('source'),
                            'cited_by_count': None,
                        })
                    return result

            fetch_attempts = 0
            fetch_errors: list = []  # list of (parent_id, error_message)

            for gen in range(1, generations + 1):
                next_to_expand = []
                for parent_id in to_expand:
                    if not parent_id:
                        continue
                    fetch_attempts += 1
                    try:
                        papers = await _fetch_next(parent_id)
                        api_calls += 1
                    except Exception as exc:
                        err_msg = str(exc)
                        logger.warning(
                            f"citation_lineage ({direction}): fetch failed for "
                            f"{parent_id}: {err_msg}"
                        )
                        fetch_errors.append((parent_id, err_msg))
                        continue

                    for p in papers:
                        key = p['key']
                        if key in seen:
                            # Record an additional parent only when it is from the
                            # immediately preceding generation (parent_gen == child_gen - 1).
                            # Skipping same-generation parents prevents within-generation
                            # edges from entering the lineage DAG and derailing the SPC
                            # main-path (the backward-walk bug: a gen-1 paper's references
                            # can include other gen-1 papers, which would otherwise create
                            # gen1→gen1 edges that the SPC greedy walk traverses sideways).
                            if key in all_papers and parent_id not in all_papers[key]['parents']:
                                child_gen = all_papers[key]['generation']
                                parent_gen = all_papers.get(parent_id, {}).get('generation')
                                if parent_gen is not None and parent_gen == child_gen - 1:
                                    all_papers[key]['parents'].append(parent_id)
                            continue
                        seen.add(key)
                        cbc_str = p.get('cited_by_count')
                        try:
                            cbc = int(cbc_str or 0)
                        except (ValueError, TypeError):
                            cbc = 0
                        all_papers[key] = {
                            'scopus_id': p['scopus_id'],
                            'doi': p['doi'],
                            'title': p['title'],
                            'year': p['year'],
                            'venue': p['venue'],
                            'generation': gen,
                            'parents': [parent_id],
                            'cited_by_count': cbc_str,
                        }
                        if p.get('openalex_id'):
                            all_papers[key]['openalex_id'] = p['openalex_id']
                        gen_counts[gen] = gen_counts.get(gen, 0) + 1

                        # Expansion criteria for the next generation.
                        # Only expand papers the backend can fetch by ID.
                        # min_citing applies to forward only (backward refs lack cbc).
                        expand_id = p.get('openalex_id') or p['scopus_id']
                        if gen < generations and expand_id:
                            if direction == 'backward' or cbc >= min_citing:
                                next_to_expand.append(expand_id)

                to_expand = next_to_expand
                if not to_expand:
                    break

            records_list = list(all_papers.values())

            # Main-path analysis (SPC)
            mp_result = compute_main_path(records_list)
            main_path_ids = mp_result['main_path']
            spc_edges = mp_result['edges']

            # Consistent base filename for all three output files
            import base64
            import json as _json
            from datetime import datetime
            _ts = datetime.now().strftime('%Y%m%dT%H%M%S')
            _slug = _query_slug(
                f'lineage-openalex-{seed_id}' if source == 'openalex' else f'lineage-{seed_id}')
            base_fname = f'scopus-{_slug}-{_ts}'

            json_path = write_lineage_to_disk(
                records_list, seed_id,
                main_path=main_path_ids, spc_edges=spc_edges,
                base_filename=base_fname,
            )
            html_path = render_lineage_html(records_list, main_path_ids, seed_id, base_fname)
            png_path = render_lineage_png(records_list, main_path_ids, seed_id, base_fname)

            # Inline corpus as base64 so sandboxed callers can access it
            # without host filesystem access.
            _corpus_payload = {
                'seed_id': seed_id,
                'records': records_list,
                'main_path': main_path_ids,
                'spc_edges': spc_edges,
            }
            corpus_b64 = base64.b64encode(
                _json.dumps(_corpus_payload, ensure_ascii=False).encode('utf-8')
            ).decode('ascii')

            non_seed = [r for r in records_list if r['generation'] > 0]
            top10 = sorted(
                non_seed,
                key=lambda r: int(r.get('cited_by_count') or 0),
                reverse=True,
            )[:10]

            gen_summary = ', '.join(
                f"gen {g}: {c}"
                for g, c in sorted(gen_counts.items())
                if g > 0
            )

            # Main-path labels: first-author surname + year if available
            mp_labels = []
            for nid in main_path_ids:
                rec = all_papers.get(nid, {})
                mp_labels.append(_make_node_label(rec, nid))

            # ── Degeneracy guard ────────────────────────────────────────────
            # Detect and report a recency-dominated or collapsed walk so the
            # caller is never silently misled.
            _gen1_count  = gen_counts.get(1, 0)
            _gen2_count  = gen_counts.get(2, 0)
            _gen1_capped = (generations >= 2 and _gen1_count >= max_per_node)
            _path_short  = len(main_path_ids) < 3
            # "Recency fringe" heuristic: if ALL of the top-5 cited papers in
            # gen-1 are from the last 2 years and have < 20 citations each.
            import datetime as _dt
            _current_year = _dt.datetime.now().year
            _gen1_papers  = [r for r in non_seed if r['generation'] == 1]
            _top5_gen1    = sorted(
                _gen1_papers,
                key=lambda r: int(r.get('cited_by_count') or 0),
                reverse=True,
            )[:5]
            _recency_fringe = bool(
                _top5_gen1
                and all(
                    int(r.get('year') or 0) >= _current_year - 1
                    and int(r.get('cited_by_count') or 0) < 20
                    for r in _top5_gen1
                )
            )
            _degenerate = (
                direction == 'forward'
                and (
                    (_gen1_capped and _gen2_count == 0)
                    or _path_short
                    or _recency_fringe
                )
            )

            # SPC completeness note
            _spc_complete = bool(spc_edges)

            all_fetches_failed = (
                fetch_attempts > 0
                and len(fetch_errors) == fetch_attempts
            )
            some_fetches_failed = fetch_errors and not all_fetches_failed

            if all_fetches_failed:
                # Don't report "no papers found" — report the real cause.
                first_err = fetch_errors[0][1]
                text = (
                    f"Citation lineage ({direction}) for {seed_id} "
                    f"({seed_paper.get('title', seed_id)!r}).\n"
                    f"Walk FAILED: all {len(fetch_errors)} node fetch(es) returned API errors.\n"
                    f"API calls: {api_calls}.\n"
                    f"Server version: {SERVER_VERSION}\n"
                    f"Error: {first_err}\n"
                )
                if len(fetch_errors) > 1:
                    text += f"(and {len(fetch_errors) - 1} more failures)\n"
            else:
                text = (
                    f"Citation lineage ({direction}) for {seed_id} "
                    f"({seed_paper.get('title', seed_id)!r}).\n"
                    f"Generations walked: {generations}. "
                    f"Papers per generation: {gen_summary or 'none (no papers found)'}.\n"
                    f"Total unique papers (excl. seed): {len(non_seed)}. "
                    f"API calls: {api_calls}. Sort: {sort_arg}.\n"
                    f"Server version: {SERVER_VERSION}\n"
                    f"Corpus written to: {json_path}\n"
                    f"Corpus (base64, UTF-8 JSON): {corpus_b64}\n"
                )
                if source == 'openalex':
                    text += "Source: OpenAlex (node IDs are OpenAlex work IDs).\n"
                if not _spc_complete:
                    text += (
                        "SPC arc weights: NOT computed (no edges in lineage graph; "
                        "main path is unweighted).\n"
                    )
                if html_path:
                    text += f"Interactive HTML: {html_path}\n"
                if png_path:
                    text += f"PNG: {png_path}\n"
                if main_path_ids:
                    text += (
                        f"Main path ({len(main_path_ids)} nodes): "
                        + " → ".join(mp_labels) + "\n"
                    )
                elif mp_result.get('note'):
                    text += f"Main path: {mp_result['note']}\n"

                if _degenerate:
                    _reasons = []
                    if _gen1_capped and _gen2_count == 0:
                        _reasons.append(
                            f"gen-1 hit the max_per_node cap ({max_per_node}) "
                            f"and gen-2 did not expand"
                        )
                    if _path_short:
                        _reasons.append(
                            f"main_path has only {len(main_path_ids)} node(s)"
                        )
                    if _recency_fringe:
                        _reasons.append(
                            "top gen-1 papers are all recent (≤2 years old) "
                            "with low citation counts"
                        )
                    text += (
                        f"\nWARNING: forward walk appears recency-dominated or collapsed "
                        f"({'; '.join(_reasons)}). "
                        f"Treat the path as a recent-citer sample, not a citation backbone. "
                        f"Consider sort='citedby' or a higher min_citing threshold.\n"
                    )

                text += "\nTop 10 most-cited papers in lineage:\n"
                for r in top10:
                    text += (
                        f"  [gen {r['generation']}] {r.get('title', r['scopus_id'])!r} "
                        f"({r.get('year', '?')}, {r.get('venue', '?')}) "
                        f"— {r.get('cited_by_count', '?')} citations\n"
                    )
                if not top10:
                    text += "  (no papers found)\n"

            if some_fetches_failed:
                text += (
                    f"\nWarning: {len(fetch_errors)} node fetch(es) failed with API errors "
                    f"(partial walk — results may be incomplete). "
                    f"First error: {fetch_errors[0][1]}\n"
                )

            return [types.TextContent(type="text", text=text)]

        elif name == "get_fulltext":
            doi = (arguments.get("doi") or "").strip()
            if not doi:
                raise ValueError("doi is required")
            prefer = arguments.get("prefer")  # None → full waterfall

            text_body: Optional[str] = None
            provenance: str = "none"
            source_url: Optional[str] = None

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
                if oa_result.get('text'):
                    text_body = oa_result['text']
                    provenance = "oa-fulltext"

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
            if file_path:
                summary['file_path'] = file_path
            summary['sample'] = sample

            import json as _json
            return [types.TextContent(type="text", text=_json.dumps(summary, ensure_ascii=False, indent=2))]

        elif name == "diagnose_connection":
            report = await client.diagnose_connection()
            import json as _json
            return [types.TextContent(type="text", text=_json.dumps(report, indent=2))]

        elif name == "get_quota_status":
            quota = await client.get_quota_status()
            if not quota:
                return [types.TextContent(type="text", text="No quota information available yet. Please make a request to initialize.")]
            
            return [types.TextContent(type="text", text=str(quota))]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Error executing tool {name}: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]

@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="research-summary",
            description="Search for papers on a topic and generate a research summary",
            arguments=[
                types.PromptArgument(
                    name="topic",
                    description="The research topic (e.g., 'machine learning healthcare')",
                    required=True
                )
            ]
        ),
        types.Prompt(
            name="author-analysis",
            description="Analyze an author's research impact and recent work",
            arguments=[
                types.PromptArgument(
                    name="author_id",
                    description="The Scopus Author ID",
                    required=True
                )
            ]
        )
    ]

@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    if not arguments:
        arguments = {}

    if name == "research-summary":
        topic = arguments.get("topic", "unknown topic")
        return types.GetPromptResult(
            description=f"Research summary for {topic}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Please search specifically for high-cited papers related to '{topic}' published in the last 5 years using the search_scopus tool. Sort by cited references if possible. After retrieving the results, please summarize the key trends and findings in this field."
                    )
                )
            ]
        )

    if name == "author-analysis":
        author_id = arguments.get("author_id", "")
        return types.GetPromptResult(
            description=f"Analysis of author {author_id}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Please call the get_author_profile tool for Author ID '{author_id}'. Based on the returned data, analyze their research impact (citations, h-index if available), identify their main affiliation, and summarize their academic standing."
                    )
                )
            ]
        )

    raise ValueError(f"Unknown prompt: {name}")

async def main():
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options()
            )
    finally:
        # Ensure client is closed on shutdown
        await client.close()

def start():
    """Entry point for the package script."""
    asyncio.run(main())

if __name__ == "__main__":
    start()
