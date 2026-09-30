"""Open-access full text: multi-source candidates, ranking and fetching."""
import asyncio
import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from scopus_mcp import oa_fulltext
from scopus_mcp.oa_fulltext import _extract_jats_text, _rank, fetch_oa_fulltext

# Texts that open with the paper's title, as a real copy of it does.
LONG = 'A Paper\nJ. Author\n' + 'x' * 6000
DOI = '10.1234/paper'


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeWeb:
    """Answers per host/path; records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        url = str(request.url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response(request) if callable(response) else response
        return httpx.Response(404)


def _serve(web):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(web)
        return real(*args, **kwargs)
    return patch.object(oa_fulltext.httpx, 'AsyncClient', side_effect=factory)


def _pdf_route(text):
    return httpx.Response(200, content=b'%PDF-1.7', headers={'content-type': 'application/pdf'}), text


OPENALEX = 'https://api.openalex.org/works/doi:'
S2 = 'https://api.semanticscholar.org/'
EPMC = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
ARXIV_API = 'https://export.arxiv.org/api/query'


def _openalex(locations, title='A Paper'):
    return httpx.Response(200, json={'title': title, 'locations': locations})


def _loc(url, version, pdf=True, oa=True, name='Repo'):
    return {'is_oa': oa, 'version': version, 'source': {'display_name': name},
            **({'pdf_url': url} if pdf else {'landing_page_url': url})}


NO_S2 = httpx.Response(404)
NO_EPMC = httpx.Response(200, json={'resultList': {'result': []}})
EMPTY_ARXIV = httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')


# ---------------------------------------------------------------------------
# Ranking and extraction
# ---------------------------------------------------------------------------

def test_rank_prefers_published_then_pdf_and_dedupes():
    cands = [
        {'url': 'https://a/landing', 'source': 's', 'version': 'published', 'kind': 'html'},
        {'url': 'https://a/pre.pdf', 'source': 's', 'version': 'preprint', 'kind': 'pdf'},
        {'url': 'https://a/pub.pdf', 'source': 's', 'version': 'published', 'kind': 'pdf'},
        {'url': 'https://a/pub.pdf/', 'source': 'dup', 'version': 'published', 'kind': 'pdf'},
        {'url': 'https://a/acc.pdf', 'source': 's', 'version': 'accepted manuscript', 'kind': 'pdf'},
        {'url': 'https://a/xml', 'source': 's', 'version': 'published', 'kind': 'jats'},
    ]
    cands.append({'url': 'https://a/unknown.pdf', 'source': 's', 'version': None, 'kind': 'pdf'})
    assert [c['url'] for c in _rank(cands)] == [
        'https://a/pub.pdf', 'https://a/xml', 'https://a/acc.pdf', 'https://a/unknown.pdf',
        'https://a/pre.pdf', 'https://a/landing']


def test_extract_jats_body_only():
    xml = ('<article><front><title>Front matter</title></front>'
           '<body><sec><p>First paragraph.</p><p>Second   paragraph.</p></sec></body></article>')
    assert _extract_jats_text(xml) == 'First paragraph. Second paragraph.'
    assert _extract_jats_text('not xml <') is None


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def test_published_pdf_wins_and_short_landing_page_is_skipped(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    web = FakeWeb({
        OPENALEX: _openalex([
            _loc('https://repo/landing', 'publishedVersion', pdf=False),
            _loc('https://repo/pub.pdf', 'publishedVersion'),
        ]),
        S2: NO_S2, EPMC: NO_EPMC, ARXIV_API: EMPTY_ARXIV,
        'https://repo/pub.pdf': httpx.Response(200, content=b'%PDF', headers={'content-type': 'application/pdf'}),
    })
    with _serve(web), patch.object(oa_fulltext, '_extract_pdf_text', return_value=LONG):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['source_url'] == 'https://repo/pub.pdf'
    assert out['source'] == 'OpenAlex: Repo' and out['version'] == 'published'
    assert out['attempts'] == [{'url': 'https://repo/pub.pdf', 'source': 'OpenAlex: Repo',
                                'version': 'published', 'outcome': 'ok (6018 chars)'}]


def test_falls_through_short_and_blocked_to_arxiv_via_semantic_scholar(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    web = FakeWeb({
        OPENALEX: _openalex([_loc('https://repo/landing', 'submittedVersion', pdf=False)]),
        S2: httpx.Response(200, json={'title': 'A Paper', 'externalIds': {'ArXiv': '1512.03385'},
                                      'openAccessPdf': {'url': 'https://publisher/blocked.pdf'}}),
        EPMC: NO_EPMC,
        'https://repo/landing': httpx.Response(200, text='<html><body>Abstract only.</body></html>',
                                               headers={'content-type': 'text/html'}),
        'https://publisher/blocked.pdf': httpx.Response(403),
        'https://arxiv.org/pdf/1512.03385': httpx.Response(200, content=b'%PDF',
                                                           headers={'content-type': 'application/pdf'}),
    })
    with _serve(web), patch.object(oa_fulltext, '_extract_pdf_text', return_value=LONG):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['source'] == 'arXiv' and out['version'] == 'preprint'
    outcomes = {a['url']: a['outcome'] for a in out['attempts']}
    assert outcomes['https://publisher/blocked.pdf'] == 'HTTP 403'
    assert outcomes['https://arxiv.org/pdf/1512.03385'] == 'ok (6018 chars)'
    # An arXiv ID from Semantic Scholar makes the title search unnecessary.
    assert not any(str(r.url).startswith(ARXIV_API) for r in web.requests)


def test_arxiv_title_search_requires_exact_title(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><id>http://arxiv.org/abs/2401.00001v1</id><title>A Paper: Extended</title></entry>'
            '<entry><id>http://arxiv.org/abs/2401.00002v2</id><title>A  paper</title></entry></feed>')
    web = FakeWeb({
        OPENALEX: _openalex([], title='A Paper'), S2: NO_S2, EPMC: NO_EPMC,
        ARXIV_API: httpx.Response(200, text=feed),
        'https://arxiv.org/pdf/2401.00002v2': httpx.Response(200, content=b'%PDF',
                                                             headers={'content-type': 'application/pdf'}),
    })
    with _serve(web), patch.object(oa_fulltext, '_extract_pdf_text', return_value=LONG):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['source_url'] == 'https://arxiv.org/pdf/2401.00002v2'
    query = parse_qs(urlsplit(str(next(r.url for r in web.requests
                                       if str(r.url).startswith(ARXIV_API)))).query)
    assert query['search_query'] == ['ti:"A Paper"']


def test_europe_pmc_full_text_xml(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    body = '<article><body><p>' + 'word ' * 1500 + '</p></body></article>'
    web = FakeWeb({
        OPENALEX: _openalex([]), S2: NO_S2, ARXIV_API: EMPTY_ARXIV,
        EPMC: httpx.Response(200, json={'resultList': {'result': [
            {'pmcid': 'PMC8371605', 'isOpenAccess': 'Y', 'inEPMC': 'Y'}]}}),
        'https://www.ebi.ac.uk/europepmc/webservices/rest/PMC8371605/fullTextXML':
            httpx.Response(200, text=body, headers={'content-type': 'application/xml'}),
    })
    with _serve(web):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['source'] == 'Europe PMC' and out['version'] == 'published'
    assert out['text'].startswith('word word')


def test_unpaywall_and_core_only_when_configured(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    web = FakeWeb({OPENALEX: _openalex([]), S2: NO_S2, EPMC: NO_EPMC, ARXIV_API: EMPTY_ARXIV})
    with _serve(web):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['text'] is None and out['attempts'] == []
    hosts = {urlsplit(str(r.url)).netloc for r in web.requests}
    assert 'api.unpaywall.org' not in hosts and 'api.core.ac.uk' not in hosts
    # No contact email configured: none is sent anywhere.
    assert all('mailto' not in r.headers.get('user-agent', '') for r in web.requests)
    assert all('mailto' not in str(r.url) for r in web.requests)


def test_unpaywall_and_core_used_with_email_and_key(monkeypatch):
    monkeypatch.setenv('CONTACT_EMAIL', 'researcher@uni.edu')
    monkeypatch.setenv('CORE_API_KEY', 'core-key')
    web = FakeWeb({
        OPENALEX: _openalex([]), S2: NO_S2, EPMC: NO_EPMC, ARXIV_API: EMPTY_ARXIV,
        'https://api.unpaywall.org/v2/': httpx.Response(200, json={'oa_locations': [
            {'url_for_pdf': 'https://inst/accepted.pdf', 'version': 'acceptedVersion',
             'repository_institution': 'Ghent University'}]}),
        'https://api.core.ac.uk/v3/search/works': httpx.Response(200, json={'results': [
            {'downloadUrl': 'https://core.ac.uk/download/1.pdf'}]}),
        'https://inst/accepted.pdf': httpx.Response(200, content=b'%PDF',
                                                    headers={'content-type': 'application/pdf'}),
    })
    with _serve(web), patch.object(oa_fulltext, '_extract_pdf_text', return_value=LONG):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['source'] == 'Unpaywall: Ghent University'
    assert out['version'] == 'accepted manuscript'
    unpaywall = next(r for r in web.requests if 'unpaywall' in str(r.url))
    assert parse_qs(urlsplit(str(unpaywall.url)).query)['email'] == ['researcher@uni.edu']
    core = next(r for r in web.requests if 'core.ac.uk' in str(r.url))
    assert core.headers['authorization'] == 'Bearer core-key'
    openalex = next(r for r in web.requests if 'openalex' in str(r.url))
    assert parse_qs(urlsplit(str(openalex.url)).query)['mailto'] == ['researcher@uni.edu']


def test_broken_source_does_not_stop_the_others(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    web = FakeWeb({
        OPENALEX: httpx.Response(200, text='not json'),
        S2: httpx.Response(500),
        EPMC: httpx.Response(200, json={'resultList': {'result': [
            {'pmcid': 'PMC1', 'isOpenAccess': 'Y', 'inEPMC': 'Y'}]}}),
        'https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1/fullTextXML':
            httpx.Response(200, text='<article><body>' + 'text ' * 1500 + '</body></article>'),
    })
    with _serve(web):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['source'] == 'Europe PMC'


def test_attempts_are_capped(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    locations = [_loc(f'https://repo/{i}.pdf', 'publishedVersion') for i in range(10)]
    web = FakeWeb({OPENALEX: _openalex(locations), S2: NO_S2, EPMC: NO_EPMC, ARXIV_API: EMPTY_ARXIV,
                   'https://repo/': httpx.Response(403)})
    with _serve(web):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['text'] is None
    assert len(out['attempts']) == oa_fulltext.MAX_ATTEMPTS
    assert out['source_url'] == 'https://repo/0.pdf'



# ---------------------------------------------------------------------------
# Title verification
# ---------------------------------------------------------------------------

def test_title_in_opening_tolerates_line_breaks_and_hyphenation():
    title = 'Deep Residual Learning for Image Recognition'
    assert oa_fulltext.title_in_opening(title, 'Deep Residual Learn-\ning for Image\nRecognition\nKaiming He')
    assert not oa_fulltext.title_in_opening(title, 'x' * 7000 + title)  # only far in: a citation
    assert not oa_fulltext.title_in_opening('', 'anything')


def test_mislinked_document_is_rejected_then_next_candidate_used(monkeypatch):
    """Live case: a repository PDF linked to ResNet was a thesis citing it."""
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    thesis = 'Propuesta metodologica para el procesamiento de senales\n' + 'y ' * 6000 + \
             ' He et al. Deep Residual Learning for Image Recognition. CVPR 2016.'
    paper = 'Deep Residual Learning for Image Recognition\nKaiming He\n' + 'z ' * 4000
    web = FakeWeb({
        OPENALEX: _openalex([_loc('https://repo/thesis.pdf', 'submittedVersion')],
                            title='Deep Residual Learning for Image Recognition'),
        S2: httpx.Response(200, json={'externalIds': {'ArXiv': '1512.03385'}}),
        EPMC: NO_EPMC,
        'https://repo/thesis.pdf': httpx.Response(200, content=b'%PDF', headers={'content-type': 'application/pdf'}),
        'https://arxiv.org/pdf/1512.03385': httpx.Response(200, content=b'%PDF-arxiv',
                                                           headers={'content-type': 'application/pdf'}),
    })
    texts = {b'%PDF': thesis, b'%PDF-arxiv': paper}
    with _serve(web), patch.object(oa_fulltext, '_extract_pdf_text', side_effect=lambda c: texts[c]):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['source'] == 'arXiv' and out['title_verified'] is True
    thesis_attempt = next(a for a in out['attempts'] if a['url'] == 'https://repo/thesis.pdf')
    assert thesis_attempt['outcome'] == 'different document: title not in its opening'


def test_text_accepted_unverified_when_no_title_known(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    web = FakeWeb({
        OPENALEX: _openalex([_loc('https://repo/p.pdf', 'publishedVersion')], title=None),
        S2: NO_S2, EPMC: NO_EPMC,
        'https://repo/p.pdf': httpx.Response(200, content=b'%PDF', headers={'content-type': 'application/pdf'}),
    })
    with _serve(web), patch.object(oa_fulltext, '_extract_pdf_text', return_value='q' * 6000):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['text'] and out['title_verified'] is False


def test_europe_pmc_exempt_from_title_check(monkeypatch):
    monkeypatch.delenv('CONTACT_EMAIL', raising=False)
    web = FakeWeb({
        OPENALEX: _openalex([], title='Highly accurate protein structure prediction'),
        S2: NO_S2, ARXIV_API: EMPTY_ARXIV,
        EPMC: httpx.Response(200, json={'resultList': {'result': [
            {'pmcid': 'PMC9', 'isOpenAccess': 'Y', 'inEPMC': 'Y'}]}}),
        'https://www.ebi.ac.uk/europepmc/webservices/rest/PMC9/fullTextXML':
            httpx.Response(200, text='<article><body>' + 'body ' * 1500 + '</body></article>'),
    })
    with _serve(web):
        out = _run(fetch_oa_fulltext(DOI))
    assert out['source'] == 'Europe PMC'
