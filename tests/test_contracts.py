"""Tests for the changeset.v1 and plan.v1 contracts — lint, roundtrip, plan ids."""

import json
from datetime import UTC, datetime

import pytest

from zelador.write.contracts import (
    ChangesetError,
    Operation,
    Plan,
    load_changeset,
    load_plan,
    plan_id,
    save_plan,
)

NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


def write_changeset(tmp_path, payload) -> str:
    path = tmp_path / "change.json"
    path.write_text(json.dumps(payload))
    return path


def valid_changeset(**overrides):
    payload = {
        "schema": "changeset.v1",
        "slug": "merge-ai-tags",
        "intents": [
            {"op": "merge_tag", "from": ["AI", "Artificial Intelligence"], "into": "topic:ai"},
            {"op": "add_tag", "tag": "status:read", "keys": ["AAAA1111"]},
        ],
    }
    payload.update(overrides)
    return payload


class TestChangesetLint:
    def test_valid_changeset_loads(self, tmp_path):
        changeset = load_changeset(write_changeset(tmp_path, valid_changeset()))
        assert changeset.slug == "merge-ai-tags"
        assert changeset.intents[0]["op"] == "merge_tag"

    def test_wrong_schema_refused(self, tmp_path):
        path = write_changeset(tmp_path, valid_changeset(schema="changeset.v2"))
        with pytest.raises(ChangesetError, match="changeset.v1"):
            load_changeset(path)

    def test_unknown_op_refused(self, tmp_path):
        payload = valid_changeset(intents=[{"op": "delete_item", "key": "AAAA1111"}])
        with pytest.raises(ChangesetError, match="delete_item"):
            load_changeset(write_changeset(tmp_path, payload))

    def test_missing_required_field_refused(self, tmp_path):
        payload = valid_changeset(intents=[{"op": "merge_tag", "into": "topic:ai"}])
        with pytest.raises(ChangesetError, match="from"):
            load_changeset(write_changeset(tmp_path, payload))

    def test_unknown_field_refused(self, tmp_path):
        payload = valid_changeset(
            intents=[{"op": "trash_item", "key": "AAAA1111", "reason": "dupe"}]
        )
        with pytest.raises(ChangesetError, match="reason"):
            load_changeset(write_changeset(tmp_path, payload))

    def test_empty_key_list_refused(self, tmp_path):
        payload = valid_changeset(intents=[{"op": "add_tag", "tag": "t:x", "keys": []}])
        with pytest.raises(ChangesetError, match="keys"):
            load_changeset(write_changeset(tmp_path, payload))

    def test_bad_slug_refused(self, tmp_path):
        path = write_changeset(tmp_path, valid_changeset(slug="Bad Slug!"))
        with pytest.raises(ChangesetError, match="slug"):
            load_changeset(path)

    def test_empty_intents_refused(self, tmp_path):
        path = write_changeset(tmp_path, valid_changeset(intents=[]))
        with pytest.raises(ChangesetError, match="intents"):
            load_changeset(path)

    def test_nullable_parent_accepted(self, tmp_path):
        payload = valid_changeset(
            intents=[
                {"op": "move_collection", "collection": "COLL1111", "parent": None},
                {"op": "create_collection", "name": "projects"},
            ]
        )
        changeset = load_changeset(write_changeset(tmp_path, payload))
        assert len(changeset.intents) == 2


class TestPlanRoundtrip:
    def make_plan(self) -> Plan:
        op = Operation(
            id="op-001",
            group=0,
            op="add_tag",
            kind="item",
            key="AAAA1111",
            version=12,
            facet="tags",
            old=[{"tag": "old"}],
            new=[{"tag": "old"}, {"tag": "status:read", "type": 0}],
            risk="low",
        )
        return Plan(
            id=plan_id("merge-ai-tags", NOW),
            slug="merge-ai-tags",
            backup="20260719T115900Z",
            library_version=8388,
            intents=[{"op": "add_tag", "tag": "status:read", "keys": ["AAAA1111"]}],
            operations=[op],
            settings=None,
        )

    def test_plan_id_is_stamp_and_slug(self):
        assert plan_id("merge-ai-tags", NOW) == "20260719T120000Z-merge-ai-tags"

    def test_save_load_roundtrip(self, tmp_path):
        plan = self.make_plan()
        path = save_plan(plan, tmp_path)
        assert path.name == f"{plan.id}.json"
        assert load_plan(path) == plan

    def test_wrong_schema_refused(self, tmp_path):
        path = save_plan(self.make_plan(), tmp_path)
        mangled = json.loads(path.read_text())
        mangled["schema"] = "plan.v0"
        path.write_text(json.dumps(mangled))
        with pytest.raises(ChangesetError, match="plan.v1"):
            load_plan(path)

    def test_settings_entry_roundtrips(self, tmp_path):
        plan = self.make_plan()
        plan = Plan(
            **{
                **plan.__dict__,
                "settings": {
                    "name": "tagColors",
                    "version": 8388,
                    "old": [],
                    "new": [{"name": "status:read", "color": "#009E73"}],
                },
            }
        )
        path = save_plan(plan, tmp_path)
        assert load_plan(path).settings["name"] == "tagColors"
