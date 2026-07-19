"""`zel local`: raw read-only SQL over a fresh snapshot of Zotero's own SQLite database.

The desktop app writes the live file continuously, so every run recopies the
database with its -wal/-journal siblings and gates on PRAGMA integrity_check —
a torn copy fails loudly, never answers.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

SIBLING_SUFFIXES = ("-wal", "-journal", "-shm")


class LocalError(Exception):
    """Snapshot or query failure against the local replica."""


def snapshot_database(zotero_dir: Path, dest_dir: Path) -> Path:
    """Recopy zotero.sqlite (+ siblings) into dest_dir and verify the copy's integrity."""
    source = zotero_dir / "zotero.sqlite"
    if not source.exists():
        raise LocalError(f"zotero.sqlite not found in {zotero_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "zotero.sqlite"
    for stale in dest_dir.glob("zotero.sqlite*"):
        stale.unlink()
    shutil.copy2(source, dest)
    for suffix in SIBLING_SUFFIXES:
        sibling = Path(str(source) + suffix)
        if sibling.exists():
            shutil.copy2(sibling, Path(str(dest) + suffix))
    _verify_integrity(dest)
    return dest


def _verify_integrity(db: Path) -> None:
    try:
        conn = sqlite3.connect(db)  # read-write on our private copy, so WAL replay works
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise LocalError(f"snapshot failed integrity check: {exc}") from None
    if result != ("ok",):
        raise LocalError(f"snapshot failed integrity check: {result}")


def query(db: Path, sql: str) -> tuple[list[str], list[tuple]]:
    """Run one read-only SQL statement; returns (column names, rows)."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA query_only=ON")
        try:
            cursor = conn.execute(sql)
            rows = cursor.fetchall()
        except sqlite3.Error as exc:
            message = str(exc)
            if "readonly" in message or "query_only" in message:
                message = f"read-only: {message}"
            raise LocalError(message) from None
        columns = [d[0] for d in cursor.description or []]
        return columns, rows
    finally:
        conn.close()
