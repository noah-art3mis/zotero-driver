"""Tests for audit check 5: citekey integrity against the configured sources."""

from tests.conftest import make_item
from zelador.audit import citations
from zelador.audit.library import Library
from zelador.citekeys import BibEntry, SourceScan


def scan(entries=None, cited=None) -> SourceScan:
    return SourceScan(entries=entries or [], cited=cited or {})


def entry(citekey, doi="", title="", year=""):
    return BibEntry(citekey=citekey, doi=doi, title=title, year=year)


class TestCitekeyCheck:
    def test_cited_pinned_and_matched_is_clean(self):
        library = Library(
            items=[make_item("AAAA1111", DOI="10.1/x", extra="Citation Key: shannon1948")]
        )
        findings = citations.check(
            library,
            scan(
                entries=[entry("shannon1948", doi="10.1/x")],
                cited={"shannon1948": ["/vault/draft.md"]},
            ),
        )
        assert findings == []

    def test_orphaned_citation_matches_no_bib_entry(self):
        findings = citations.check(
            Library(), scan(cited={"ghost2024": ["/vault/draft.md", "/vault/old.md"]})
        )
        assert len(findings) == 1
        found = findings[0]
        assert found["kind"] == "orphaned_citation"
        assert found["keys"] == []
        assert "ghost2024" in found["message"]
        assert found["detail"]["files"] == ["/vault/draft.md", "/vault/old.md"]

    def test_cited_but_unpinned_names_the_item(self):
        library = Library(items=[make_item("AAAA1111", DOI="10.1/x", extra="OCLC: 5")])
        findings = citations.check(
            library,
            scan(
                entries=[entry("shannon1948", doi="10.1/x")],
                cited={"shannon1948": ["/vault/draft.md"]},
            ),
        )
        assert [f["kind"] for f in findings] == ["cited_unpinned"]
        assert findings[0]["keys"] == ["AAAA1111"]
        assert "shannon1948" in findings[0]["message"]

    def test_cited_entry_matching_no_item_is_reported(self):
        findings = citations.check(
            Library(),
            scan(
                entries=[entry("shannon1948", title="Gone", year="1900")],
                cited={"shannon1948": ["/vault/draft.md"]},
            ),
        )
        assert [f["kind"] for f in findings] == ["unmatched_entry"]

    def test_cited_entry_claimed_by_two_items_is_ambiguous(self):
        library = Library(
            items=[make_item("AAAA1111", DOI="10.1/x"), make_item("BBBB2222", DOI="10.1/x")]
        )
        findings = citations.check(
            library,
            scan(
                entries=[entry("shannon1948", doi="10.1/x")],
                cited={"shannon1948": ["/vault/draft.md"]},
            ),
        )
        assert [f["kind"] for f in findings] == ["ambiguous_entry"]
        assert findings[0]["keys"] == ["AAAA1111", "BBBB2222"]

    def test_uncited_bib_entries_are_not_findings(self):
        # The bib exports the whole library; only cited keys matter.
        findings = citations.check(
            Library(items=[make_item("AAAA1111", DOI="10.1/x", extra="")]),
            scan(entries=[entry("shannon1948", doi="10.1/x")]),
        )
        assert findings == []
