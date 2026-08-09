"""
Integration test for get_abstract_details fallback pipeline.

Tests that DOI 10.1177/17411432221086850 (Scopus ID 85126552977) yields a
non-empty abstract either from Scopus (if an insttoken is present) or from
the OpenAlex fallback (reachable without credentials).

The test is skipped when SCOPUS_API_KEY is not set.  It makes real network
requests so it is tagged 'integration'.
"""
import os
import asyncio
import pytest

pytestmark = pytest.mark.integration

SCOPUS_ID = "85126552977"
DOI = "10.1177/17411432221086850"


@pytest.fixture
def api_key():
    key = os.getenv("SCOPUS_API_KEY")
    if not key:
        pytest.skip("SCOPUS_API_KEY not set — skipping integration test")
    return key


@pytest.mark.asyncio
async def test_get_abstract_details_non_empty(api_key):
    """Abstract must be non-empty via Scopus or OpenAlex/Crossref fallback."""
    from unittest.mock import patch
    from scopus_mcp.client import ScopusClient
    from scopus_mcp.utils import (
        clean_abstract_details,
        _fetch_abstract_openalex,
        _fetch_abstract_crossref,
    )

    with patch("scopus_mcp.config.get_api_key", return_value=api_key):
        client = ScopusClient()
        try:
            raw = await client.get_abstract(SCOPUS_ID)
        finally:
            await client.close()

    details = clean_abstract_details(raw)

    # Authors must be populated from dc:creator even in META view
    assert details["authors"], "authors list is empty"

    # DOI must be present so fallbacks can be attempted
    assert details["doi"] == DOI, f"expected doi={DOI}, got {details['doi']}"

    # If Scopus did not provide the abstract, try fallbacks
    abstract = details.get("description")
    source = details.get("abstract_source", "none")

    if not abstract:
        abstract = await _fetch_abstract_openalex(DOI)
        if abstract:
            source = "openalex"
        else:
            abstract = await _fetch_abstract_crossref(DOI)
            if abstract:
                source = "crossref"

    assert abstract, (
        f"No abstract found via scopus, openalex, or crossref for doi={DOI}. "
        f"_els_status={details.get('_els_status')}, _view_used={details.get('_view_used')}"
    )
    assert len(abstract) > 50, f"Abstract suspiciously short ({len(abstract)} chars): {abstract!r}"
    print(f"\nabstract_source={source}, length={len(abstract)}")


@pytest.mark.asyncio
async def test_openalex_fallback_reconstructs_abstract():
    """OpenAlex fallback must reconstruct a readable abstract for the test DOI."""
    from scopus_mcp.utils import _fetch_abstract_openalex

    abstract = await _fetch_abstract_openalex(DOI)
    assert abstract, "OpenAlex returned no abstract for the test DOI"
    assert len(abstract) > 50


@pytest.mark.asyncio
async def test_clean_abstract_details_authors_from_dc_creator():
    """clean_abstract_details must extract authors from dc:creator in META view."""
    from scopus_mcp.utils import clean_abstract_details

    meta_response = {
        "_view_used": "META",
        "_els_status": "OK",
        "abstracts-retrieval-response": {
            "coredata": {
                "dc:identifier": "SCOPUS_ID:85126552977",
                "prism:doi": DOI,
                "dc:title": "Test title",
                "dc:creator": {
                    "author": [
                        {
                            "@auid": "57209207827",
                            "ce:indexed-name": "Murphy G.",
                            "ce:surname": "Murphy",
                            "ce:given-name": "Gavin",
                            "ce:initials": "G.",
                        }
                    ]
                },
            }
        },
    }

    details = clean_abstract_details(meta_response)
    assert details["authors"], "authors should be populated from dc:creator"
    assert details["authors"][0]["name"] == "Murphy G."
    assert details["authors"][0]["auth_id"] == "57209207827"
    assert details["description"] is None
    assert details["abstract_source"] == "none"
    assert details["_els_status"] == "OK"
    assert details["_view_used"] == "META"
