"""Tests for the client's write layer — batch writes, per-object result maps, settings, schema."""

import httpx
import pytest

from tests.conftest import USER_ID, FakeZotero, make_collection, make_item
from zelador.client import ZoteroClient, ZoteroError
from zelador.config import Credentials

CREDS = Credentials(api_key="topsecret123", user_id=USER_ID)


def client_for(fake: FakeZotero, **kwargs) -> ZoteroClient:
    kwargs.setdefault("sleep", lambda s: None)
    return ZoteroClient(CREDS, transport=fake.transport, **kwargs)


class TestWriteItems:
    def test_partial_update_merges_and_bumps_version(self):
        fake = FakeZotero(items=[make_item("AAAA1111", version=3, volume="1")], library_version=10)
        client = client_for(fake)
        result = client.write_items([{"key": "AAAA1111", "version": 3, "volume": "2"}])
        assert result.applied == {"AAAA1111": 11}
        assert fake.items[0]["data"]["volume"] == "2"
        assert fake.items[0]["data"]["title"] == "A title"  # untouched fields preserved
        assert fake.items[0]["version"] == 11

    def test_chunks_at_50(self):
        items = [make_item(f"K{i:03d}", version=1) for i in range(120)]
        fake = FakeZotero(items=items)
        client = client_for(fake)
        result = client.write_items(
            [{"key": f"K{i:03d}", "version": 1, "volume": str(i)} for i in range(120)]
        )
        assert len(result.applied) == 120
        posts = [r for r in fake.requests if r.method == "POST"]
        assert len(posts) == 3

    def test_version_conflict_lands_in_failed_by_key(self):
        fake = FakeZotero(items=[make_item("STALE111", version=9)], library_version=10)
        client = client_for(fake)
        result = client.write_items([{"key": "STALE111", "version": 3, "volume": "2"}])
        assert result.applied == {}
        assert result.failed["STALE111"]["code"] == 412
        assert fake.items[0]["data"].get("volume") is None  # nothing written

    def test_no_op_write_reported_unchanged(self):
        fake = FakeZotero(items=[make_item("SAME1111", version=3, volume="1")], library_version=10)
        client = client_for(fake)
        result = client.write_items([{"key": "SAME1111", "version": 3, "volume": "1"}])
        assert result.unchanged == {"SAME1111": 3}
        assert result.applied == {}
        assert fake.library_version == 10  # unchanged writes do not bump the library

    def test_failure_messages_are_redacted(self):
        fake = FakeZotero(items=[make_item("AAAA1111", version=9)])
        fake.script.append(
            httpx.Response(
                200,
                json={
                    "success": {},
                    "unchanged": {},
                    "failed": {"0": {"code": 400, "message": "bad key topsecret123 leaked"}},
                },
                headers={"Last-Modified-Version": "42"},
            )
        )
        client = client_for(fake)
        result = client.write_items([{"key": "AAAA1111", "version": 9, "volume": "2"}])
        assert "topsecret123" not in result.failed["AAAA1111"]["message"]


class TestWriteCollections:
    def test_update_and_create(self):
        fake = FakeZotero(collections=[make_collection("COLL1111", "Old name", version=4)])
        client = client_for(fake)
        result = client.write_collections(
            [
                {"key": "COLL1111", "version": 4, "name": "New name"},
                {"key": "NEWC2222", "version": 0, "name": "Fresh", "parentCollection": False},
            ]
        )
        assert set(result.applied) == {"COLL1111", "NEWC2222"}
        by_key = {c["key"]: c for c in fake.collections}
        assert by_key["COLL1111"]["data"]["name"] == "New name"
        assert by_key["NEWC2222"]["data"]["name"] == "Fresh"


class TestWriteSetting:
    def test_writes_value_version_pinned(self):
        fake = FakeZotero(library_version=42)
        client = client_for(fake)
        value = [{"name": "status:read", "color": "#009E73"}]
        new_version = client.write_setting("tagColors", value, if_unmodified_since=42)
        assert new_version == 43
        assert fake.settings["tagColors"]["value"] == value
        put = [r for r in fake.requests if r.method == "PUT"][0]
        assert put.headers["If-Unmodified-Since-Version"] == "42"

    def test_stale_version_fails_loudly(self):
        fake = FakeZotero(library_version=42)
        client = client_for(fake)
        with pytest.raises(ZoteroError, match="412"):
            client.write_setting("tagColors", [], if_unmodified_since=40)


class TestItemTypeFields:
    def test_fetches_and_caches_per_session(self):
        fake = FakeZotero()
        client = client_for(fake)
        fields = client.item_type_fields("journalArticle")
        assert "volume" in fields and "DOI" in fields
        client.item_type_fields("journalArticle")
        assert len([r for r in fake.requests if "itemTypeFields" in str(r.url)]) == 1

    def test_unknown_type_fails_loudly(self):
        fake = FakeZotero()
        client = client_for(fake)
        with pytest.raises(ZoteroError, match="400"):
            client.item_type_fields("notAType")


class TestBatchReadsForWrites:
    def test_collections_batch_by_key(self):
        fake = FakeZotero(
            collections=[make_collection(f"C{i:03d}AAAA", f"c{i}") for i in range(60)],
            page_size=100,
        )
        client = client_for(fake)
        found = client.collections_batch([f"C{i:03d}AAAA" for i in range(60)])
        assert len(found) == 60
        gets = [r for r in fake.requests if "collectionKey" in str(r.url)]
        assert len(gets) == 2  # chunked at 50

    def test_items_batch_can_include_trashed(self):
        fake = FakeZotero(items=[make_item("GONE1111", deleted=True)])
        client = client_for(fake)
        assert client.items_batch(["GONE1111"]) == []
        found = client.items_batch(["GONE1111"], include_trashed=True)
        assert [i["key"] for i in found] == ["GONE1111"]
