"""Tests for zelador.local — SQLite snapshot, integrity gate, read-only agent SQL."""

import sqlite3

import pytest

from zelador import local


@pytest.fixture
def zotero_dir(tmp_path):
    d = tmp_path / "Zotero"
    d.mkdir()
    conn = sqlite3.connect(d / "zotero.sqlite")
    conn.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT)")
    conn.execute("INSERT INTO items VALUES (1, 'AAAA1111')")
    conn.commit()
    conn.close()
    return d


class TestSnapshot:
    def test_copies_and_queries(self, zotero_dir, tmp_path):
        snap = local.snapshot_database(zotero_dir, tmp_path / "cache")
        cols, rows = local.query(snap, "SELECT key FROM items")
        assert cols == ["key"]
        assert rows == [("AAAA1111",)]

    def test_wal_sibling_copied_so_recent_writes_visible(self, zotero_dir, tmp_path):
        conn = sqlite3.connect(zotero_dir / "zotero.sqlite")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO items VALUES (2, 'BBBB2222')")
        conn.commit()
        # desktop app still running: snapshot while the connection holds the WAL open
        snap = local.snapshot_database(zotero_dir, tmp_path / "cache")
        conn.close()
        _, rows = local.query(snap, "SELECT count(*) FROM items")
        assert rows == [(2,)]

    def test_recopies_fresh_every_run(self, zotero_dir, tmp_path):
        snap = local.snapshot_database(zotero_dir, tmp_path / "cache")
        conn = sqlite3.connect(zotero_dir / "zotero.sqlite")
        conn.execute("INSERT INTO items VALUES (3, 'CCCC3333')")
        conn.commit()
        conn.close()
        snap = local.snapshot_database(zotero_dir, tmp_path / "cache")
        _, rows = local.query(snap, "SELECT count(*) FROM items")
        assert rows == [(2,)]

    def test_torn_copy_fails_loudly(self, zotero_dir, tmp_path):
        (zotero_dir / "zotero.sqlite").write_bytes(b"this is not a database")
        with pytest.raises(local.LocalError, match="integrity|database"):
            local.snapshot_database(zotero_dir, tmp_path / "cache")

    def test_missing_database_fails_loudly(self, tmp_path):
        empty = tmp_path / "NotZotero"
        empty.mkdir()
        with pytest.raises(local.LocalError, match="zotero.sqlite"):
            local.snapshot_database(empty, tmp_path / "cache")


class TestQuery:
    def test_writes_refused(self, zotero_dir, tmp_path):
        snap = local.snapshot_database(zotero_dir, tmp_path / "cache")
        with pytest.raises(local.LocalError, match="read-only|attempt to write"):
            local.query(snap, "INSERT INTO items VALUES (9, 'EVIL')")

    def test_bad_sql_fails_loudly(self, zotero_dir, tmp_path):
        snap = local.snapshot_database(zotero_dir, tmp_path / "cache")
        with pytest.raises(local.LocalError, match="no such table"):
            local.query(snap, "SELECT * FROM nonexistent")
