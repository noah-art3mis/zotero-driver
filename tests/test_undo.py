"""Tests for zel undo — backwards replay with current-state verification."""

import pytest

from tests.conftest import USER_ID, FakeZotero, make_collection, make_item
from zelador.client import ZoteroClient
from zelador.config import Credentials
from zelador.write.changelog import SessionLog, read_log
from zelador.write.undo import UndoRefused, run_undo

SESSION = "20260719T120000Z-test-change"


def client_for(fake: FakeZotero) -> ZoteroClient:
    return ZoteroClient(
        Credentials(api_key="k", user_id=USER_ID), transport=fake.transport, sleep=lambda s: None
    )


def write_session(log_dir, entries):
    """A finished apply session: every entry pending then resolved."""
    log = SessionLog(log_dir / f"{SESSION}.jsonl")
    log.start(plan=SESSION, backup="20260719T115900Z", timestamp="20260719T120100Z")
    for operation, status, version in entries:
        log.pending([operation])
        log.resolve(operation["id"], status, version)
    return log.path


def op_dict(op_id, key, facet, old, new, kind="item", version=1):
    return {
        "id": op_id, "group": 0, "op": "x", "kind": kind, "key": key,
        "version": version, "facet": facet, "old": old, "new": new, "risk": "low",
    }


class TestRefusals:
    def test_missing_session(self, tmp_path):
        with pytest.raises(UndoRefused, match="no session"):
            run_undo("nope", client_for(FakeZotero()), tmp_path)

    def test_pending_entries_refused(self, tmp_path):
        log = SessionLog(tmp_path / f"{SESSION}.jsonl")
        log.start(plan=SESSION, backup="b", timestamp="t")
        log.pending([op_dict("op-001", "AAAA1111", "tags", [], [])])
        with pytest.raises(UndoRefused, match="reconcile"):
            run_undo(SESSION, client_for(FakeZotero()), tmp_path)


class TestReplay:
    def test_reverses_applied_entry(self, tmp_path):
        # Post-apply state: tag was added at version 5; undo strips it back.
        fake = FakeZotero(
            items=[make_item("AAAA1111", version=5, tags=[{"tag": "t:x", "type": 0}])],
            library_version=5,
        )
        write_session(
            tmp_path,
            [(op_dict("op-001", "AAAA1111", "tags", [], [{"tag": "t:x", "type": 0}], version=1),
              "applied", 5)],
        )
        outcome = run_undo(SESSION, client_for(fake), tmp_path)
        assert outcome.undone == 1 and outcome.conflicts == []
        assert fake.items[0]["data"]["tags"] == []
        _, entries = read_log(tmp_path / f"{SESSION}.jsonl")
        assert entries["op-001"].status == "undone"
        assert entries["op-001"].version == 6

    def test_drifted_state_is_a_conflict_left_untouched(self, tmp_path):
        # The tag was edited after the apply — current state no longer equals
        # the logged new state, so undo reports and does not touch it.
        drifted = [{"tag": "t:x", "type": 0}, {"tag": "later-edit", "type": 0}]
        fake = FakeZotero(items=[make_item("AAAA1111", version=9, tags=drifted)])
        write_session(
            tmp_path,
            [(op_dict("op-001", "AAAA1111", "tags", [], [{"tag": "t:x", "type": 0}], version=1),
              "applied", 5)],
        )
        outcome = run_undo(SESSION, client_for(fake), tmp_path)
        assert outcome.undone == 0
        assert any("AAAA1111" in c for c in outcome.conflicts)
        assert fake.items[0]["data"]["tags"] == drifted
        _, entries = read_log(tmp_path / f"{SESSION}.jsonl")
        assert entries["op-001"].status == "applied"  # story not rewritten

    def test_coalesced_entries_undo_to_first_old(self, tmp_path):
        # merge then add on one item: verify against the LAST new, restore the FIRST old.
        fake = FakeZotero(
            items=[
                make_item(
                    "AAAA1111",
                    version=5,
                    tags=[{"tag": "topic:ai", "type": 0}, {"tag": "status:read", "type": 0}],
                )
            ]
        )
        first_old = [{"tag": "AI", "type": 1}]
        mid = [{"tag": "topic:ai", "type": 0}]
        final = [{"tag": "topic:ai", "type": 0}, {"tag": "status:read", "type": 0}]
        write_session(
            tmp_path,
            [
                (op_dict("op-001", "AAAA1111", "tags", first_old, mid, version=1), "applied", 5),
                (op_dict("op-002", "AAAA1111", "tags", mid, final, version=1), "applied", 5),
            ],
        )
        outcome = run_undo(SESSION, client_for(fake), tmp_path)
        assert outcome.undone == 2
        assert fake.items[0]["data"]["tags"] == first_old

    def test_create_collection_undone_by_trashing(self, tmp_path):
        fake = FakeZotero(
            collections=[make_collection("NEWC0001", "archive", version=5)], library_version=5
        )
        created = {"name": "archive", "parentCollection": False}
        write_session(
            tmp_path,
            [(op_dict("op-001", "NEWC0001", "object", None, created,
                      kind="collection", version=0), "applied", 5)],
        )
        outcome = run_undo(SESSION, client_for(fake), tmp_path)
        assert outcome.undone == 1
        assert fake.collections[0]["data"]["deleted"] is True  # trashed, never purged

    def test_created_then_renamed_collection_still_undoes(self, tmp_path):
        # One plan created a collection and renamed it — the coalesced write
        # carried the final name, so verification must use the composed state,
        # not the creation-time state.
        fake = FakeZotero(
            collections=[make_collection("NEWC0001", "renamed", version=5)], library_version=5
        )
        created = {"name": "archive", "parentCollection": False}
        write_session(
            tmp_path,
            [
                (op_dict("op-001", "NEWC0001", "object", None, created,
                         kind="collection", version=0), "applied", 5),
                (op_dict("op-002", "NEWC0001", "name", "archive", "renamed",
                         kind="collection", version=0), "applied", 5),
            ],
        )
        outcome = run_undo(SESSION, client_for(fake), tmp_path)
        assert outcome.conflicts == []
        assert outcome.undone == 2
        assert fake.collections[0]["data"]["deleted"] is True

    def test_trash_item_undone_by_untrashing(self, tmp_path):
        fake = FakeZotero(items=[make_item("AAAA1111", version=5, deleted=True)])
        write_session(
            tmp_path,
            [(op_dict("op-001", "AAAA1111", "deleted", False, True, version=1), "applied", 5)],
        )
        outcome = run_undo(SESSION, client_for(fake), tmp_path)
        assert outcome.undone == 1
        assert not fake.items[0]["data"]["deleted"]

    def test_settings_entry_restored(self, tmp_path):
        new_value = [{"name": "status:read", "color": "#009E73"}]
        old_value = [{"name": "ler", "color": "#FF0000"}]
        fake = FakeZotero(settings={"tagColors": {"value": new_value, "version": 5}})
        entry = {
            "id": "settings", "kind": "setting", "key": "tagColors", "facet": "setting",
            "version": 4, "old": old_value, "new": new_value,
        }
        write_session(tmp_path, [(entry, "applied", 5)])
        outcome = run_undo(SESSION, client_for(fake), tmp_path)
        assert outcome.undone == 1
        assert fake.settings["tagColors"]["value"] == old_value

    def test_only_applied_entries_replay(self, tmp_path):
        fake = FakeZotero(items=[make_item("AAAA1111", version=5, tags=[])])
        write_session(
            tmp_path,
            [
                (op_dict("op-001", "AAAA1111", "tags", [{"tag": "a"}], [], version=1),
                 "failed", None),
                (op_dict("op-002", "AAAA1111", "field:volume", "", "7", version=1),
                 "unchanged", 1),
            ],
        )
        outcome = run_undo(SESSION, client_for(fake), tmp_path)
        assert outcome.undone == 0 and outcome.conflicts == []
        assert [r for r in fake.requests if r.method == "POST"] == []

    def test_dry_run_verifies_but_writes_nothing(self, tmp_path):
        fake = FakeZotero(
            items=[make_item("AAAA1111", version=5, tags=[{"tag": "t:x", "type": 0}])]
        )
        write_session(
            tmp_path,
            [(op_dict("op-001", "AAAA1111", "tags", [], [{"tag": "t:x", "type": 0}], version=1),
              "applied", 5)],
        )
        outcome = run_undo(SESSION, client_for(fake), tmp_path, dry_run=True)
        assert outcome.undone == 1  # would undo
        assert fake.items[0]["data"]["tags"] == [{"tag": "t:x", "type": 0}]
        assert [r for r in fake.requests if r.method in ("POST", "PUT")] == []
        _, entries = read_log(tmp_path / f"{SESSION}.jsonl")
        assert entries["op-001"].status == "applied"

    def test_second_undo_finds_nothing(self, tmp_path):
        fake = FakeZotero(
            items=[make_item("AAAA1111", version=5, tags=[{"tag": "t:x", "type": 0}])]
        )
        write_session(
            tmp_path,
            [(op_dict("op-001", "AAAA1111", "tags", [], [{"tag": "t:x", "type": 0}], version=1),
              "applied", 5)],
        )
        run_undo(SESSION, client_for(fake), tmp_path)
        again = run_undo(SESSION, client_for(fake), tmp_path)
        assert again.undone == 0 and again.conflicts == []
