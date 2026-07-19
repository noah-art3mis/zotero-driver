"""Recovery utilities: zel debug reconcile and zel debug restore.

Reconcile resolves a crashed apply's pending entries deterministically: fetch
the touched objects, compare against each entry's logged old and new state,
mark it applied or failed accordingly. Restore is layer 1's named path — push
the backed-up JSON of exactly the named objects back through the standard
write machinery, deliberately unpinned to the backup's versions because
last-resort recovery overwrites whatever state the accident left.
"""

from __future__ import annotations

import json
from datetime import datetime
from itertools import batched
from pathlib import Path

from zelador.client import BATCH_SIZE, ZoteroClient
from zelador.write.apply import ApplyOutcome
from zelador.write.changelog import SessionLog, read_log
from zelador.write.undo import facet_value


class RestoreError(Exception):
    """Recovery could not start — nothing was written."""


def run_reconcile(session: str, client: ZoteroClient, log_dir: Path) -> dict:
    path = log_dir / f"{session}.jsonl"
    if not path.exists():
        raise RestoreError(f"no session log named {session} in {log_dir}")
    _, entries = read_log(path)
    pending = [entry for entry in entries.values() if entry.status == "pending"]
    log = SessionLog(path)
    counts = {"applied": 0, "failed": 0}
    current = _fetch_current(client, pending)
    for entry in pending:
        op = entry.operation
        landed, version = _landed(client, current, op)
        if landed:
            log.resolve(op["id"], "applied", version)
            counts["applied"] += 1
        else:
            log.resolve(op["id"], "failed")
            counts["failed"] += 1
    return counts


def _landed(client, current, op) -> tuple[bool, int | None]:
    if op["kind"] == "setting":
        live = client.setting(op["key"])
        return ((live["value"] if live else []) == op["new"], live["version"] if live else None)
    obj = current.get((op["kind"], op["key"]))
    if obj is None:
        return False, None
    if op["facet"] == "object":
        data, created = obj["data"], op["new"]
        landed = (
            data.get("name") == created["name"]
            and data.get("parentCollection", False) == created["parentCollection"]
        )
        return landed, obj["version"]
    return facet_value(obj["data"], op["facet"]) == op["new"], obj["version"]


def _fetch_current(client, entries) -> dict[tuple[str, str], dict]:
    item_keys = {e.operation["key"] for e in entries if e.operation["kind"] == "item"}
    coll_keys = {e.operation["key"] for e in entries if e.operation["kind"] == "collection"}
    current: dict[tuple[str, str], dict] = {}
    if item_keys:
        for obj in client.items_batch(sorted(item_keys), include_trashed=True):
            current[("item", obj["key"])] = obj
    if coll_keys:
        for obj in client.collections_batch(sorted(coll_keys)):
            current[("collection", obj["key"])] = obj
    return current


def run_restore(
    backup_path: Path, keys: list[str], client: ZoteroClient, log_dir: Path, now: datetime
) -> ApplyOutcome:
    objects = _read_backup(backup_path)
    missing = [key for key in keys if key not in objects]
    if missing:
        raise RestoreError(f"key(s) not in backup {backup_path.name}: {', '.join(missing)}")
    current = _fetch_named(client, keys, objects)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    session = f"{stamp}-restore"
    log = SessionLog(log_dir / f"{session}.jsonl")
    log.start(plan=session, backup=backup_path.stem, timestamp=stamp)
    outcome = ApplyOutcome(log_path=str(log.path))
    counter = iter(range(1, len(keys) + 1))
    for kind, write_fn in (("collection", client.write_collections), ("item", client.write_items)):
        kind_keys = [key for key in keys if objects[key][0] == kind]
        writes = []
        for key in kind_keys:
            data = dict(objects[key][1])
            live = current.get(key)
            data["key"] = key
            data["version"] = live["version"] if live else 0  # current, never the backup's pin
            writes.append(
                (
                    kind,
                    data,
                    [
                        {
                            "id": f"op-{next(counter):03d}",
                            "kind": kind,
                            "key": key,
                            "version": data["version"],
                            "facet": "object",
                            "old": live["data"] if live else None,
                            "new": objects[key][1],
                            "risk": "high",
                        }
                    ],
                )
            )
        for chunk in batched(writes, BATCH_SIZE):
            _execute_chunk(write_fn, log, list(chunk), outcome)
    return outcome


def _execute_chunk(write_fn, log, chunk, outcome: ApplyOutcome) -> None:
    log.pending([op for _, _, ops in chunk for op in ops])
    result = write_fn([write for _, write, _ in chunk])
    for _, write, ops in chunk:
        key = write["key"]
        if key in result.applied:
            for op in ops:
                log.resolve(op["id"], "applied", result.applied[key])
            outcome.applied += len(ops)
        elif key in result.unchanged:
            for op in ops:
                log.resolve(op["id"], "unchanged", result.unchanged[key])
            outcome.unchanged += len(ops)
        else:
            error = result.failed.get(key) or {"code": None, "message": "no per-object result"}
            for op in ops:
                log.resolve(op["id"], "failed")
            outcome.failed += len(ops)
            outcome.failures.append({"key": key, **error})


def _read_backup(backup_path: Path) -> dict[str, tuple[str, dict]]:
    """key -> (kind, data) for every item and collection in the backup file."""
    try:
        handle = backup_path.open()
    except FileNotFoundError:
        raise RestoreError(f"no such backup: {backup_path}") from None
    objects: dict[str, tuple[str, dict]] = {}
    with handle:
        for raw in handle:
            line = json.loads(raw)
            if line["kind"] in ("item", "collection"):
                obj = line["object"]
                objects[obj["key"]] = (line["kind"], obj["data"])
    return objects


def _fetch_named(client, keys, objects) -> dict[str, dict]:
    item_keys = [key for key in keys if objects[key][0] == "item"]
    coll_keys = [key for key in keys if objects[key][0] == "collection"]
    current: dict[str, dict] = {}
    if item_keys:
        for obj in client.items_batch(item_keys, include_trashed=True):
            current[obj["key"]] = obj
    if coll_keys:
        for obj in client.collections_batch(coll_keys):
            current[obj["key"]] = obj
    return current
