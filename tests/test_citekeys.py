"""Tests for citekey sources: bib parsing, use scanning, pin detection, item matching."""

import pytest

from tests.conftest import make_item
from zelador.citekeys import (
    BibEntry,
    match_entries,
    parse_bib,
    pinned_citekey,
    scan_sources,
    scan_text,
)
from zelador.config import ConfigError

BIB = """
@comment{jabref-meta: databaseType:bibtex;}
@string{jofp = {Journal of Fine Papers}}

@article{shannon1948,
  title = {A Mathematical Theory of Communication},
  author = {Shannon, Claude E.},
  year = {1948},
  doi = {10.1002/j.1538-7305.1948.tb01338.x},
}

@book{simon1971designing,
  title = {Designing Organizations for an {Information-Rich} World},
  author = {Simon, Herbert A.},
  date = {1971-09},
}

@misc{noyear,
  title = "Quoted Title, With Comma",
  year = 2020,
}
"""


class TestParseBib:
    def test_entries_with_keys_and_fields(self):
        entries = parse_bib(BIB)
        assert [e.citekey for e in entries] == ["shannon1948", "simon1971designing", "noyear"]
        shannon = entries[0]
        assert shannon.doi == "10.1002/j.1538-7305.1948.tb01338.x"
        assert shannon.title == "A Mathematical Theory of Communication"
        assert shannon.year == "1948"

    def test_comment_preamble_string_are_not_entries(self):
        keys = {e.citekey for e in parse_bib(BIB)}
        assert "jofp" not in keys and "jabref-meta: databaseType:bibtex;" not in keys

    def test_nested_braces_in_title_survive(self):
        entry = parse_bib(BIB)[1]
        assert entry.title == "Designing Organizations for an {Information-Rich} World"

    def test_year_falls_back_to_date_field(self):
        assert parse_bib(BIB)[1].year == "1971"

    def test_quoted_values_and_bare_numbers(self):
        entry = parse_bib(BIB)[2]
        assert entry.title == "Quoted Title, With Comma"
        assert entry.year == "2020"
        assert entry.doi == ""

    def test_doi_is_normalized(self):
        text = "@article{x, doi = {https://doi.org/10.1000/XYZ} }"
        assert parse_bib(text)[0].doi == "10.1000/xyz"


class TestScanText:
    def test_wiki_links(self):
        found = scan_text("As shown in [[@shannon1948]] and [[@simon1971designing|Simon]].")
        assert found == {"shannon1948", "simon1971designing"}

    def test_wiki_link_with_heading_anchor(self):
        assert scan_text("See [[@shannon1948#Results]].") == {"shannon1948"}

    def test_tex_cite_commands_with_comma_lists(self):
        text = r"\cite{a} \citep{b,c} \citet{d} \citep[see][p.~3]{e, f}"
        assert scan_text(text) == {"a", "b", "c", "d", "e", "f"}

    def test_plain_at_mentions_are_not_citations(self):
        assert scan_text("mail me @ home; user@example.com; [@pandoc2020]") == set()


class TestScanSources:
    def test_bib_globs_and_note_basenames(self, tmp_path):
        (tmp_path / "library.bib").write_text(BIB)
        vault = tmp_path / "vault"
        (vault / "notes").mkdir(parents=True)
        (vault / "draft.md").write_text("Cites [[@shannon1948]] twice: [[@shannon1948]].")
        (vault / "notes" / "@simon1971designing.md").write_text("A literature note.")
        (vault / "notes" / "paper.tex").write_text(r"\citep{ghost2024}")
        scan = scan_sources(
            [str(tmp_path / "library.bib"), str(vault / "**" / "*.md"), str(vault / "**" / "*.tex")]
        )
        assert {e.citekey for e in scan.entries} == {"shannon1948", "simon1971designing", "noyear"}
        assert set(scan.cited) == {"shannon1948", "simon1971designing", "ghost2024"}
        assert scan.cited["shannon1948"] == [str(vault / "draft.md")]

    def test_missing_bib_path_fails_loudly(self, tmp_path):
        with pytest.raises(ConfigError, match="no such"):
            scan_sources([str(tmp_path / "gone.bib")])

    def test_empty_glob_is_fine(self, tmp_path):
        scan = scan_sources([str(tmp_path / "*.md")])
        assert scan.entries == [] and scan.cited == {}


class TestPinnedCitekey:
    def test_absent(self):
        assert pinned_citekey("") is None
        assert pinned_citekey("OCLC: 12345\ntex.ignore: true") is None

    def test_present_among_other_lines(self):
        assert pinned_citekey("OCLC: 12345\nCitation Key: shannon1948") == "shannon1948"

    def test_case_insensitive(self):
        assert pinned_citekey("citation key: shannon1948") == "shannon1948"


class TestMatchEntries:
    def entry(self, citekey="shannon1948", doi="", title="", year=""):
        return BibEntry(citekey=citekey, doi=doi, title=title, year=year)

    def test_doi_match_wins(self):
        items = [
            make_item("AAAA1111", DOI="https://doi.org/10.1000/XYZ", title="Different title"),
            make_item("BBBB2222", title="A Mathematical Theory of Communication", date="1948"),
        ]
        match = match_entries([self.entry(doi="10.1000/xyz")], items)
        assert match.item_for == {"shannon1948": "AAAA1111"}

    def test_title_year_fallback_normalizes(self):
        items = [
            make_item(
                "BBBB2222",
                title="A mathematical theory of communication.",
                date="July 1948",
                parsed_date="1948-07",
            )
        ]
        match = match_entries(
            [self.entry(title="A Mathematical Theory of {Communication}", year="1948")], items
        )
        assert match.item_for == {"shannon1948": "BBBB2222"}

    def test_two_items_claiming_one_entry_is_ambiguous(self):
        items = [
            make_item("AAAA1111", DOI="10.1000/xyz"),
            make_item("BBBB2222", DOI="10.1000/xyz"),
        ]
        match = match_entries([self.entry(doi="10.1000/xyz")], items)
        assert match.item_for == {}
        assert match.ambiguous == {"shannon1948": ["AAAA1111", "BBBB2222"]}

    def test_no_match_lands_in_unmatched(self):
        match = match_entries([self.entry(doi="10.9/none", title="Gone", year="1900")], [])
        assert match.unmatched == ["shannon1948"]

    def test_attachments_and_notes_never_match(self):
        items = [
            make_item("PDFA1111", item_type="attachment", title="Communication", date="1948"),
            make_item("NOTE1111", item_type="note", title="Communication", date="1948"),
        ]
        match = match_entries([self.entry(title="Communication", year="1948")], items)
        assert match.item_for == {}
