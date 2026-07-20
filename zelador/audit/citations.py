"""Audit check 5: citekey integrity — the join the writing pipeline depends on.

Only cited citekeys are examined: orphaned citations (no bib entry — evidence
of past drift), entries that no longer resolve to an item, and the hazard set
proper — cited items whose citekey is unpinned, one metadata edit away from
silently recomputing.
"""

from __future__ import annotations

from zelador.audit.library import Library, finding
from zelador.citekeys import SourceScan, match_entries, pinned_citekey


def check(library: Library, scan: SourceScan) -> list[dict]:
    match = match_entries(scan.entries, library.items)
    bib_keys = scan.bib_keys()
    items_by_key = {i["key"]: i for i in library.items}
    findings = []
    for citekey, files in scan.cited.items():
        if citekey not in bib_keys:
            findings.append(
                finding(
                    "citekeys",
                    "orphaned_citation",
                    [],
                    f"'{citekey}' is cited in {len(files)} file(s) but matches no bib entry",
                    citekey=citekey,
                    files=files,
                )
            )
        elif citekey in match.ambiguous:
            keys = match.ambiguous[citekey]
            findings.append(
                finding(
                    "citekeys",
                    "ambiguous_entry",
                    keys,
                    f"bib entry '{citekey}' is claimed by {len(keys)} items — "
                    "resolve the duplicates before pinning",
                    citekey=citekey,
                )
            )
        elif citekey not in match.item_for:
            findings.append(
                finding(
                    "citekeys",
                    "unmatched_entry",
                    [],
                    f"cited bib entry '{citekey}' matches no item by DOI or title+year",
                    citekey=citekey,
                )
            )
        else:
            key = match.item_for[citekey]
            extra = items_by_key[key]["data"].get("extra") or ""
            if pinned_citekey(extra) is None:
                findings.append(
                    finding(
                        "citekeys",
                        "cited_unpinned",
                        [key],
                        f"item {key} is cited as '{citekey}' but its citekey is not pinned — "
                        "a metadata edit would recompute it",
                        citekey=citekey,
                        files=files,
                    )
                )
    return findings
