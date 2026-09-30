# Tool reference

Generated from the server's tool definitions by `scripts/gen_tools_doc.py`;
do not edit by hand. Tools marked **OpenAlex** accept `source="openalex"`
and then need no Scopus subscription (see [data sources](data-sources.md)).

## Search and records

### `search_scopus`

Search for documents in Scopus using a query string.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `query` | string | required | The Scopus search query (e.g., 'TITLE(AI) AND PUBYEAR > 2020'). |
| `count` | integer | 5 | Number of results to return (default 5, max 25). |
| `sort` | string | coverDate | Sort order (e.g., 'coverDate', 'relevancy'). |

### `search_all` · **OpenAlex**

Search Scopus (or OpenAlex with source='openalex') and page through results automatically, returning up to max_results entries in one call. Scopus pages hold SCOPUS_PAGE_SIZE records (default 25) and switch to cursor paging beyond 5,000; OpenAlex pages hold 200. Results over 50 records are written to disk as JSON and CSV. Large max_results values consume significant quota — use conservatively.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `query` | string | required | Search query. Scopus: Scopus syntax (e.g., 'TITLE(AI) AND PUBYEAR > 2020'). OpenAlex: plain words matched against title and abstract; quote phrases (e.g., '"organizing vision"'). |
| `max_results` | integer | 200 | Maximum total results to fetch across all pages (default 200). Large values consume quota. |
| `sort` | string |  | Sort order (e.g., 'coverDate', 'relevancy', 'citedby'). Defaults to 'coverDate' for Scopus, 'relevance' for OpenAlex. |

### `search_fulltext`

Search the full text of Elsevier (ScienceDirect) journal articles, not just titles and abstracts: finds papers that use a construct in their body without naming it up front. Needs Scopus/ScienceDirect subscriber access; covers Elsevier-published content only. With context=true, the top results' full texts are retrieved to count mentions in the body (separately from the reference list), give their positions through the article, and quote example sentences: how a paper uses the construct, not just that it does. Up to 1000 results; over 50 are written to JSON and CSV.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `query` | string | required | ScienceDirect query; quote phrases, e.g. '"organizing vision"'. AND, OR, NOT allowed. |
| `journal` | string |  | Restrict to a journal title, e.g. 'Information and Organization'. |
| `from_year` | integer |  | First publication year. |
| `to_year` | integer |  | Last publication year. |
| `open_access_only` | boolean | False |  |
| `max_results` | integer | 100 | Results to fetch (default 100, max 1000). |
| `sort` | `relevance` \| `date` | relevance |  |
| `context` | boolean | False | Analyse mentions in the top results' full texts. |
| `max_context` | integer | 10 | Articles to analyse when context=true (default 10, max 25); one full-text request each. |

### `get_abstract_details`

Retrieve full details for a specific document by Scopus ID.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `scopus_id` | string | required | The Scopus ID of the document. |

### `resolve_identifier`

Resolve any document identifier (Scopus ID, EID, DOI, or PII) to the full cross-reference set (scopus_id, eid, doi, pii, title). Use this to obtain a DOI for cross-linking with OpenAlex/Crossref, or to normalize an ID before calling other tools.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `identifier` | string | required | The identifier value (e.g. '0031512927', '2-s2.0-0031512927', or a DOI). |
| `id_type` | `scopus_id` \| `eid` \| `doi` \| `pii` |  | Optional override of the identifier type. |

### `search_authors` · **OpenAlex**

Find authors by name, optionally narrowed by affiliation. Scopus (default, needs subscriber entitlement): author IDs for get_author_profile, document counts, current affiliation, subject areas and name variants, ranked by document count. OpenAlex: OpenAlex author IDs, ORCID, works and citation counts, h-index, institution and topics. Common surnames need an affiliation or given name to be useful.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string | required | 'Surname, Given names' or 'Given names Surname', e.g. 'Swanson, E. Burton'. |
| `affiliation` | string |  | Optional affiliation words to narrow the match, e.g. 'Los Angeles'. |
| `count` | integer | 10 | Number of authors to return (default 10, max 25). |

### `get_author_profile`

Retrieve an author's profile by Author ID.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `author_id` | string | required | The Scopus Author ID. |

### `get_fulltext`

Retrieve the full text of a paper via a provider waterfall: (1) ScienceDirect full text (requires SCOPUS_INSTTOKEN or institutional IP), (2) open-access copy: every open location in OpenAlex, Semantic Scholar's open PDF and arXiv ID, arXiv by exact title, Europe PMC, and Unpaywall or CORE when configured; published versions first, and the result names the source and version (preprint, accepted manuscript, published), (3) Scopus abstract fallback. Returns provenance, character count, file path, and a ~1500-char sample. Full body is written to disk — never returned inline. ToS note: retrieval is for the user's own non-commercial text-and-data-mining; content written to local disk must not be redistributed.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `doi` | string | required | The DOI of the paper (e.g. '10.1016/j.infoandorg.2026.100608'). |
| `prefer` | `sciencedirect` \| `oa` \| `abstract` |  | Skip straight to a tier for testing: 'sciencedirect', 'oa', 'abstract'. |

## Citations

### `get_references` · **OpenAlex**

Retrieve the cited-reference list of a document (Backward Citations) via the Abstract Retrieval REF view. Complements get_citing_papers, which returns forward citations. Scopus requires an entitled (subscriber) key; source='openalex' does not.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `scopus_id` | string | required | The Scopus ID (or EID) of the document whose references to retrieve. With source='openalex', a DOI or OpenAlex work ID also works. |
| `count` | integer | 25 | Maximum number of references to return (default 25). |

### `get_citing_papers` · **OpenAlex**

Retrieve a list of papers that have cited the specified document (Forward Citations).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `scopus_id` | string | required | The Scopus ID of the document to find citations for. With source='openalex', a DOI or OpenAlex work ID also works. |
| `count` | integer | 5 | Number of results to return (default 5, max 25). |
| `sort` | string | coverDate | Sort order (e.g., 'coverDate', 'relevancy'). |

## Networks and lineage

### `bibliographic_coupling` · **OpenAlex**

Build a bibliographic-coupling graph for a set of seed papers. Two seeds are coupled when they share cited references; edge weight = count of shared references, cosine = Salton index. Maps the current research front. Scopus needs an entitled (subscriber) key for REF-view access; source='openalex' does not. Output: GraphML + CSV edge list written to disk.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `seed_ids` | list of string | required | Seed papers: Scopus IDs (bare numeric or SCOPUS_ID: prefixed). With source='openalex', DOIs and OpenAlex work IDs also work. |
| `min_shared` | integer | 2 | Minimum shared references for an edge to be emitted (default 2). |

### `co_citation` · **OpenAlex**

Build a co-citation graph for a set of seed papers. Two seeds are co-cited when a later paper cites both; edge weight = count of co-citing papers, cosine = Salton index. Maps the intellectual base of a field. max_citing_per_seed bounds the API quota used per seed. Output: GraphML + CSV edge list written to disk.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `seed_ids` | list of string | required | Seed papers: Scopus IDs (bare numeric or SCOPUS_ID: prefixed). With source='openalex', DOIs and OpenAlex work IDs also work. |
| `min_shared` | integer | 2 | Minimum co-citing papers for an edge to be emitted (default 2). |
| `max_citing_per_seed` | integer | 500 | Cap on citing papers fetched per seed (default 500). Limits quota usage. |

### `citation_lineage` · **OpenAlex**

Walk the citation lineage of a seed paper across multiple generations. Forward: generation 1 = papers that cite the seed; generation 2 = papers that cite those; up to 3 generations. Backward: walks cited references. All papers are deduplicated globally. Output: corpus written to disk as JSON plus compact inline summary; the corpus is also returned inline as base64 so sandboxed callers can inspect it. Use sort='citedby' (default for forward) to collect the most-cited citers first, which gives a meaningful citation-backbone; sort='coverDate' collects the most recent citers first (which can produce a recency-dominated walk). source='openalex' walks OpenAlex instead (no Scopus entitlement; node IDs are OpenAlex work IDs; reference lists are thinner and absent for AIS eLibrary papers). Server version is included in every response.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `seed_id` | string | required | Scopus ID or EID of the seed paper. With source='openalex', a DOI or OpenAlex work ID also works. |
| `generations` | integer | 1 | Number of generations to walk (default 1, max 3). |
| `max_per_node` | integer | 200 | Cap on citing papers fetched per paper per generation (default 200). |
| `min_citing` | integer | 0 | Only expand papers that have at least this many citing papers (default 0 = expand all up to max_per_node). Pruning high values avoids exploding on trivially-cited nodes. Ignored for backward direction. |
| `direction` | `forward` \| `backward` | forward | 'forward' (default): walk citing papers via search_all + REF(). Fan-out can be large; use max_per_node to bound quota. 'backward': walk cited references via get_references, up to max_per_node per paper; references with no ID are skipped. |
| `sort` | `citedby` \| `coverDate` \| `relevancy` | citedby | How to rank citing papers before the max_per_node cap is applied (forward direction only; ignored for backward). 'citedby' (default): highest citation count first — captures the high-flow backbone. 'coverDate': most recent first — captures the current fringe but may produce a recency-dominated walk on high-citation seeds. 'relevancy': Scopus relevance score (Scopus only). |

## Bibliometrics and bibliography

### `publication_counts` · **OpenAlex**

Count publications per year for a query, e.g. to chart how attention to a topic rose and fell. Scopus: your query in Scopus syntax, one request per year, so from_year and to_year are required (at most 60 years). OpenAlex: plain words matched against title and abstract (quote phrases), one request for all years. The two sources count differently; compare trends within one source, not levels across sources.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `query` | string | required | Scopus: Scopus syntax, e.g. 'TITLE-ABS-KEY("organizing vision")'. OpenAlex: e.g. '"organizing vision"'. |
| `from_year` | integer |  | First year (inclusive). |
| `to_year` | integer |  | Last year (inclusive). |

### `topic_landscape`

Where and at what prestige a topic is published. Runs a Scopus query and reports (1) papers per broad subject area over all results, and (2) per subject category, how many papers appear in Q1, Q2, Q3 and Q4 journals of that category, with the main journals. A journal can be Q1 in one category and Q3 in another, so each paper counts in every category of its journal. By default quartiles count journal papers only: proceedings series such as IFAC-PapersOnLine or Procedia CIRP also carry CiteScore ranks, and are reported separately with book series, together with the overall mix of venue types. Large topics are analysed on a sample of max_papers papers (up to 2000): most recent by default, or most cited to see where influential work appears; coverage is stated. Needs Scopus search entitlement.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `query` | string | required | Scopus query, e.g. 'TITLE-ABS-KEY("organizing vision")'. |
| `from_year` | integer |  | First publication year. |
| `to_year` | integer |  | Last publication year. |
| `max_papers` | integer | 500 | Papers to analyse by quartile (default 500, max 2000). |
| `top_categories` | integer | 15 | Categories to report, largest first. |
| `sample` | `recent` \| `cited` \| `relevance` | recent | Which papers to analyse when the topic has more than max_papers: most recent, most cited (where influential work appears), or most relevant. |
| `journals_only` | boolean | True | Count only journal papers in the quartiles; ranked conference proceedings and book series are reported separately. False counts every ranked venue. |

### `get_journal_metrics` · **OpenAlex**

Journal metrics for a list of journals, e.g. a litbaskets basket. Scopus (default): SJR, SNIP, CiteScore and CiteScore Tracker with their years, plus subject areas, from the Serial Title API. Give ISSNs, or Scopus source IDs (SRCIDs), which are mapped to ISSNs through one Scopus search each (needs search entitlement). source='openalex': OpenAlex's own measures (2-year mean citedness, h-index, i10-index), ISSNs only, no entitlement. Journals not found are listed, never dropped. Also written to CSV. At most 200 journals per call.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `issns` | list of string |  | ISSNs, with or without hyphen. |
| `source_ids` | list of string |  | Scopus source IDs (SRCID), Scopus only. |

### `find_journals`

List the journals in one or more Scopus subject categories at or above a CiteScore percentile within that category: the quality cut-off for scoping a literature review (Q1 = 75, top 10% = 90). Categories are ASJC names or codes, e.g. 'Information Systems' (1710), 'Management Information Systems' (1404); ambiguous names return the candidates. Returns each journal's rank, percentile, quartile and CiteScore, a CSV, and ready-to-use Scopus query fragments SRCID(...) for search_all. Percentiles are from the latest complete CiteScore year.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `categories` | list of string | required | ASJC category names or 4-digit codes. |
| `min_percentile` | integer | 75 | Keep journals at or above this percentile in the category (75 = Q1, 90 = top 10%). |
| `journals_only` | boolean | True | Exclude book series, conference proceedings and trade journals. |

### `get_bibtex`

BibTeX entries for a list of papers, written to a .bib file and returned inline. Identifiers may be DOIs, Scopus IDs/EIDs or OpenAlex work IDs. Entries come from the publisher's metadata via DOI content negotiation (errors included, so check author names). Papers without a DOI, such as AIS conference papers, get a minimal entry built from Scopus or OpenAlex metadata, marked with a note. At most 200 identifiers per call.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `identifiers` | list of string | required | DOIs, Scopus IDs/EIDs, or OpenAlex work IDs (W...). |

## Diagnostics

### `diagnose_connection`

Diagnose Scopus connectivity and entitlement. Checks config presence, api.elsevier.com reachability, metadata and search entitlement, and per-API capabilities (REF-view references, ScienceDirect full text, Serial Title journal metrics). Returns a JSON report with a one-line verdict and 'unavailable_tools', the tools that cannot work with the current access. Run this first when Scopus behaves strangely — especially when valid searches fail with 'Error translating query', which usually means missing subscriber entitlement (off-network without SCOPUS_INSTTOKEN), not bad query syntax.

### `get_quota_status`

Get the current API quota status (remaining/limit). Note: Values are updated only after making a request.

### `get_server_info`

Return the server version and a health summary. Call this to confirm which build you are talking to.
