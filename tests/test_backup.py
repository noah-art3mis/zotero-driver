"""Tests for zelador.backup — full-library JSONL snapshots and the conditional no-op."""

import json
from datetime import UTC, datetime

from tests.conftest import FakeZotero, make_collection, make_item
from zelador import backup
from zelador.client import ZoteroClient
from zelador.config import Credentials

CREDS = Credentials(api_key="k", user_id="11868292")
NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


def library() -> FakeZotero:
    return FakeZotero(
        items=[
            make_item("AAAA1111", tags=[{"tag": "status:read"}, {"tag": "ai"}]),
            make_item("BBBB2222", tags=[{"tag": "ai"}]),
            make_item("TRSH0001", deleted=True),
        ],
        collections=[make_collection("CCCC0001", "projects")],
        settings={
            "tagColors": {"value": [{"name": "status:read", "color": "#009E73"}], "version": 5}
        },
        library_version=42,
        page_size=100,
    )


def client_for(fake: FakeZotero) -> ZoteroClient:
    return ZoteroClient(CREDS, transport=fake.transport, sleep=lambda s: None)


class TestRunBackup:
    def test_writes_items_collections_and_tagcolors(self, tmp_path):
        fake = library()
        path = backup.run_backup(client_for(fake), tmp_path, now=NOW)
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        kinds = [line["kind"] for line in lines]
        assert kinds[0] == "header"
        assert kinds.count("item") == 3  # trashed item included
        assert kinds.count("collection") == 1
        assert kinds.count("setting") == 1
        header = lines[0]
        assert header["library_version"] == 42
        assert any("includeTrashed=1" in str(r.url) for r in fake.requests)

    def test_noop_when_library_unchanged(self, tmp_path):
        fake = library()
        first = backup.run_backup(client_for(fake), tmp_path, now=NOW)
        assert first is not None
        again = backup.run_backup(client_for(fake), tmp_path, now=NOW)
        assert again is None
        assert len(list(tmp_path.glob("*.jsonl"))) == 1
        conditional = [r for r in fake.requests if "If-Modified-Since-Version" in r.headers]
        assert conditional

    def test_new_backup_when_library_moved(self, tmp_path):
        fake = library()
        backup.run_backup(client_for(fake), tmp_path, now=NOW)
        fake.library_version = 50
        second = backup.run_backup(
            client_for(fake), tmp_path, now=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
        )
        assert second is not None
        assert len(list(tmp_path.glob("*.jsonl"))) == 2

    def test_missing_tagcolors_writes_no_setting_line(self, tmp_path):
        fake = library()
        fake.settings = {}
        path = backup.run_backup(client_for(fake), tmp_path, now=NOW)
        kinds = [json.loads(line)["kind"] for line in path.read_text().splitlines()]
        assert "setting" not in kinds


class TestLatestBackup:
    def test_none_when_no_backups(self, tmp_path):
        assert backup.latest_backup(tmp_path) is None

    def test_returns_newest_with_header_fields(self, tmp_path):
        fake = library()
        backup.run_backup(client_for(fake), tmp_path, now=NOW)
        fake.library_version = 50
        backup.run_backup(
            client_for(fake), tmp_path, now=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
        )
        info = backup.latest_backup(tmp_path)
        assert info.library_version == 50
        assert info.path.name.startswith("20260720")

    def test_stats_count_items_and_distinct_tags(self, tmp_path):
        fake = library()
        path = backup.run_backup(client_for(fake), tmp_path, now=NOW)
        info = backup.latest_backup(tmp_path)
        stats = backup.backup_stats(info.path)
        assert path == info.path
        assert stats.items == 3
        assert stats.tags == 2  # status:read + ai, deduplicated
