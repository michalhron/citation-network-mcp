"""Result files: output folder, JSON/CSV writers, inline-vs-file threshold."""
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


CSV_COLUMNS = [
    'scopus_id', 'title', 'creator', 'publication_name',
    'cover_date', 'doi', 'cited_by_count', 'aggregation_type', 'url',
    'openalex_id', 'source',
]


# Inline-vs-file threshold: result sets larger than this are written to disk.
SEARCH_ALL_INLINE_THRESHOLD = 50


def _output_dir() -> Path:
    """Resolve the directory for large result dumps.

    Order: SCOPUS_MCP_OUTPUT_DIR env var → ~/scopus-mcp-output.
    Creates the directory (and parents) if absent.
    """
    d = os.environ.get('SCOPUS_MCP_OUTPUT_DIR')
    path = Path(d).expanduser() if d else Path.home() / 'scopus-mcp-output'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _query_slug(query: str) -> str:
    """Derive a short, filesystem-safe slug from a Scopus query string."""
    slug = re.sub(r'[^\w\s-]', '', query.lower())
    slug = re.sub(r'[\s_-]+', '-', slug).strip('-')
    return slug[:50] or 'query'


def write_fulltext_to_disk(doi: str, text: str) -> str:
    """Write retrieved full text to disk; return absolute path.

    Filename: fulltext-<doi-slug>.txt under SCOPUS_MCP_OUTPUT_DIR.
    """
    out = _output_dir()
    slug = re.sub(r'[^\w-]', '-', doi)[:60].strip('-')
    path = out / f'fulltext-{slug}.txt'
    path.write_text(text, encoding='utf-8')
    return str(path)


def write_results_to_disk(records: List[Dict[str, Any]], query: str) -> Dict[str, str]:
    """Write cleaned records to JSON and CSV files; return their absolute paths.

    Output directory: SCOPUS_MCP_OUTPUT_DIR if set, else ~/scopus-mcp-output.
    CSV columns: scopus_id, title, creator, publication_name, cover_date,
                 doi, cited_by_count, aggregation_type, url.
    """
    out = _output_dir()
    ts = datetime.now().strftime('%Y%m%dT%H%M%S')
    base = f'scopus-{_query_slug(query)}-{ts}'

    json_path = out / f'{base}.json'
    csv_path = out / f'{base}.csv'

    json_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)

    return {'json_path': str(json_path), 'csv_path': str(csv_path)}


def should_write_to_disk(
    records: List[Dict[str, Any]],
    threshold: int = SEARCH_ALL_INLINE_THRESHOLD,
) -> bool:
    """Return True when the result count exceeds the inline-return threshold."""
    return len(records) > threshold
