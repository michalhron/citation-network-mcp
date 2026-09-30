# Prompt examples

Plain requests work; the assistant picks the tools. Each example lists the
tools it typically triggers. Add "use OpenAlex" to any of them when you have
no Scopus subscription.

## Find literature

> Find the 20 most-cited papers on "organizing vision" since 2015.

`search_all` with a Scopus query such as `TITLE-ABS-KEY("organizing vision") AND PUBYEAR > 2014`, sorted by citations.

> Get everything Scopus has on digital platform ecosystems in the Basket of Eight journals.

`search_all` restricted by `SRCID(...)`; results over 50 records are written to JSON and CSV.

> Who is E. Burton Swanson on Scopus, and what has he published recently?

`search_authors`, then `get_author_profile` with the author ID it returns.

## Follow citations

> What does Swanson and Ramiller (1997) cite, and who cites it most?

`resolve_identifier` for the DOI, then `get_references` and `get_citing_papers`.

> Trace two generations of work building on Swanson and Ramiller (1997) and show me the main path.

`citation_lineage` with `generations=2`: a JSON corpus, an interactive HTML graph and a PNG, with the search-path-count main path named in the reply.

> Walk the reference lineage backward from this 2025 paper: which older work does it inherit from?

`citation_lineage` with `direction="backward"`.

## Map a field

> Build a bibliographic-coupling network for these twelve papers to see the current research fronts.

`bibliographic_coupling`: seeds linked by shared references, as GraphML for VOSviewer or Gephi.

> Which of these classics are cited together most often?

`co_citation`: seeds linked by later papers that cite both, showing the field's intellectual base.

## Measure and cite

> How has publishing on "digital transformation" grown each year since 2010?

`publication_counts`, one source at a time: Scopus and OpenAlex count differently, so compare their trends, not their totals.

> Give me SJR and CiteScore for the Basket of Eight.

`get_journal_metrics` with ISSNs or Scopus source IDs, also saved as CSV.

> Make a BibTeX file for the papers we found.

`get_bibtex`: publisher metadata for papers with DOIs; marked, generated entries for the rest.

> Get the full text of this Elsevier paper.

`get_fulltext`: ScienceDirect when your subscription allows it, otherwise an open-access copy, otherwise the abstract, always stating which.

## When something fails

> Why are my Scopus searches failing?

`diagnose_connection`: which APIs your current access entitles, which tools will fail, and why. See [access and troubleshooting](access.md).
