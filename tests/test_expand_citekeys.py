"""Tests for pin_citekey expansion."""

from tests.test_expand import failures_of, make_item, run_expand
from zelador.citekeys import BibEntry, SourceScan

SHANNON = BibEntry(citekey="shannon1948", doi="10.1/x", title="", year="")
CITED = SourceScan(entries=[SHANNON], cited={"shannon1948": ["/vault/draft.md"]})


class TestPinCitekey:
    def test_appends_pin_line_to_empty_extra(self):
        plan = run_expand(
            [{"op": "pin_citekey", "key": "AAAA1111"}],
            items=[make_item("AAAA1111", version=6, DOI="10.1/x")],
            scan=CITED,
        )
        op = plan.operations[0]
        assert op.facet == "field:extra" and op.version == 6 and op.risk == "low"
        assert op.old == "" and op.new == "Citation Key: shannon1948"

    def test_trailing_newline_in_extra_leaves_no_blank_line(self):
        plan = run_expand(
            [{"op": "pin_citekey", "key": "AAAA1111"}],
            items=[make_item("AAAA1111", DOI="10.1/x", extra="OCLC: 5\n")],
            scan=CITED,
        )
        assert plan.operations[0].old == "OCLC: 5\n"
        assert plan.operations[0].new == "OCLC: 5\nCitation Key: shannon1948"

    def test_preserves_existing_extra_content(self):
        plan = run_expand(
            [{"op": "pin_citekey", "key": "AAAA1111"}],
            items=[make_item("AAAA1111", DOI="10.1/x", extra="OCLC: 5")],
            scan=CITED,
        )
        assert plan.operations[0].new == "OCLC: 5\nCitation Key: shannon1948"

    def test_already_pinned_same_key_is_a_no_op(self):
        plan = run_expand(
            [{"op": "pin_citekey", "key": "AAAA1111"}],
            items=[make_item("AAAA1111", DOI="10.1/x", extra="Citation Key: shannon1948")],
            scan=CITED,
        )
        assert plan.operations == []

    def test_conflicting_existing_pin_is_refused(self):
        failures = failures_of(
            [{"op": "pin_citekey", "key": "AAAA1111"}],
            items=[make_item("AAAA1111", DOI="10.1/x", extra="Citation Key: other2020")],
            scan=CITED,
        )
        assert "other2020" in failures

    def test_requires_citekey_sources(self):
        assert "citekey_sources" in failures_of(
            [{"op": "pin_citekey", "key": "AAAA1111"}], items=[make_item("AAAA1111")]
        )

    def test_no_matching_bib_entry_is_refused(self):
        failures = failures_of(
            [{"op": "pin_citekey", "key": "AAAA1111"}],
            items=[make_item("AAAA1111", DOI="10.9/other")],
            scan=CITED,
        )
        assert "no bib entry matches" in failures

    def test_entry_claimed_by_two_items_names_the_collision(self):
        failures = failures_of(
            [{"op": "pin_citekey", "key": "AAAA1111"}],
            items=[make_item("AAAA1111", DOI="10.1/x"), make_item("BBBB2222", DOI="10.1/x")],
            scan=CITED,
        )
        assert "AAAA1111" in failures and "BBBB2222" in failures

    def test_item_claimed_by_two_entries_is_refused(self):
        twice = SourceScan(
            entries=[SHANNON, BibEntry(citekey="dupe1948", doi="10.1/x", title="", year="")],
            cited={},
        )
        failures = failures_of(
            [{"op": "pin_citekey", "key": "AAAA1111"}],
            items=[make_item("AAAA1111", DOI="10.1/x")],
            scan=twice,
        )
        assert "dupe1948" in failures and "shannon1948" in failures
