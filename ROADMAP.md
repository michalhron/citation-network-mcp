# Scopus MCP — Roadmap

Single living plan for the `michalhron/scopus-mcp` fork. Replaces the
2026-06-24 roadmap and the 2026-06-25 addendum; their research-design content
is kept verbatim under "Research design" below.

_Last updated: 2026-09-30._

## Status

- Version 0.8.1 on `feat/offnetwork-auth` (PR #2), awaiting merge into `main`;
  Phase 1 work continues on `feat/roadmap-phase1`, stacked on it.
  Claude Desktop is pinned to that branch's tip. See CHANGELOG.md.
- 14 tools. Scopus: search, `search_all`, abstracts, author profiles,
  identifiers, references, citing papers, full text. Networks:
  `bibliographic_coupling`, `co_citation`, `citation_lineage` with SPC main path.
  Ops: `diagnose_connection`, quota, server info.
- Offline suite of about 270 tests; CI on Ubuntu, macOS and Windows.
- Upstream (qwe4559999/scopus-mcp) inactive since 2026-05-17; PRs #11–#14
  unanswered. This fork is maintained independently.

## Deadline that shapes everything: institutional access ends ~end Nov 2026

Michal leaves the institution around the end of November 2026. Every route to
Scopus subscriber entitlement goes with the affiliation: campus IP, VPN or
SSH proxy (`SCOPUS_PROXY`), and an insttoken. **Decision (2026-09-30): no
insttoken request through the library.** The June request to
apisupport@elsevier.com is abandoned.

After departure, search, citing papers, references, coupling, co-citation and
lineage stop working on Scopus. Only ID-based metadata keeps working, and only
if the API key itself survives the affiliation. Two consequences:

1. Harvest the research corpora while entitled (Phase 1, item 2).
2. OpenAlex becomes the long-term backend (Phase 1, item 3). It also removes
   the subscription barrier that keeps this server's audience small.

## Phase 0 — Close out

- [x] Off-network entitlement: OS secret store, proxy, diagnostics (0.8.1).
- [x] Cross-OS CI.
- [x] CHANGELOG.md; this consolidated roadmap.
- [ ] Merge PR #2 into `main` (Michal; Claude may not merge), then repin
      Claude Desktop to `main`.
- [ ] Optional: comment on upstream PRs asking about co-maintainership.

## Phase 1 — Features, ordered by the access deadline

Every new tool gets a live smoke test before it is trusted (see Lessons).

1. [x] **Per-API access check.** `diagnose_connection` probes REF view,
       ScienceDirect full text and Serial Title, and lists unavailable tools.
       Live on campus 2026-09-30: all three entitled (REF returned 109 refs;
       subscription full text 53,017 chars). So the June REF refusal was not a
       permanent key limit; most likely it happened off the campus network.
2. [ ] **Harvest before end of November.** Run the lineage, reference and
       full-text pulls that "What Inherits?" and "Hype Without a Cycle" need,
       to disk via the file-output contract. Data work, not code. More urgent
       than planned: OpenAlex cannot replace Scopus for AIS conference papers
       (see item 3), and coupling networks built before the 2026-09-30
       reference-paging fix used at most 40 references per seed, so re-run
       them.
3. [x] **OpenAlex backend.** `source="openalex"` on search, citing papers,
       references, coupling and co-citation; results carry OpenAlex IDs and
       sources are never mixed. Live 2026-09-30 on four organizing-vision
       seeds: Scopus coupling 4/4 seeds, 6 edges; OpenAlex 1/4. Two AIS
       eLibrary papers are in OpenAlex with no reference lists (and
       misattributed to JAIS), one ICIS paper has no exact title match.
       Journal articles work, but thinner (Swanson 2025: 98 references in
       OpenAlex vs 171 in Scopus). Citation-lineage support still to come.
4. [ ] **Yearly publication counts** for a query (OpenAlex `group_by`, one
       request; Scopus per-year `totalResults` while entitled).
5. [ ] **Journal metrics** (SJR, SNIP, CiteScore) via the Serial Title API,
       for litbaskets baskets.
6. [ ] **BibTeX export** via Crossref content negotiation.
- Dropped: Scopus author search. Needs subscriber entitlement that ends in
  November; OpenAlex author data replaces it.

## Phase 2 — Public release (only if outside users are wanted)

- [ ] Choose a package name; `scopus-mcp` on PyPI belongs to upstream. Update
      authors, URLs and `mcp-name`; keep MIT and credit upstream.
- [ ] Fix `publish.yml` first: it publishes to PyPI on any `v*` tag, under
      upstream's name. Until then, never push version tags.
- [ ] Split `server.py` and `utils.py` (1,200+ lines each) into tool modules.
- [ ] README around the differentiator (citation-network analysis, works
      without a Scopus subscription via OpenAlex); update or drop README_CN.
- [ ] Publish: PyPI, MCP registry (`server.json`), Claude Desktop bundle.

## Phase 3 — Research pipeline

Orchestrator skill (Session D below) and the lineage-model ideas. Mostly
skill-side, so independent of distribution. Plan it on the harvested corpora
and OpenAlex, since Scopus REF view and full text end with the affiliation.

## Release process

Bump `pyproject.toml`, `SERVER_VERSION` and the User-Agent strings together,
add a CHANGELOG entry, merge to `main`, repin Claude Desktop to the merge
commit. No tags until Phase 2 fixes `publish.yml`.

---

# Research design

Carried over verbatim from the 2026-06-24 roadmap and 2026-06-25 addendum.
Statements about access below predate the 2026-09-30 decision above.

### Known boundaries (facts, not bugs)

- REF-VIEW ENTITLEMENT WALL. The current API key (individual entitlement) is
  refused on Abstract Retrieval view=REF: X-ELS-Status=AUTHORIZATION_ERROR with
  X-RateLimit-Remaining=9983 (NOT quota exhaustion; key works for all non-REF
  endpoints). This blocks get_references, bibliographic_coupling, and BACKWARD
  citation_lineage. Forward lineage (via search_all citing queries) is unaffected
  and is the usable capability now.
  UNRESOLVED CONTRADICTION: get_references worked earlier on 2026-06-24 (Swanson
  85216793920 returned 40 refs; coupling processed 13/14 seeds), then refused
  later the same session. Either intermittent, or a per-document/rate dimension.
  RETEST one clean get_references on 85216793920 when the API is fresh: 40 refs
  (transient blip) vs AUTHORIZATION_ERROR (truly token-gated).

- INSTTOKEN IS THE SINGLE UNLOCK. The institutional token (requested from Elsevier
  2026-06-24, apisupport@elsevier.com, from UGent address, with the API key)
  unlocks BOTH REF view AND ScienceDirect full text — they are coupled. When it
  arrives: add "SCOPUS_INSTTOKEN":"..." to the config env block, repin, no code
  change. As of 2026-06-25 AM: no reply yet. Everything in section 2 is gated on
  this.

- FULL TEXT ALREADY PARTLY WORKS. get_fulltext returned sciencedirect-fulltext
  (154,448 chars) on 10.1016/j.infoandorg.2026.100608 WITHOUT the insttoken —
  Elsevier entitlements are granular/per-endpoint, so the full-text path shows
  life even before the token. Provenance flagging (sciencedirect-fulltext /
  oa-fulltext / scopus-abstract / none) is the methodological backbone: the audit
  must never present an abstract-grade verdict as full-text-grade.

- RECENCY FLOOR. Ahead-of-print 2026 papers are not yet Scopus-indexed and cannot
  be seeds (e.g. Leavell ISR 10.1287/isre.2024.1339, published online 2026-06-12,
  returned "no record"). OpenAlex/Crossref index faster — folding them in would
  raise the recency floor (future triangulation work).

### Lessons (the recurring tax)

Three times now, Claude Code's mocked tests passed while the live Scopus
contract failed: (1) cursor paging cap, (2) the 1 MB tool-return limit,
(3) the REF parser. Mocked tests prove *logic*; only a live call proves the
*shape*. **Rule: every new tool gets a live smoke test before we trust it.**

The REF parser bug specifically: real REF view uses FLAT keys
(`scopus-id`, `ce:doi`, `title`, `sourcetitle`, `prism:coverDate`,
`author-list.author[].ce:indexed-name`, dedup by `@auid`), NOT the nested
`ref-info`/`refd-itemidlist` structure originally assumed. The 0.5.1 test now
asserts a parsed ref has `scopus_id` or `doi`, so this can't regress silently.

### Cross-source principle: Scopus = structure, Springer = content

`resolve_identifier` -> DOI -> SpringerLink (`get_article`,
`get_open_access_fulltext`). Scopus knows the citation graph; Springer knows
what papers *say* (real abstracts, OA full text). Citation tracing has never
seen inside the papers — this pairing does.

**Coverage caveat that shapes which ideas are real:** Springer's IS footprint is
partial. Basket of Eight is mostly INFORMS/Wiley/T&F, not Springer — for
MISQ/ISR, fall back to Scopus abstracts or triangulate via OpenAlex/Crossref.
Springer lands well on BISE, Electronic Markets, IS Frontiers, and the
LNCS/LNBIP conference world (e.g. the PoEM paper, the DPP papers in Electronic
Markets). Full-text-dependent ideas work best on the design-science / Euro-IS /
conference slice and degrade gracefully elsewhere.

#### Ideas on top of the foundation

- **Annotated network maps.** Name coupling/co-citation clusters by their shared
  Springer abstract content, not raw keywords.
- **Citation-context / predicate test.** With OA full text, check whether a
  citing paper engages the seed construct substantively (load-bearing in the
  theory section) or just name-drops it. Scopus says "X cited Y"; Springer text
  says *how* — real inheritance vs. ritual citation.
- **Bridge-paper reading pipeline.** Map network (Scopus) -> high-betweenness
  papers -> full text (Springer) for just those -> summarize. Read 8 pivotal
  papers, not 200 abstracts.

### How this feeds `mechanism-inheritance-audit` (the convergence point)

Today the audit runs on the handful of papers Michal has personally read. The
pipeline makes it corpus-scale and empirical:

1. **Seed** a paper or construct (organizing vision, swift trust).
2. **`search_all`** the forward-citation lineage across generations (no 25-cap).
3. **`resolve_identifier`** each citing paper -> DOI.
4. **SpringerLink** pull abstracts / OA full text per citing cohort.
5. **Feed the text** into the inheritance classifier to judge
   *survives / reinterprets / breaks* per cohort — on content, not titles.
6. **Predicate test** uses OA full text to confirm the mechanism's predicates
   actually survive in the citing work, not just the label.

Scopus supplies the lineage skeleton; Springer supplies the flesh. The
difference between an anecdote and a corpus-scale finding for **"What Inherits?
A Forcing Audit for the IS Cumulative Tradition"** (CAIS Debate, Sept 2026).

### The reframe: this is an AUDIT layer on an established method, not a new method

The structural machinery built so far — forward lineage walking, SPC main-path
scoring, the layered DAG, the D3/PNG renderers — is NOT the contribution. It is a
reimplementation of an established, decades-old, citable method: main-path
analysis (Hummon & Doreian 1989; Batagelj 2003), already implemented in Pajek and
already applied within IS (Liang et al. 2016, IT outsourcing, Information &
Management). The map this produces is a solved problem.

The contribution is the layer ON TOP: a substrate/predicate audit that reads the
full text at each transmission edge and judges whether the mechanism actually
survived, was reinterpreted, or broke. Every main-path method in the literature
assumes citation == knowledge transmission. None reads the text to test that
assumption. That untested assumption is the blind spot; the audit fills it.

Paper structure that follows from this:
- Section 3 (baseline / method built upon): the SPC main-path machinery. Cite
  Hummon-Doreian, Batagelj, Pajek, Liang et al. This is DONE in code.
- Section 4 (contribution): the substrate/predicate audit. Survives / reinterprets
  / breaks, read from full text. This is SPEC'd, not built (gated on insttoken).

Key rhetorical move: reproduce a Liang-style main-path trajectory on the seed,
then show — by reading each edge — that some transmissions are real inheritance
and others ceremonial, and that this changes the trajectory. The before/after
figure (raw map vs. verdict-coloured map) is the key figure.

Target paper: "What Inherits? A Forcing Audit for the IS Cumulative Tradition"
(CAIS Debate, Sept 2026).

### Four lineage-model design ideas (all gated on REF + full text)

These emerged in sequence and compose into one model. The tree rendered on
2026-06-24 is the single-parent SIMPLIFICATION of this target model.

(a) TRANSMISSION-BASED PARENTAGE. Do not parent every citer to the seed. Parent
    each paper to the most recent lineage member it ALSO cites — that is where it
    actually inherited the construct; the seed citation is often ceremonial.
    Requires each node's reference list (REF view).

(b) CITATION INTENSITY AS EDGE WEIGHT. Count in-text mentions of each cited
    lineage member in the body. One parenthetical mention in the intro = likely
    performative; repeated engagement across sections = real inheritance. A
    structural (countable) signal that needs full text but not judgment. Fiddly:
    must handle author-year and numeric citation forms, disambiguate same-author
    papers. NOTE: nearest prior work is Liu et al. 2014 ("citations with
    different levels of relevancy") — read it to carve the delta.

(c) MULTIPLE PARENTS (the DAG, not the tree). Retrospectives / "25 years on"
    papers engage the whole history including the root — they don't branch at the
    end, they wire to many ancestors at once. A paper is a child of EVERY lineage
    member it substantively engages, weighted by intensity. Generation becomes a
    property of the EDGE (this edge spans N generations), not of the paper. Node
    parentage shape then classifies paper type for free: many-generation parents
    = synthesiser/retrospective; one strong recent parent = thread-continuer;
    weak seed-only edge = performative citer.

(d) PERFORMATIVE-CITATION FLAGGING. A late, low-intensity, seed-only citation is
    ceremonial, not inheritance. Time-lag (citer year − seed year), especially
    relative to the construct's canonisation point, is a cheap STRUCTURAL PRIOR
    for which edges to suspect. Lag tells you where to look; the text tells you
    what's there. Prior, never verdict. NOTE: nearest prior work is Liu & Kuan
    2016 ("decay in knowledge diffusion") — read it to carve the delta.

Discipline held throughout: SERVER = structure/data (deterministic, no LLM).
SKILL = judgment (the survives/reinterprets/breaks classifier). Structure prunes
(main-path / lag / parentage / intensity); the classifier judges only the spine.

### Related work to map (the foil literature)

The contribution is positioned AGAINST this literature, which is the foundation
and the foil — not competition. Read the two starred ones FIRST; they are nearest
the contribution and define the delta.

Foundational / method:
- Hummon & Doreian (1989), Social Networks 11(1):39-63 — 10.1016/0378-8733(89)90017-8 — the origin (SPC method rebuilt on 06-24).
- Batagelj (2003), arXiv cs/0309023 — the SPC algorithm specifically.
- Liu & Lu (2012), JASIST 63(3):528-542 — 10.1002/asi.21692 — key-route search (multiple main paths; relevant to DAG-not-tree).
- Liu, Lu & Ho (2019), Scientometrics 119(1):379-391 — 10.1007/s11192-019-03034-x — "a few notes on main path analysis"; where the method is contested.

NEAREST PRIOR WORK — READ FIRST:
- * Liu, Chen, Ho & Li (2014), JASIST 65(12):2479-2488 — 10.1002/asi.23135 — "citations with different levels of relevancy." Nearest to the CITATION-INTENSITY idea. Confirm: they weight structurally/by metadata; the audit weights by READING.
- * Liu & Kuan (2016), JASIST 67(2):465-476 — 10.1002/asi.23384 — "decay in knowledge diffusion." Nearest to the PERFORMATIVE-CITATION idea. Confirm: their decay is structural; the audit's is text-confirmed.

IS precedent (proves the method is accepted in-field; also the visual foil):
- Liang, Wang, Xue & Cui (2016), Information & Management 53(2):227-251 — 10.1016/j.im.2015.10.001 — main-path analysis of IT outsourcing. Figure 5 ("multiple main paths of the ITO citation network") is the canonical example of what the structural method produces — and what it cannot show (whether any edge is real inheritance). Use as the motivating foil figure.

Also useful:
- Lucio-Arias & Leydesdorff (2008), JASIST 59(12):1948-1962 — 10.1002/asi.20903 — HistCite historiograms.
- Yeo et al. (2014), Scientometrics 98(1):633-655 — 10.1007/s11192-013-1140-3 — aggregative/stochastic main paths.

Resource: Da Vincier Lab "list of main path articles" — davincierlab.weebly.com/list-of-main-path-articles.html — maintained bibliography; literature-review starting set.

STILL TO SEARCH (not yet mapped): citation-context classification / "citation
function" NLP literature — the subfield that classifies WHY a paper cites another.
Nearest to the intensity/engagement idea on the NLP side; map before claiming
novelty there. (Not searched as of 06-25.)

Tooling note: Pajek is the canonical main-path tool but is a GUI desktop app — not
a library, no API to attach edge verdicts. Cite it as the established tool; do NOT
try to integrate it. Use it once at most to validate that compute_main_path
reproduces the canonical SPC numbers.

### SESSION D — orchestrator skill prompt (ready to paste into Claude Code)

> Note: build the lineage-walker tool (multi-generation forward traversal) as
> part of this, OR as a small Session A.5 first — the orchestrator needs it.
> The current `get_citing_papers` / `search_all` are single-hop only.

---

I want to build a cross-source bibliometric orchestrator as a SKILL (not server
code), in my skills directory, that chains my Scopus MCP server and my
SpringerLink MCP server to run mechanism-inheritance audits at corpus scale.
Read my existing `mechanism-inheritance-audit` skill first to match its
structure, vocabulary (survives / reinterprets / breaks, predicate test,
forcing analogy), and output format — this skill orchestrates data collection
and hands the assembled corpus to that existing classifier; it does not
reimplement the judgment.

The skill's pipeline, given a seed paper (Scopus ID or DOI) and a construct name:

1. Resolve the seed via `resolve_identifier` to get its DOI and metadata.
2. Walk the forward-citation lineage with `search_all` on `REF(2-s2.0-<id>)`.
   Support N generations (default 1, optional 2): generation 1 = direct citers;
   generation 2 = citers of those, deduped against generation 1. Tag every
   citing paper with its generation, year, and venue. Cap per generation to
   bound quota (default 300).
3. For each citing paper, resolve its DOI.
4. For papers whose DOI is in Springer's coverage, pull the abstract via the
   SpringerLink MCP (`get_article`), and OA full text via
   `get_open_access_fulltext` where available. For non-Springer DOIs, fall back
   to the Scopus abstract. Record provenance (springer-fulltext /
   springer-abstract / scopus-abstract / none) per paper.
5. Group citing papers into cohorts by generation x (year band or venue tier).
6. Assemble a corpus file (reuse the Scopus file-output convention:
   `SCOPUS_MCP_OUTPUT_DIR`, JSON) with one record per citing paper:
   scopus_id, doi, title, year, venue, generation, provenance, abstract/text.
7. Hand each cohort's text to the `mechanism-inheritance-audit` classifier to
   judge survives / reinterprets / breaks for the named construct, and where OA
   full text exists, run the predicate test (does the citing paper engage the
   construct substantively or name-drop it).
8. Return: a cohort-by-cohort inheritance table (generation, cohort, verdict,
   evidence-strength flag based on provenance), the corpus file path, and a list
   of the highest-leverage papers (full-text-available + high citation) to read.

Be explicit about the coverage caveat: Springer IS coverage is partial, so most
verdicts on Basket-of-Eight-heavy lineages will rest on abstracts, not full
text — flag evidence strength accordingly and never present an abstract-only
verdict as if it had full-text backing.

This is a design-and-scaffold task; don't run the full pipeline live yet — build
the skill, stub the tool calls with the real signatures, and give me a dry-run
plan on the organizing-vision seed before we spend quota on a real corpus.

---
