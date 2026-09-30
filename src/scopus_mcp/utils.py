"""Compatibility layer: these helpers moved to focused modules
(output, oa_fulltext, graphs, lineage, records). Import from those in new
code; this module re-exports every name so existing imports keep working."""
from .output import (
    CSV_COLUMNS,
    SEARCH_ALL_INLINE_THRESHOLD,
    _output_dir,
    _query_slug,
    write_fulltext_to_disk,
    write_results_to_disk,
    should_write_to_disk,
)
from .oa_fulltext import (
    _extract_pdf_text,
    _extract_html_text,
    fetch_oa_fulltext,
)
from .graphs import (
    EDGE_CSV_COLUMNS,
    _make_node_label,
    compute_pairwise_edges,
    write_graph_to_disk,
    render_graph_png,
)
from .lineage import (
    write_lineage_to_disk,
    _rec_key,
    compute_main_path,
    _D3_HTML_TEMPLATE,
    render_lineage_html,
    render_lineage_png,
)
from .records import (
    clean_search_results,
    _reconstruct_abstract_from_openalex,
    _strip_jats_tags,
    _fetch_abstract_openalex,
    _fetch_abstract_crossref,
    clean_abstract_details,
    clean_author_profile,
    _extract_affiliation,
    to_scopus_id,
    to_eid,
    detect_id_type,
    clean_identifiers,
    clean_references,
)

__all__ = [
    'CSV_COLUMNS',
    'SEARCH_ALL_INLINE_THRESHOLD',
    '_output_dir',
    '_query_slug',
    'write_fulltext_to_disk',
    'write_results_to_disk',
    'should_write_to_disk',
    '_extract_pdf_text',
    '_extract_html_text',
    'fetch_oa_fulltext',
    'EDGE_CSV_COLUMNS',
    '_make_node_label',
    'compute_pairwise_edges',
    'write_graph_to_disk',
    'render_graph_png',
    'write_lineage_to_disk',
    '_rec_key',
    'compute_main_path',
    '_D3_HTML_TEMPLATE',
    'render_lineage_html',
    'render_lineage_png',
    'clean_search_results',
    '_reconstruct_abstract_from_openalex',
    '_strip_jats_tags',
    '_fetch_abstract_openalex',
    '_fetch_abstract_crossref',
    'clean_abstract_details',
    'clean_author_profile',
    '_extract_affiliation',
    'to_scopus_id',
    'to_eid',
    'detect_id_type',
    'clean_identifiers',
    'clean_references',
]
