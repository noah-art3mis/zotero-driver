"""Audit check 2: tag mess — near/case-duplicate clusters, automatic vs manual."""

from __future__ import annotations

import re
from collections import defaultdict

from zelador.audit.library import Library, finding

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(tag: str) -> str:
    return _NON_ALNUM.sub(" ", tag.lower()).strip()


def check(library: Library) -> list[dict]:
    clusters: dict[str, list] = defaultdict(list)
    for tag in library.tags:
        clusters[normalize(tag["tag"])].append(tag)
    findings = []
    for _key, members in sorted(clusters.items()):
        if len(members) < 2:
            continue
        tags = [
            {
                "tag": t["tag"],
                "type": "auto" if t.get("meta", {}).get("type") == 1 else "manual",
                "numItems": t.get("meta", {}).get("numItems", 0),
            }
            for t in members
        ]
        names = ", ".join(t["tag"] for t in tags)
        findings.append(
            finding(
                "tags", "near_duplicate_cluster", [], f"probable duplicates: {names}", tags=tags
            )
        )
    return findings
