"""Tests for validate/expansion — intents against the live library into version-pinned plans."""

from datetime import UTC, datetime

import pytest

from tests.conftest import USER_ID, FakeZotero, make_collection, make_item
from zelador.citekeys import BibEntry, SourceScan
from zelador.client import ZoteroClient
from zelador.config import Credentials
from zelador.taxonomy import Family, TagEntry, Taxonomy
from zelador.write.contracts import Changeset
from zelador.write.expand import ValidationError, expand

NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
BACKUP_TS = "20260719T115900Z"

TAX = Taxonomy(
    families={
        "status": Family(coloured=True, exclusive=True),
        "topic": Family(),
    },
    tags=(
        TagEntry(tag="status:to-read", colour="#E69F00"),
        TagEntry(tag="status:read", colour="#009E73"),
        TagEntry(tag="topic:ai", aliases=("AI", "Artificial Intelligence")),
    ),
)


def keygen():
    counter = iter(range(1, 100))
    return lambda: f"NEWC{next(counter):04d}"


def run_expand(
    intents,
    items=None,
    collections=None,
    settings=None,
    taxonomy=TAX,
    library_version=100,
    scan=None,
):
    fake = FakeZotero(
        items=items or [],
        collections=collections or [],
        settings=settings or {},
        library_version=library_version,
        page_size=100,
    )
    client = ZoteroClient(
        Credentials(api_key="k", user_id=USER_ID), transport=fake.transport, sleep=lambda s: None
    )
    changeset = Changeset(slug="test-change", intents=intents)
    return expand(
        changeset, client, taxonomy, backup=BACKUP_TS, now=NOW, keygen=keygen(), scan=scan
    )


def failures_of(intents, **kwargs) -> str:
    with pytest.raises(ValidationError) as exc_info:
        run_expand(intents, **kwargs)
    return "\n".join(exc_info.value.failures)


class TestPlanHeader:
    def test_header_binds_backup_and_library_version(self):
        plan = run_expand(
            [{"op": "add_tag", "tag": "topic:ai", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111")],
            library_version=123,
        )
        assert plan.id == "20260719T120000Z-test-change"
        assert plan.backup == BACKUP_TS
        assert plan.library_version == 123
        assert plan.intents[0]["op"] == "add_tag"

    def test_settings_read_does_not_clobber_library_version(self):
        # The tagColors GET is a single-object request whose header carries the
        # setting's own (older) version — the plan must pin the library version.
        plan = run_expand(
            [{"op": "add_tag", "tag": "topic:ai", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111")],
            settings={"tagColors": {"value": [], "version": 40}},
            library_version=123,
        )
        assert plan.library_version == 123


class TestMergeTag:
    def test_rewrites_each_carrying_item(self):
        items = [
            make_item(
                "AAAA1111",
                version=7,
                tags=[{"tag": "AI", "type": 1}, {"tag": "keep-me", "type": 0}],
            ),
            make_item("BBBB2222", version=9, tags=[{"tag": "Artificial Intelligence", "type": 1}]),
            make_item("CCCC3333", tags=[{"tag": "unrelated"}]),
        ]
        plan = run_expand(
            [{"op": "merge_tag", "from": ["AI", "Artificial Intelligence"], "into": "topic:ai"}],
            items=items,
        )
        assert len(plan.operations) == 2
        first = plan.operations[0]
        assert first.key == "AAAA1111" and first.version == 7 and first.facet == "tags"
        assert first.old == [{"tag": "AI", "type": 1}, {"tag": "keep-me", "type": 0}]
        assert first.new == [{"tag": "keep-me", "type": 0}, {"tag": "topic:ai", "type": 0}]
        assert first.risk == "high"

    def test_item_already_canonical_just_drops_aliases(self):
        items = [
            make_item("AAAA1111", tags=[{"tag": "AI", "type": 1}, {"tag": "topic:ai", "type": 0}])
        ]
        plan = run_expand([{"op": "merge_tag", "from": ["AI"], "into": "topic:ai"}], items=items)
        assert plan.operations[0].new == [{"tag": "topic:ai", "type": 0}]

    def test_into_must_be_canonical(self):
        assert "canonical" in failures_of(
            [{"op": "merge_tag", "from": ["ai stuff"], "into": "AI"}],
            items=[make_item("AAAA1111", tags=[{"tag": "ai stuff"}])],
        )

    def test_merging_a_canonical_tag_away_is_refused(self):
        assert "canonical" in failures_of(
            [{"op": "merge_tag", "from": ["status:read"], "into": "topic:ai"}],
            items=[make_item("AAAA1111", tags=[{"tag": "status:read"}])],
        )

    def test_requires_taxonomy(self):
        assert "taxonomy" in failures_of(
            [{"op": "merge_tag", "from": ["AI"], "into": "topic:ai"}],
            items=[make_item("AAAA1111", tags=[{"tag": "AI"}])],
            taxonomy=None,
        )


class TestAddRemoveTag:
    def test_add_tag_low_risk_manual_type(self):
        plan = run_expand(
            [{"op": "add_tag", "tag": "topic:ai", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111", version=4, tags=[{"tag": "x"}])],
        )
        op = plan.operations[0]
        assert op.new == [{"tag": "x"}, {"tag": "topic:ai", "type": 0}]
        assert op.risk == "low" and op.version == 4

    def test_add_tag_already_present_is_a_no_op(self):
        plan = run_expand(
            [{"op": "add_tag", "tag": "topic:ai", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111", tags=[{"tag": "topic:ai", "type": 0}])],
        )
        assert plan.operations == []

    def test_add_tag_refuses_alias_output(self):
        message = failures_of(
            [{"op": "add_tag", "tag": "AI", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111")],
        )
        assert "topic:ai" in message  # points at the canonical tag

    def test_add_tag_unknown_item(self):
        assert "ZZZZ9999" in failures_of(
            [{"op": "add_tag", "tag": "topic:ai", "keys": ["ZZZZ9999"]}]
        )

    def test_remove_tag_high_risk_and_absent_is_no_op(self):
        plan = run_expand(
            [
                {"op": "remove_tag", "tag": "junk", "keys": ["AAAA1111", "BBBB2222"]},
            ],
            items=[
                make_item("AAAA1111", tags=[{"tag": "junk"}, {"tag": "keep"}]),
                make_item("BBBB2222", tags=[{"tag": "keep"}]),
            ],
        )
        assert len(plan.operations) == 1
        assert plan.operations[0].risk == "high"
        assert plan.operations[0].new == [{"tag": "keep"}]


class TestExclusivity:
    def test_introducing_second_status_tag_is_refused(self):
        message = failures_of(
            [{"op": "add_tag", "tag": "status:read", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111", tags=[{"tag": "status:to-read", "type": 0}])],
        )
        assert "status" in message and "AAAA1111" in message

    def test_preexisting_violation_untouched_is_tolerated(self):
        items = [
            make_item(
                "AAAA1111",
                tags=[{"tag": "status:to-read", "type": 0}, {"tag": "status:read", "type": 0}],
            )
        ]
        plan = run_expand(
            [{"op": "add_tag", "tag": "topic:ai", "keys": ["AAAA1111"]}], items=items
        )
        assert len(plan.operations) == 1


class TestFillField:
    def test_filling_empty_field_is_low_risk(self):
        plan = run_expand(
            [{"op": "fill_field", "key": "AAAA1111", "field": "volume", "value": "12"}],
            items=[make_item("AAAA1111", version=6)],
        )
        op = plan.operations[0]
        assert op.facet == "field:volume" and op.old == "" and op.new == "12"
        assert op.risk == "low"

    def test_overwriting_is_high_risk(self):
        plan = run_expand(
            [{"op": "fill_field", "key": "AAAA1111", "field": "volume", "value": "12"}],
            items=[make_item("AAAA1111", volume="11")],
        )
        assert plan.operations[0].risk == "high"
        assert plan.operations[0].old == "11"

    def test_extra_is_never_a_target(self):
        assert "extra" in failures_of(
            [{"op": "fill_field", "key": "AAAA1111", "field": "extra", "value": "x"}],
            items=[make_item("AAAA1111")],
        )

    def test_field_checked_against_item_type_schema(self):
        assert "publisher" in failures_of(
            [{"op": "fill_field", "key": "AAAA1111", "field": "publisher", "value": "MIT"}],
            items=[make_item("AAAA1111", item_type="journalArticle")],
        )

    def test_same_value_is_a_no_op(self):
        plan = run_expand(
            [{"op": "fill_field", "key": "AAAA1111", "field": "volume", "value": "12"}],
            items=[make_item("AAAA1111", volume="12")],
        )
        assert plan.operations == []


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


class TestCitekeyGuard:
    def cited_unpinned(self, **overrides):
        return make_item("AAAA1111", DOI="10.1/x", **overrides)

    def test_title_edit_on_cited_unpinned_item_is_refused(self):
        failures = failures_of(
            [{"op": "fill_field", "key": "AAAA1111", "field": "title", "value": "New"}],
            items=[self.cited_unpinned()],
            scan=CITED,
        )
        assert "pin_citekey" in failures and "shannon1948" in failures

    def test_date_edit_likewise_but_other_fields_pass(self):
        failures = failures_of(
            [{"op": "fill_field", "key": "AAAA1111", "field": "date", "value": "1948"}],
            items=[self.cited_unpinned(date="")],
            scan=CITED,
        )
        assert "pin_citekey" in failures
        plan = run_expand(
            [{"op": "fill_field", "key": "AAAA1111", "field": "volume", "value": "27"}],
            items=[self.cited_unpinned()],
            scan=CITED,
        )
        assert len(plan.operations) == 1

    def test_pin_in_same_changeset_clears_the_guard_either_order(self):
        for intents in (
            [
                {"op": "pin_citekey", "key": "AAAA1111"},
                {"op": "fill_field", "key": "AAAA1111", "field": "title", "value": "New"},
            ],
            [
                {"op": "fill_field", "key": "AAAA1111", "field": "title", "value": "New"},
                {"op": "pin_citekey", "key": "AAAA1111"},
            ],
        ):
            plan = run_expand(intents, items=[self.cited_unpinned()], scan=CITED)
            assert len(plan.operations) == 2

    def test_already_pinned_item_edits_freely(self):
        plan = run_expand(
            [{"op": "fill_field", "key": "AAAA1111", "field": "title", "value": "New"}],
            items=[self.cited_unpinned(extra="Citation Key: shannon1948")],
            scan=CITED,
        )
        assert len(plan.operations) == 1

    def test_uncited_item_edits_freely(self):
        uncited = SourceScan(entries=[SHANNON], cited={})
        plan = run_expand(
            [{"op": "fill_field", "key": "AAAA1111", "field": "title", "value": "New"}],
            items=[self.cited_unpinned()],
            scan=uncited,
        )
        assert len(plan.operations) == 1

    def test_without_sources_the_guard_is_inert(self):
        plan = run_expand(
            [{"op": "fill_field", "key": "AAAA1111", "field": "title", "value": "New"}],
            items=[self.cited_unpinned()],
        )
        assert len(plan.operations) == 1


class TestCollectionMembership:
    def test_add_to_collection(self):
        plan = run_expand(
            [{"op": "add_to_collection", "collection": "COLL1111", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111", version=3, collections=["OTHER222"])],
            collections=[make_collection("COLL1111", "Shelf")],
        )
        op = plan.operations[0]
        assert op.facet == "collections" and op.kind == "item"
        assert op.old == ["OTHER222"] and op.new == ["OTHER222", "COLL1111"]
        assert op.risk == "low"

    def test_remove_from_collection_high_risk(self):
        plan = run_expand(
            [{"op": "remove_from_collection", "collection": "COLL1111", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111", collections=["COLL1111"])],
            collections=[make_collection("COLL1111", "Shelf")],
        )
        assert plan.operations[0].new == [] and plan.operations[0].risk == "high"

    def test_unknown_collection_refused(self):
        assert "COLL9999" in failures_of(
            [{"op": "add_to_collection", "collection": "COLL9999", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111")],
        )


class TestCollectionObjects:
    def test_create_collection_precomputes_key(self):
        plan = run_expand(
            [{"op": "create_collection", "name": "projects", "parent": "COLL1111"}],
            collections=[make_collection("COLL1111", "Root")],
        )
        op = plan.operations[0]
        assert op.kind == "collection" and op.facet == "object" and op.version == 0
        assert op.key == "NEWC0001"
        assert op.new == {"name": "projects", "parentCollection": "COLL1111"}
        assert op.risk == "low"

    def test_create_collection_parent_must_exist(self):
        assert "GONE9999" in failures_of(
            [{"op": "create_collection", "name": "projects", "parent": "GONE9999"}]
        )

    def test_rename_collection(self):
        plan = run_expand(
            [{"op": "rename_collection", "collection": "COLL1111", "name": "Better"}],
            collections=[make_collection("COLL1111", "Worse", version=5)],
        )
        op = plan.operations[0]
        assert op.facet == "name" and op.old == "Worse" and op.new == "Better"
        assert op.version == 5 and op.risk == "low"

    def test_move_collection_high_risk(self):
        plan = run_expand(
            [{"op": "move_collection", "collection": "CHLD1111", "parent": None}],
            collections=[
                make_collection("ROOT1111", "Root"),
                make_collection("CHLD1111", "Child", parent="ROOT1111"),
            ],
        )
        op = plan.operations[0]
        assert op.facet == "parentCollection" and op.old == "ROOT1111" and op.new is False
        assert op.risk == "high"

    def test_move_under_own_descendant_refused(self):
        message = failures_of(
            [{"op": "move_collection", "collection": "ROOT1111", "parent": "CHLD1111"}],
            collections=[
                make_collection("ROOT1111", "Root"),
                make_collection("CHLD1111", "Child", parent="ROOT1111"),
            ],
        )
        assert "descendant" in message

    def test_trash_collection_and_item(self):
        plan = run_expand(
            [
                {"op": "trash_collection", "collection": "COLL1111"},
                {"op": "trash_item", "key": "AAAA1111"},
            ],
            items=[make_item("AAAA1111")],
            collections=[make_collection("COLL1111", "Shelf")],
        )
        assert [(op.kind, op.facet, op.old, op.new, op.risk) for op in plan.operations] == [
            ("collection", "deleted", False, True, "high"),
            ("item", "deleted", False, True, "high"),
        ]


class TestCoalescing:
    def test_sequential_tag_ops_compose(self):
        items = [make_item("AAAA1111", version=7, tags=[{"tag": "AI", "type": 1}])]
        plan = run_expand(
            [
                {"op": "merge_tag", "from": ["AI"], "into": "topic:ai"},
                {"op": "add_tag", "tag": "status:read", "keys": ["AAAA1111"]},
            ],
            items=items,
        )
        merge, add = plan.operations
        assert merge.new == [{"tag": "topic:ai", "type": 0}]
        assert add.old == merge.new  # the second op sees the first's result
        assert add.new == [{"tag": "topic:ai", "type": 0}, {"tag": "status:read", "type": 0}]
        assert merge.version == add.version == 7  # one write, one pin

    def test_groups_index_into_intents(self):
        items = [make_item("AAAA1111", tags=[{"tag": "AI"}]), make_item("BBBB2222")]
        plan = run_expand(
            [
                {"op": "merge_tag", "from": ["AI"], "into": "topic:ai"},
                {"op": "add_tag", "tag": "status:read", "keys": ["BBBB2222"]},
            ],
            items=items,
        )
        assert [op.group for op in plan.operations] == [0, 1]
        assert [op.id for op in plan.operations] == ["op-001", "op-002"]


class TestSettingsDrift:
    def test_drift_adds_settings_entry(self):
        live = {"value": [{"name": "status:read", "color": "#009E73"}], "version": 90}
        plan = run_expand(
            [{"op": "add_tag", "tag": "topic:ai", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111")],
            settings={"tagColors": live},
            library_version=100,
        )
        assert plan.settings == {
            "name": "tagColors",
            "version": 100,
            "old": [{"name": "status:read", "color": "#009E73"}],
            "new": [
                {"name": "status:to-read", "color": "#E69F00"},
                {"name": "status:read", "color": "#009E73"},
            ],
        }

    def test_no_drift_no_entry(self):
        live = {
            "value": [
                {"name": "status:to-read", "color": "#E69F00"},
                {"name": "status:read", "color": "#009E73"},
            ],
            "version": 90,
        }
        plan = run_expand(
            [{"op": "add_tag", "tag": "topic:ai", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111")],
            settings={"tagColors": live},
        )
        assert plan.settings is None

    def test_no_taxonomy_no_entry(self):
        plan = run_expand(
            [{"op": "remove_tag", "tag": "junk", "keys": ["AAAA1111"]}],
            items=[make_item("AAAA1111", tags=[{"tag": "junk"}])],
            taxonomy=None,
        )
        assert plan.settings is None


class TestFailureAggregation:
    def test_all_failures_reported_at_once(self):
        with pytest.raises(ValidationError) as exc_info:
            run_expand(
                [
                    {"op": "add_tag", "tag": "topic:ai", "keys": ["ZZZZ9999"]},
                    {"op": "fill_field", "key": "AAAA1111", "field": "extra", "value": "x"},
                ],
                items=[make_item("AAAA1111")],
            )
        assert len(exc_info.value.failures) == 2
