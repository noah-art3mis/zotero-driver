"""The changeset.v1 and plan.v1 contracts.

Changesets are symbolic intents in a closed operation vocabulary; plans are
their expansion into exact version-pinned per-object operations. Both are
linted structurally here — semantic validation against the live library and
the taxonomy happens in expand.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Closed operation vocabulary: op -> {field: type spec}. A trailing "?" marks
# an optional field; specs are "str", "str_list" (non-empty), "str_or_null",
# "str_map" (non-empty, str -> non-empty str), "creator_list" (Zotero creator
# objects). Deletes are unrepresentable by construction — trash_* is the only
# removal.
OPS: dict[str, dict[str, str]] = {
    "merge_tag": {"from": "str_list", "into": "str"},
    "add_tag": {"tag": "str", "keys": "str_list"},
    "remove_tag": {"tag": "str", "keys": "str_list"},
    "fill_field": {"key": "str", "field": "str", "value": "str"},
    "clear_field": {"key": "str", "field": "str"},
    "set_creators": {"key": "str", "creators": "creator_list"},
    "set_item_type": {"key": "str", "itemType": "str"},
    "create_item": {
        "itemType": "str",
        "fields": "str_map",
        "creators": "creator_list?",
        "tags": "str_list?",
        "collections": "str_list?",
        "attachment": "str?",
    },
    "add_to_collection": {"collection": "str", "keys": "str_list"},
    "remove_from_collection": {"collection": "str", "keys": "str_list"},
    "create_collection": {"name": "str", "parent": "str_or_null?"},
    "rename_collection": {"collection": "str", "name": "str"},
    "move_collection": {"collection": "str", "parent": "str_or_null"},
    "trash_collection": {"collection": "str"},
    "trash_item": {"key": "str"},
}


class ChangesetError(Exception):
    """Structurally defective changeset or plan file — bad input, exit 2."""


@dataclass(frozen=True)
class Changeset:
    slug: str
    intents: list[dict]


@dataclass(frozen=True)
class Operation:
    """One expanded write facet of one intent group, pinned to an object version."""

    id: str
    group: int  # index into the plan's intents
    op: str
    kind: str  # "item" | "collection"
    key: str
    version: int  # pinned object version; 0 = create
    facet: str  # "tags" | "collections" | "field:<name>" | "name" | "parentCollection" | "deleted" | "object"  # noqa: E501
    old: object
    new: object
    risk: str  # "low" | "high"


@dataclass(frozen=True)
class Plan:
    id: str
    slug: str
    backup: str  # timestamp id of the backup this plan was validated against
    library_version: int  # library version at validation time
    intents: list[dict]
    operations: list[Operation]
    settings: dict | None  # tagColors drift entry: {name, version, old, new}


def load_changeset(path: Path) -> Changeset:
    """Parse and structurally lint a changeset.v1 file."""
    raw = _read_json(path)
    if raw.get("schema") != "changeset.v1":
        raise ChangesetError(f"{path}: schema must be 'changeset.v1', got {raw.get('schema')!r}")
    slug = raw.get("slug")
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise ChangesetError(f"{path}: slug must be lowercase kebab-case, got {slug!r}")
    intents = raw.get("intents")
    if not isinstance(intents, list) or not intents:
        raise ChangesetError(f"{path}: intents must be a non-empty list")
    for index, intent in enumerate(intents):
        _lint_intent(intent, f"{path}: intent {index}")
    return Changeset(slug=slug, intents=intents)


def _lint_intent(intent, where: str) -> None:
    if not isinstance(intent, dict):
        raise ChangesetError(f"{where}: must be a mapping, got {intent!r}")
    op = intent.get("op")
    if op not in OPS:
        raise ChangesetError(f"{where}: unknown op {op!r} — allowed: {', '.join(sorted(OPS))}")
    spec = OPS[op]
    fields = {name.rstrip("?") for name in spec}
    unknown = set(intent) - fields - {"op"}
    if unknown:
        raise ChangesetError(f"{where}: unknown field(s) on {op}: {', '.join(sorted(unknown))}")
    for name, kind in spec.items():
        optional = name.endswith("?") or kind.endswith("?")
        field_name = name.rstrip("?")
        if field_name not in intent:
            if optional:
                continue
            raise ChangesetError(f"{where}: {op} requires field {field_name!r}")
        _check_type(intent[field_name], kind.rstrip("?"), f"{where}: {op}.{field_name}")


def _check_type(value, kind: str, where: str) -> None:
    if kind == "str":
        if not isinstance(value, str) or not value:
            raise ChangesetError(f"{where} must be a non-empty string, got {value!r}")
    elif kind == "str_or_null":
        if value is not None and (not isinstance(value, str) or not value):
            raise ChangesetError(f"{where} must be a non-empty string or null, got {value!r}")
    elif kind == "str_list" and (
        not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value)
    ):
        raise ChangesetError(f"{where} must be a non-empty list of strings, got {value!r}")
    elif kind == "str_map" and (
        not isinstance(value, dict)
        or not value
        or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items())
    ):
        raise ChangesetError(
            f"{where} must be a non-empty mapping of non-empty strings, got {value!r}"
        )
    elif kind == "creator_list":
        if not isinstance(value, list) or not value:
            raise ChangesetError(f"{where} must be a non-empty list of creators, got {value!r}")
        for creator in value:
            _check_creator(creator, where)


def _check_creator(creator, where: str) -> None:
    """One Zotero creator: creatorType plus either a single `name` or
    firstName/lastName — never both forms, never other keys."""
    if not isinstance(creator, dict):
        raise ChangesetError(f"{where}: creator must be a mapping, got {creator!r}")
    unknown = set(creator) - {"creatorType", "name", "firstName", "lastName"}
    if unknown:
        raise ChangesetError(f"{where}: unknown creator key(s): {', '.join(sorted(unknown))}")
    if not isinstance(creator.get("creatorType"), str) or not creator["creatorType"]:
        raise ChangesetError(f"{where}: creator requires a non-empty creatorType")
    has_name = "name" in creator
    has_parts = "firstName" in creator or "lastName" in creator
    if has_name == has_parts:
        raise ChangesetError(
            f"{where}: creator takes either name or firstName/lastName, got {creator!r}"
        )
    for field in ("name", "firstName", "lastName"):
        if field in creator and (not isinstance(creator[field], str) or not creator[field]):
            raise ChangesetError(f"{where}: creator {field} must be a non-empty string")


def plan_id(slug: str, now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{slug}"


def save_plan(plan: Plan, plans_dir: Path) -> Path:
    path = plans_dir / f"{plan.id}.json"
    payload = {"schema": "plan.v1", **asdict(plan)}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def load_plan(path: Path) -> Plan:
    raw = _read_json(path)
    if raw.get("schema") != "plan.v1":
        raise ChangesetError(f"{path}: schema must be 'plan.v1', got {raw.get('schema')!r}")
    raw.pop("schema")
    raw["operations"] = [Operation(**op) for op in raw["operations"]]
    return Plan(**raw)


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise ChangesetError(f"no such file: {path}") from None
    except json.JSONDecodeError as exc:
        raise ChangesetError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ChangesetError(f"{path} must hold a JSON object")
    return raw
