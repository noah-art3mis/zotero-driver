"""Shared object-state authority for expansion, apply, undo, and recovery.

One vocabulary for turning a group of operations into an object's composed data
(`compose`, built on the per-op `apply_facet` overlay) and for asking whether
live server data matches an expected state (`matches`). Every caller that
writes, verifies, undoes, or reconciles routes its facet<->field mapping and
serialization knowledge through here, so those rules live in exactly one place.
"""

from __future__ import annotations

from collections.abc import Iterable

from zelador.client import ZoteroClient

# Object-data fields whose absence has a non-None canonical value. Every other
# field (title, volume, any field:*) is a scalar: absent reads back as None, and
# a value written "" reads back absent, so None and "" compare equal.
_LIST_FIELDS = ("tags", "collections", "creators")
_FLAG_FIELDS = ("parentCollection", "parentItem")


def facet_field(facet: str) -> str:
    """The object-data field a facet writes to ("field:volume" -> "volume")."""
    return facet.removeprefix("field:") if facet.startswith("field:") else facet


def apply_facet(data: dict, facet: str, value) -> None:
    """Overlay one operation's value onto object data in place. The `object`
    facet carries a whole object payload; every other facet names one field."""
    if facet == "object":
        data.update(value)
    else:
        data[facet_field(facet)] = value


def compose(facet_values: Iterable[tuple[str, object]]) -> dict:
    """The object-data dict a group of operations composes to, overlaid in order
    (last value per field wins, mirroring one-write-per-object apply)."""
    data: dict = {}
    for facet, value in facet_values:
        apply_facet(data, facet, value)
    return data


def matches(live: dict, expected: dict) -> bool:
    """Does live object data equal every field of an expected object state?
    Pure field equality under the server's serialization — trash policy and
    undo preconditions live in the callers, not here."""
    for field, value in expected.items():
        current = _normalized(field, live.get(field))
        if current is None and value == "":
            continue  # the server drops a field written as the empty string
        if not _field_equal(field, current, value):
            return False
    return True


def _normalized(field: str, value):
    """A live field value under the server's serialization: absent list fields
    are [], absent parent links are False, `deleted` is a bool."""
    if field in _LIST_FIELDS:
        return value or []
    if field in _FLAG_FIELDS:
        return value if value is not None else False
    if field == "deleted":
        return bool(value)
    return value


def _field_equal(field: str, live, expected) -> bool:
    """One field's live value against an expected value. An item's tags are a
    set — Zotero re-sorts them and omits a manual tag's type, so neither order
    nor an absent type 0 distinguishes two states."""
    if field == "tags":
        return sorted((t["tag"], t.get("type", 0)) for t in live) == sorted(
            (t["tag"], t.get("type", 0)) for t in expected
        )
    return live == expected


def setting_value(setting: dict | None) -> list:
    """A settings GET result flattened to its comparable value; unset means []."""
    return setting["value"] if setting else []


def fetch_objects(
    client: ZoteroClient, item_keys: list[str], coll_keys: list[str]
) -> dict[tuple[str, str], dict]:
    """Exactly the named objects (trash included), keyed by (kind, key)."""
    current: dict[tuple[str, str], dict] = {}
    if item_keys:
        for obj in client.items_batch(sorted(item_keys), include_trashed=True):
            current[("item", obj["key"])] = obj
    if coll_keys:
        for obj in client.collections_batch(sorted(coll_keys)):
            current[("collection", obj["key"])] = obj
    return current
