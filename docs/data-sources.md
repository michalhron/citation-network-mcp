# Data sources: Scopus and OpenAlex

Every search, citation, reference, network and count tool runs on either
source. Scopus is the default; pass `source="openalex"` for the other.

| | Scopus | OpenAlex |
| --- | --- | --- |
| Access | Your institution's Scopus subscription for search, citations, references and full text; metadata by ID works on a plain API key | Free and open; no subscription |
| Search | Full Scopus syntax (`TITLE-ABS-KEY(...)`, `PUBYEAR`, `SRCID`, ...) | Plain words matched against title and abstract; quote phrases |
| Reference lists | Complete for indexed papers | Thinner; none for AIS eLibrary papers |
| IDs in results | Scopus IDs | OpenAlex work IDs (`W…`) |
| Cost | Your Scopus API quota | Daily budget: $0.10 anonymous, $1 with a free key |

## Rules

- **Never mix sources within one analysis.** Node and record IDs differ, and
  so do the citation graphs: from the same seed, the two sources pick
  different top citers and find different main paths.
- **Compare trends within a source, not levels across sources.**
  `publication_counts` for "organizing vision", 2015–2026: Scopus 61,
  OpenAlex 77, with different year-to-year shapes.

## Identifiers with `source="openalex"`

Seeds and IDs may be DOIs, OpenAlex work IDs or Scopus IDs. A Scopus ID is
resolved to a DOI through Scopus metadata, which a plain API key can read.
Records without a DOI, common for AIS conference papers, are matched by exact
title (ignoring case and punctuation) within one year; a fuzzy match is never
accepted, because a wrong seed silently corrupts a network.

## Coverage, measured

Checked live on 2026-09-30:

| Case | Scopus | OpenAlex |
| --- | --- | --- |
| References of Swanson et al. (2025), *Journal of Information Technology* | 171 | 132 |
| Coupling on four organizing-vision seeds (three AIS conference papers) | 4/4 seeds, 6 edges | 1/4 seeds: two AIS papers have no reference lists, one has no exact title match |
| Forward lineage of Swanson & Ramiller (1997), 2 generations, 20 per node | 378 papers | 348 papers |

For information-systems research built on AIS conference literature (ICIS,
AMCIS, ECIS, PACIS), use Scopus for anything that needs reference lists.
Journal-based analyses work on both.

## OpenAlex budget

OpenAlex bills per request: single-record lookups are free, list pages cost 1
credit, searches 10. Without a key the daily allowance is $0.10 (about 1,000
list requests); a free account key (`OPENALEX_API_KEY`) raises it to $1.
When it runs out, errors say how much is left and when it resets.
