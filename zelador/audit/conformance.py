"""Audit check 6: registry conformance — the steady-state check backing intake.

Runs only when a taxonomy is loaded: unregistered tags, untriaged top-level
items (no `status:` tag), exclusive-family violations, and drift between the
registry's colours and the library's live tagColors setting.
"""

from __future__ import annotations

from collections import Counter

from zelador.audit.library import Library, finding
from zelador.taxonomy import Taxonomy


def check(library: Library, taxonomy: Taxonomy) -> list[dict]:
    findings = []
    for tag in library.tags:
        name = tag["tag"]
        if taxonomy.is_known(name):
            continue
        meta = tag.get("meta", {})
        findings.append(
            finding(
                "registry",
                "unknown_tag",
                [],
                f"unregistered tag: {name}",
                tag=name,
                numItems=meta.get("numItems", 0),
                type="auto" if meta.get("type") == 1 else "manual",
            )
        )

    top_level = [i for i in library.regular_items() if not i["data"].get("parentItem")]
    for item in top_level:
        names = [t["tag"] for t in item["data"].get("tags", [])]
        by_family = Counter(n.split(":", 1)[0] for n in names if ":" in n)
        if "status" in taxonomy.families and not by_family.get("status"):
            findings.append(
                finding(
                    "registry",
                    "untriaged",
                    [item["key"]],
                    f"no status: tag: {item['data'].get('title', '')}",
                )
            )
        for family, count in sorted(by_family.items()):
            spec = taxonomy.families.get(family)
            if spec and spec.exclusive and count > 1:
                carried = ", ".join(n for n in names if n.startswith(f"{family}:"))
                findings.append(
                    finding(
                        "registry",
                        "exclusive_violation",
                        [item["key"]],
                        f"{count} {family}: tags on one item: {carried}",
                    )
                )

    registry_colors = taxonomy.tag_colors_value()
    if registry_colors != library.tag_colors:
        findings.append(
            finding(
                "registry",
                "tag_colors_drift",
                [],
                "registry colours differ from the library's tagColors setting",
                registry=registry_colors,
                library=library.tag_colors,
            )
        )
    return findings
