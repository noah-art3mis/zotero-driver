"""Expansion: validate symbolic intents and expand them into a version-pinned plan.

Every failure is collected, never raised mid-walk — the agent gets the whole
list at once. Operations on the same object compose through a working copy of
its state, so one plan produces one write per object and the second operation's
old state is the first's new state.
"""

from __future__ import annotations

import copy
import secrets
from collections.abc import Callable
from datetime import datetime

from zelador.citekeys import CITEKEY_FIELDS, SourceScan, match_entries, pin_line, pinned_citekey
from zelador.client import ZoteroClient
from zelador.taxonomy import Taxonomy
from zelador.write.contracts import Changeset, Operation, Plan, plan_id

ZOTERO_KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"


class ValidationError(Exception):
    """One or more validation failures; nothing expands."""

    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("\n".join(failures))


def _generate_key() -> str:
    return "".join(secrets.choice(ZOTERO_KEY_ALPHABET) for _ in range(8))


def expand(
    changeset: Changeset,
    client: ZoteroClient,
    taxonomy: Taxonomy | None,
    backup: str,
    now: datetime,
    keygen: Callable[[], str] | None = None,
    scan: SourceScan | None = None,
) -> Plan:
    """Expand a changeset against the live library; raises ValidationError on any failure."""
    items = client.all_items()
    collections = client.all_collections()
    # Captured before the settings GET — a single-object request whose header
    # carries the setting's own version, not the library version.
    library_version = client.last_modified_version
    assert library_version is not None
    tag_colors = client.setting("tagColors")
    expander = _Expander(client, items, collections, taxonomy, keygen or _generate_key, scan)
    for group, intent in enumerate(changeset.intents):
        expander.expand_intent(group, intent)
    expander.check_exclusivity()
    expander.check_citekey_pins()
    if expander.failures:
        raise ValidationError(expander.failures)
    return Plan(
        id=plan_id(changeset.slug, now),
        slug=changeset.slug,
        backup=backup,
        library_version=library_version,
        intents=changeset.intents,
        operations=expander.operations,
        settings=_settings_drift(taxonomy, tag_colors, library_version),
    )


def _settings_drift(taxonomy: Taxonomy | None, live_setting, library_version: int) -> dict | None:
    """The registry is authoritative for the whole tagColors array — any difference is drift."""
    if taxonomy is None:
        return None
    live = live_setting["value"] if live_setting else []
    registry = taxonomy.tag_colors_value()
    if live == registry:
        return None
    return {"name": "tagColors", "version": library_version, "old": live, "new": registry}


class _Expander:
    def __init__(self, client, items, collections, taxonomy, keygen, scan=None):
        self.client = client
        self.taxonomy = taxonomy
        self.keygen = keygen
        self.scan = scan
        self.match = match_entries(scan.entries, items) if scan else None
        self.items = items
        self.items_by_key = {i["key"]: i for i in items}
        self.colls_by_key = {c["key"]: c for c in collections}
        self.created_keys: set[str] = set()
        self._working_items: dict[str, dict] = {}
        self._working_colls: dict[str, dict] = {}
        self.operations: list[Operation] = []
        self.failures: list[str] = []

    # -- state ---------------------------------------------------------------

    def item_state(self, key: str) -> dict:
        """The item's evolving data — ops later in the changeset see earlier results."""
        if key not in self._working_items:
            self._working_items[key] = copy.deepcopy(self.items_by_key[key]["data"])
        return self._working_items[key]

    def coll_state(self, key: str) -> dict:
        if key not in self._working_colls:
            self._working_colls[key] = copy.deepcopy(self.colls_by_key[key]["data"])
        return self._working_colls[key]

    def item_tags(self, key: str) -> list[dict]:
        if key in self._working_items:
            return self._working_items[key]["tags"]
        return self.items_by_key[key]["data"].get("tags", [])

    def emit(self, group, op, kind, key, version, facet, old, new, risk) -> None:
        operation = Operation(
            id=f"op-{len(self.operations) + 1:03d}",
            group=group,
            op=op,
            kind=kind,
            key=key,
            version=version,
            facet=facet,
            old=old,
            new=new,
            risk=risk,
        )
        self.operations.append(operation)

    def fail(self, where: str, message: str) -> None:
        self.failures.append(f"{where}: {message}")

    def require_item(self, key: str, where: str) -> dict | None:
        if key not in self.items_by_key:
            self.fail(where, f"no such item: {key}")
            return None
        return self.item_state(key)

    def require_collection(self, key: str, where: str) -> dict | None:
        if key not in self.colls_by_key:
            self.fail(where, f"no such collection: {key}")
            return None
        return self.coll_state(key)

    # -- dispatch ------------------------------------------------------------

    def expand_intent(self, group: int, intent: dict) -> None:
        op = intent["op"]
        where = f"intent {group} ({op})"
        getattr(self, f"_{op}")(group, where, intent)

    def _canonical_tag(self, tag: str, where: str) -> bool:
        """True when `tag` may be written. Aliases point at their canonical owner."""
        if self.taxonomy is None:
            self.fail(where, "writing tags requires taxonomy.yaml")
            return False
        if tag in self.taxonomy.canonical():
            return True
        owner = self.taxonomy.alias_map().get(tag)
        if owner:
            self.fail(where, f"{tag!r} is an alias — the canonical tag is {owner!r}")
        else:
            self.fail(where, f"{tag!r} is not a canonical registry tag")
        return False

    # -- tag ops -------------------------------------------------------------

    def _merge_tag(self, group: int, where: str, intent: dict) -> None:
        sources, target = intent["from"], intent["into"]
        if not self._canonical_tag(target, where):
            return
        merged_canonical = [t for t in sources if t in self.taxonomy.canonical()]
        if merged_canonical:
            self.fail(where, f"cannot merge canonical tag(s) away: {', '.join(merged_canonical)}")
            return
        source_set = set(sources)
        for item in self.items:
            key = item["key"]
            tags = self.item_tags(key)
            if not any(t["tag"] in source_set for t in tags):
                continue
            new = [t for t in tags if t["tag"] not in source_set]
            if not any(t["tag"] == target for t in new):
                new.append({"tag": target, "type": 0})
            self.emit(group, "merge_tag", "item", key, item["version"], "tags",
                      copy.deepcopy(tags), new, "high")
            self.item_state(key)["tags"] = new

    def _add_tag(self, group: int, where: str, intent: dict) -> None:
        tag = intent["tag"]
        if not self._canonical_tag(tag, where):
            return
        for key in intent["keys"]:
            if self.require_item(key, where) is None:
                continue
            tags = self.item_tags(key)
            if any(t["tag"] == tag for t in tags):
                continue
            new = [*copy.deepcopy(tags), {"tag": tag, "type": 0}]
            version = self.items_by_key[key]["version"]
            self.emit(group, "add_tag", "item", key, version, "tags",
                      copy.deepcopy(tags), new, "low")
            self.item_state(key)["tags"] = new

    def _remove_tag(self, group: int, where: str, intent: dict) -> None:
        tag = intent["tag"]
        for key in intent["keys"]:
            if self.require_item(key, where) is None:
                continue
            tags = self.item_tags(key)
            if not any(t["tag"] == tag for t in tags):
                continue
            new = [t for t in copy.deepcopy(tags) if t["tag"] != tag]
            version = self.items_by_key[key]["version"]
            self.emit(group, "remove_tag", "item", key, version, "tags",
                      copy.deepcopy(tags), new, "high")
            self.item_state(key)["tags"] = new

    # -- item ops ------------------------------------------------------------

    def _fill_field(self, group: int, where: str, intent: dict) -> None:
        key, field, value = intent["key"], intent["field"], intent["value"]
        data = self.require_item(key, where)
        if data is None:
            return
        if field == "extra":
            self.fail(where, "'extra' is never a fill_field target — pin_citekey owns it")
            return
        valid = self.client.item_type_fields(data["itemType"])
        if field not in valid:
            self.fail(where, f"{field!r} is not a field of {data['itemType']!r}")
            return
        old = data.get(field) or ""
        if old == value:
            return
        version = self.items_by_key[key]["version"]
        risk = "low" if not old else "high"
        self.emit(group, "fill_field", "item", key, version, f"field:{field}", old, value, risk)
        data[field] = value

    def _pin_citekey(self, group: int, where: str, intent: dict) -> None:
        key = intent["key"]
        data = self.require_item(key, where)
        if data is None:
            return
        if self.scan is None:
            self.fail(where, "pin_citekey needs citekey_sources configured in config.yaml")
            return
        citekey = self._resolve_citekey(key, where)
        if citekey is None:
            return
        existing = pinned_citekey(data.get("extra") or "")
        if existing == citekey:
            return
        if existing is not None:
            self.fail(
                where,
                f"item {key} already pins citekey {existing!r} — "
                f"refusing to overwrite with {citekey!r}",
            )
            return
        old = data.get("extra") or ""
        new = f"{old}\n{pin_line(citekey)}" if old else pin_line(citekey)
        version = self.items_by_key[key]["version"]
        self.emit(group, "pin_citekey", "item", key, version, "field:extra", old, new, "low")
        data["extra"] = new

    def _resolve_citekey(self, key: str, where: str) -> str | None:
        """The bib entry this item resolves to — the export is the citekey authority."""
        claims = sorted(ck for ck, item in self.match.item_for.items() if item == key)
        if len(claims) > 1:
            self.fail(
                where,
                f"item {key} is matched by multiple bib entries: {', '.join(claims)} — "
                "the bib export has duplicates",
            )
            return None
        if claims:
            return claims[0]
        collisions = sorted(ck for ck, keys in self.match.ambiguous.items() if key in keys)
        if collisions:
            others = ", ".join(self.match.ambiguous[collisions[0]])
            self.fail(
                where,
                f"bib entry {collisions[0]!r} is claimed by multiple items ({others}) — "
                "resolve the duplicates first",
            )
        else:
            self.fail(where, f"no bib entry matches item {key} by DOI or title+year")
        return None

    def _trash_item(self, group: int, where: str, intent: dict) -> None:
        key = intent["key"]
        data = self.require_item(key, where)
        if data is None or data.get("deleted"):
            return
        version = self.items_by_key[key]["version"]
        self.emit(group, "trash_item", "item", key, version, "deleted", False, True, "high")
        data["deleted"] = True

    # -- collection membership -----------------------------------------------

    def _add_to_collection(self, group: int, where: str, intent: dict) -> None:
        self._membership(group, where, intent, add=True)

    def _remove_from_collection(self, group: int, where: str, intent: dict) -> None:
        self._membership(group, where, intent, add=False)

    def _membership(self, group: int, where: str, intent: dict, add: bool) -> None:
        ckey = intent["collection"]
        if ckey not in self.colls_by_key:
            self.fail(where, f"no such collection: {ckey}")
            return
        for key in intent["keys"]:
            data = self.require_item(key, where)
            if data is None:
                continue
            memberships = data.get("collections", [])
            if add == (ckey in memberships):
                continue
            new = [*memberships, ckey] if add else [m for m in memberships if m != ckey]
            version = self.items_by_key[key]["version"]
            op = "add_to_collection" if add else "remove_from_collection"
            self.emit(group, op, "item", key, version, "collections",
                      list(memberships), new, "low" if add else "high")
            data["collections"] = new

    # -- collection objects --------------------------------------------------

    def _create_collection(self, group: int, where: str, intent: dict) -> None:
        parent = intent.get("parent")
        if parent is not None and parent not in self.colls_by_key:
            self.fail(where, f"no such parent collection: {parent}")
            return
        key = self.keygen()
        new = {"name": intent["name"], "parentCollection": parent or False}
        self.emit(group, "create_collection", "collection", key, 0, "object", None, new, "low")
        self.colls_by_key[key] = {"key": key, "version": 0, "data": {**new, "key": key}}
        self.created_keys.add(key)

    def _rename_collection(self, group: int, where: str, intent: dict) -> None:
        key = intent["collection"]
        data = self.require_collection(key, where)
        if data is None or data["name"] == intent["name"]:
            return
        version = self.colls_by_key[key]["version"]
        self.emit(group, "rename_collection", "collection", key, version, "name",
                  data["name"], intent["name"], "low")
        data["name"] = intent["name"]

    def _move_collection(self, group: int, where: str, intent: dict) -> None:
        key = intent["collection"]
        data = self.require_collection(key, where)
        if data is None:
            return
        parent = intent["parent"]
        if parent is not None:
            if parent not in self.colls_by_key:
                self.fail(where, f"no such parent collection: {parent}")
                return
            if self._is_descendant(parent, of=key):
                self.fail(where, f"{parent} is a descendant of {key} — the move would cycle")
                return
        new = parent or False
        if data["parentCollection"] == new:
            return
        version = self.colls_by_key[key]["version"]
        self.emit(group, "move_collection", "collection", key, version, "parentCollection",
                  data["parentCollection"], new, "high")
        data["parentCollection"] = new

    def _trash_collection(self, group: int, where: str, intent: dict) -> None:
        key = intent["collection"]
        data = self.require_collection(key, where)
        if data is None or data.get("deleted"):
            return
        version = self.colls_by_key[key]["version"]
        self.emit(group, "trash_collection", "collection", key, version, "deleted",
                  False, True, "high")
        data["deleted"] = True

    def _is_descendant(self, key: str, of: str) -> bool:
        """Walk parent links in working state; `of` reachable from `key` means descendant."""
        seen = set()
        current = key
        while current and current not in seen:
            seen.add(current)
            if current == of:
                return True
            coll = self._working_colls.get(current) or self.colls_by_key[current]["data"]
            current = coll.get("parentCollection") or None
        return False

    # -- cross-cutting checks ------------------------------------------------

    def check_citekey_pins(self) -> None:
        """Citekey-affecting edits on cited-but-unpinned items are refused: Better
        BibTeX would silently recompute the key every downstream citation joins on.
        A pin_citekey op anywhere in the same changeset satisfies the guard —
        checked against final working state, so intent order never matters."""
        if self.scan is None:
            return
        item_citekeys: dict[str, str] = {}
        for cited in sorted(self.scan.cited):
            matched = self.match.item_for.get(cited)
            if matched is not None:
                item_citekeys.setdefault(matched, cited)
        facets = {f"field:{name}" for name in CITEKEY_FIELDS}
        flagged: set[str] = set()
        for op in self.operations:
            if op.op != "fill_field" or op.facet not in facets or op.key in flagged:
                continue
            citekey = item_citekeys.get(op.key)
            if citekey is None:
                continue
            if pinned_citekey(self.item_state(op.key).get("extra") or "") is None:
                flagged.add(op.key)
                self.failures.append(
                    f"item {op.key}: cited as {citekey!r} but unpinned — a "
                    f"{op.facet.removeprefix('field:')} edit would recompute its citekey; "
                    "add a pin_citekey op for it in this changeset"
                )

    def check_exclusivity(self) -> None:
        """A write may not introduce a new exclusive-family violation; stock violations
        it leaves untouched are the conformance audit's business, not a veto."""
        if self.taxonomy is None:
            return
        exclusive = [f for f, spec in self.taxonomy.families.items() if spec.exclusive]
        for key, working in self._working_items.items():
            original = self.items_by_key[key]["data"].get("tags", [])
            for family in exclusive:
                before = sum(1 for t in original if t["tag"].startswith(f"{family}:"))
                after = sum(1 for t in working["tags"] if t["tag"].startswith(f"{family}:"))
                if after > 1 and after > before:
                    self.failures.append(
                        f"item {key}: would carry {after} '{family}:' tags — family is exclusive"
                    )
