"""Tests for the four M1 audit checks as pure functions over a Library snapshot."""

from tests.conftest import make_collection, make_item, make_tag
from zelador.audit import completeness, duplicates, hygiene, tagmess
from zelador.audit.library import Library


def lib(items=None, collections=None, tags=None) -> Library:
    return Library(items=items or [], collections=collections or [], tags=tags or [])


def complete_article(key="GOOD0001", **overrides):
    fields = dict(
        title="A fine paper",
        date="2021-05-01",
        parsed_date="2021-05-01",
        creators=[{"creatorType": "author", "firstName": "A", "lastName": "B"}],
        publicationTitle="Journal of Fine Papers",
        volume="12",
        issue="3",
        pages="1-10",
        DOI="10.1000/xyz",
        bib='<div class="csl-bib-body">B, A. (2021). A fine paper.</div>',
    )
    fields.update(overrides)
    return make_item(key, **fields)


class TestCompleteness:
    def test_complete_journal_article_passes(self):
        findings = completeness.check(lib(items=[complete_article()]))
        assert findings == []

    def test_missing_fields_reported_with_bib_evidence(self):
        item = complete_article(key="BAD00001", DOI="", volume="")
        findings = completeness.check(lib(items=[item]))
        assert len(findings) == 1
        finding = findings[0]
        assert finding["kind"] == "incomplete"
        assert finding["keys"] == ["BAD00001"]
        assert set(finding["detail"]["missing"]) == {"DOI", "volume"}
        assert "fine paper" in finding["detail"]["bib"]

    def test_missing_creators_and_date_reported(self):
        item = complete_article(key="BAD00002", creators=[], date="", parsed_date=None)
        findings = completeness.check(lib(items=[item]))
        assert set(findings[0]["detail"]["missing"]) >= {"creators", "date"}

    def test_inexact_date_flagged_for_webpage(self):
        item = make_item(
            "WEB00001",
            item_type="webpage",
            title="Post",
            date="2023",
            parsed_date="2023",
            creators=[{"creatorType": "author", "lastName": "X"}],
            websiteTitle="Blog",
            url="https://x.example",
        )
        findings = completeness.check(lib(items=[item]))
        assert "exact date" in str(findings[0]["detail"]["missing"])

    def test_unruled_item_type_is_a_finding(self):
        item = make_item("ODD00001", item_type="artwork", title="A painting", date="1900")
        findings = completeness.check(lib(items=[item]))
        assert findings[0]["kind"] == "no_rule"

    def test_standalone_attachment_is_a_finding(self):
        orphan = make_item("ATT00001", item_type="attachment", title="loose.pdf")
        child = make_item("ATT00002", item_type="attachment", title="ok.pdf", parentItem="GOOD0001")
        findings = completeness.check(lib(items=[complete_article(), orphan, child]))
        assert [f["kind"] for f in findings] == ["standalone_attachment"]
        assert findings[0]["keys"] == ["ATT00001"]

    def test_notes_and_annotations_skipped(self):
        note = make_item("NOTE0001", item_type="note", title="")
        assert completeness.check(lib(items=[note])) == []

    def test_preprint_accepts_archive_id_in_place_of_doi(self):
        preprint = make_item(
            "PRE00001",
            item_type="preprint",
            title="A preprint",
            date="2024-01-02",
            parsed_date="2024-01-02",
            creators=[{"creatorType": "author", "lastName": "Y"}],
            repository="arXiv",
            archiveID="arXiv:2401.00001",
        )
        assert completeness.check(lib(items=[preprint])) == []

    def test_broken_render_flagged_even_when_fields_pass(self):
        item = complete_article(bib='<div class="csl-bib-body">(n.d.)</div>')
        findings = completeness.check(lib(items=[item]))
        assert findings[0]["kind"] == "broken_render"


class TestTagMess:
    def test_case_and_near_duplicates_cluster(self):
        found = tagmess.check(
            lib(
                tags=[
                    make_tag("Machine Learning", 5),
                    make_tag("machine learning", 3, tag_type=1),
                    make_tag("machine-learning", 2),
                    make_tag("statistics", 4),
                ]
            )
        )
        assert len(found) == 1
        cluster = found[0]["detail"]["tags"]
        assert {t["tag"] for t in cluster} == {
            "Machine Learning",
            "machine learning",
            "machine-learning",
        }
        assert any(t["type"] == "auto" for t in cluster)

    def test_distinct_tags_no_findings(self):
        assert tagmess.check(lib(tags=[make_tag("ai"), make_tag("ethics")])) == []


class TestHygiene:
    def test_unfiled_empty_duplicate_and_orphaned(self):
        items = [
            make_item("ITEM0001", collections=["COLL0001"]),
            make_item("ITEM0002", collections=[]),
        ]
        collections = [
            make_collection("COLL0001", "Detection"),
            make_collection("COLL0002", "Clickbait", parent="COLL0001"),
            make_collection("COLL0003", "clickbait", parent="COLL0001"),
            make_collection("COLL0004", "Lost", parent="GONE9999"),
        ]
        found = hygiene.check(lib(items=items, collections=collections))
        kinds = {f["kind"] for f in found}
        assert kinds == {
            "unfiled_item",
            "empty_collection",
            "duplicate_siblings",
            "orphaned_subtree",
        }
        unfiled = next(f for f in found if f["kind"] == "unfiled_item")
        assert unfiled["keys"] == ["ITEM0002"]
        dupes = next(f for f in found if f["kind"] == "duplicate_siblings")
        assert set(dupes["keys"]) == {"COLL0002", "COLL0003"}

    def test_attachments_do_not_count_as_unfiled(self):
        items = [make_item("ATT00001", item_type="attachment", parentItem="X")]
        assert hygiene.check(lib(items=items)) == []


class TestDuplicates:
    def test_same_doi_grouped(self):
        items = [
            make_item("DUP00001", title="Paper A", DOI="10.1000/XYZ"),
            make_item("DUP00002", title="Paper A (copy)", DOI="https://doi.org/10.1000/xyz"),
            make_item("OTHER001", title="Unrelated", DOI="10.9999/other"),
        ]
        found = duplicates.check(lib(items=items))
        assert len(found) == 1
        assert set(found[0]["keys"]) == {"DUP00001", "DUP00002"}
        assert found[0]["kind"] == "same_doi"

    def test_near_identical_title_year(self):
        items = [
            make_item("DUP00003", title="Deep Learning!", date="2016", parsed_date="2016"),
            make_item("DUP00004", title="deep learning", date="2016", parsed_date="2016"),
        ]
        found = duplicates.check(lib(items=items))
        assert any(f["kind"] == "same_title_year" for f in found)

    def test_same_title_different_year_not_duplicate(self):
        items = [
            make_item("OK000001", title="Deep Learning", date="2016", parsed_date="2016"),
            make_item("OK000002", title="Deep Learning", date="2020", parsed_date="2020"),
        ]
        assert duplicates.check(lib(items=items)) == []
