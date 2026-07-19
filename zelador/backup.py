"""Pre-session snapshots: full-library JSONL dumps, the last-resort restore source.

Layer 1 of the safety model. Each backup holds every item (trash included),
every collection object (names and parent links live only there), and the
tagColors setting, one JSON object per line under a header line carrying the
library version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from zelador.client import ZoteroClient


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    timestamp: str
    library_version: int


@dataclass(frozen=True)
class BackupStats:
    items: int
    collections: int
    tags: int


def run_backup(client: ZoteroClient, backups_dir: Path, now: datetime | None = None) -> Path | None:
    """Snapshot the library; a verified no-op (None) when nothing changed since the last one."""
    previous = latest_backup(backups_dir)
    items = client.all_items(
        include_trashed=True,
        if_modified_since=previous.library_version if previous else None,
    )
    if items is None:
        return None
    version = client.last_modified_version
    collections = client.all_collections()
    tag_colors = client.setting("tagColors")

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    path = backups_dir / f"{stamp}.jsonl"
    lines = [{"kind": "header", "library_version": version, "timestamp": stamp}]
    lines += [{"kind": "item", "object": item} for item in items]
    lines += [{"kind": "collection", "object": coll} for coll in collections]
    if tag_colors is not None:
        lines.append({"kind": "setting", "name": "tagColors", "object": tag_colors})

    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
    tmp.rename(path)
    return path


def latest_backup(backups_dir: Path) -> BackupInfo | None:
    """The newest backup's identity, read from its filename and header line."""
    candidates = sorted(backups_dir.glob("*.jsonl"))
    if not candidates:
        return None
    path = candidates[-1]
    with path.open() as handle:
        header = json.loads(handle.readline())
    return BackupInfo(
        path=path, timestamp=header["timestamp"], library_version=header["library_version"]
    )


def backup_stats(path: Path) -> BackupStats:
    """Item/collection/distinct-tag counts from one backup file."""
    items = collections = 0
    tags: set[str] = set()
    with path.open() as handle:
        for raw in handle:
            line = json.loads(raw)
            if line["kind"] == "item":
                items += 1
                tags.update(t["tag"] for t in line["object"]["data"].get("tags", []))
            elif line["kind"] == "collection":
                collections += 1
    return BackupStats(items=items, collections=collections, tags=len(tags))
