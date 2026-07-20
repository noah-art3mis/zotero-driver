"""Tests for enrichment lookups: cache-through fetching, Crossref and arXiv parsing."""

import json

import httpx
import pytest

from zelador.lookup.cache import LookupCache
from zelador.lookup.sources import SourceError, Web, arxiv, crossref

CROSSREF_WORK = {
    "DOI": "10.1002/j.1538-7305.1948.tb01338.x",
    "title": ["A Mathematical Theory of Communication"],
    "author": [{"family": "Shannon", "given": "C. E."}],
    "issued": {"date-parts": [[1948, 7]]},
    "container-title": ["Bell System Technical Journal"],
    "volume": "27",
    "issue": "3",
    "page": "379-423",
    "publisher": "Wiley",
    "URL": "https://doi.org/10.1002/j.1538-7305.1948.tb01338.x",
}

ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <published>2017-06-12T17:57:34Z</published>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:doi>10.48550/arXiv.1706.03762</arxiv:doi>
  </entry>
</feed>
"""


def item_data(**overrides):
    data = {
        "itemType": "journalArticle",
        "title": "A Mathematical Theory of Communication",
        "creators": [{"creatorType": "author", "firstName": "C. E.", "lastName": "Shannon"}],
        "date": "1948-07",
    }
    data.update(overrides)
    return data


def web_for(handler, tmp_path) -> tuple[Web, list]:
    requests: list[httpx.Request] = []

    def handle(request):
        requests.append(request)
        return handler(request)

    cache = LookupCache(tmp_path / "cache")
    return Web(cache, transport=httpx.MockTransport(handle)), requests


class TestCacheThrough:
    def test_second_fetch_never_hits_the_network(self, tmp_path):
        web, requests = web_for(lambda r: httpx.Response(200, text="body"), tmp_path)
        assert web.get("https://api.example.org/works", {"q": "x"}) == "body"
        assert web.get("https://api.example.org/works", {"q": "x"}) == "body"
        assert len(requests) == 1

    def test_different_urls_cache_separately(self, tmp_path):
        web, requests = web_for(lambda r: httpx.Response(200, text=str(r.url)), tmp_path)
        first = web.get("https://api.example.org/works", {"q": "x"})
        second = web.get("https://api.example.org/works", {"q": "y"})
        assert first != second and len(requests) == 2

    def test_http_error_is_a_source_error_and_not_cached(self, tmp_path):
        web, requests = web_for(lambda r: httpx.Response(500, text="boom"), tmp_path)
        with pytest.raises(SourceError, match="500"):
            web.get("https://api.example.org/works")
        with pytest.raises(SourceError):
            web.get("https://api.example.org/works")
        assert len(requests) == 2


class TestCrossref:
    def test_doi_lookup_goes_direct(self, tmp_path):
        web, requests = web_for(
            lambda r: httpx.Response(200, json={"message": CROSSREF_WORK}), tmp_path
        )
        candidates = crossref(item_data(DOI="10.1002/j.1538-7305.1948.tb01338.x"), web)
        assert "/works/10.1002" in str(requests[0].url)
        assert len(candidates) == 1
        found = candidates[0]
        assert found.source == "crossref"
        assert found.title == "A Mathematical Theory of Communication"
        assert found.creators == ["Shannon, C. E."]
        assert found.year == "1948"
        assert found.container == "Bell System Technical Journal"
        assert found.volume == "27" and found.issue == "3" and found.pages == "379-423"
        assert found.score == 1.0

    def test_bibliographic_query_scores_by_title_similarity(self, tmp_path):
        other = {**CROSSREF_WORK, "DOI": "10.9/other", "title": ["Communication Systems"]}
        web, requests = web_for(
            lambda r: httpx.Response(200, json={"message": {"items": [other, CROSSREF_WORK]}}),
            tmp_path,
        )
        candidates = crossref(item_data(), web)
        assert "query.bibliographic" in str(requests[0].url)
        assert "Shannon" in str(requests[0].url)
        assert [c.doi for c in candidates] == ["10.1002/j.1538-7305.1948.tb01338.x", "10.9/other"]
        assert candidates[0].score > candidates[1].score

    def test_no_doi_and_no_title_is_refused(self, tmp_path):
        web, _ = web_for(lambda r: httpx.Response(200, json={}), tmp_path)
        with pytest.raises(SourceError, match="title"):
            crossref(item_data(title="", DOI=""), web)


class TestArxiv:
    def test_parses_atom_feed(self, tmp_path):
        web, requests = web_for(lambda r: httpx.Response(200, text=ARXIV_FEED), tmp_path)
        candidates = arxiv(item_data(title="Attention Is All You Need"), web)
        assert "search_query" in str(requests[0].url)
        assert len(candidates) == 1
        found = candidates[0]
        assert found.source == "arxiv"
        assert found.title == "Attention Is All You Need"
        assert found.creators == ["Ashish Vaswani", "Noam Shazeer"]
        assert found.year == "2017"
        assert found.doi == "10.48550/arxiv.1706.03762"
        assert found.url == "http://arxiv.org/abs/1706.03762v7"
        assert found.score == 1.0

    def test_results_are_deterministic_json_round_trip(self, tmp_path):
        web, _ = web_for(lambda r: httpx.Response(200, text=ARXIV_FEED), tmp_path)
        [found] = arxiv(item_data(title="Attention Is All You Need"), web)
        assert json.loads(json.dumps(found.__dict__)) == found.__dict__
