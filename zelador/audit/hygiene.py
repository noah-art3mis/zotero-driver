"""Audit check 3: collection hygiene — unfiled items, empty/duplicate/orphaned collections."""

from __future__ import annotations

from collections import defaultdict

from zelador.audit.library import Library, finding


def check(library: Library) -> list[dict]:
    findings = []
    by_key = {c["key"]: c["data"] for c in library.collections}
    membership_counts: dict[str, int] = defaultdict(int)
    for item in library.regular_items():
        member_of = item["data"].get("collections") or []
        for coll_key in member_of:
            membership_counts[coll_key] += 1
        if not member_of:
            findings.append(
                finding(
                    "collections",
                    "unfiled_item",
                    [item["key"]],
                    f"in no collection: {item['data'].get('title', '')}",
                )
            )

    children: dict = defaultdict(list)
    for key, data in by_key.items():
        children[data.get("parentCollection") or False].append(key)

    for key, data in sorted(by_key.items()):
        if not membership_counts[key] and not children[key]:
            findings.append(
                finding(
                    "collections", "empty_collection", [key], f"empty collection: {data['name']}"
                )
            )
        parent = data.get("parentCollection")
        if parent and parent not in by_key:
            findings.append(
                finding(
                    "collections",
                    "orphaned_subtree",
                    [key],
                    f"parent {parent} does not exist: {data['name']}",
                )
            )

    for _parent, sibling_keys in sorted(children.items(), key=lambda pair: str(pair[0])):
        names: dict = defaultdict(list)
        for key in sibling_keys:
            names[by_key[key]["name"].casefold()].append(key)
        for name, keys in sorted(names.items()):
            if len(keys) > 1:
                findings.append(
                    finding(
                        "collections",
                        "duplicate_siblings",
                        sorted(keys),
                        f"duplicate sibling collections named {name!r}",
                    )
                )
    return findings
