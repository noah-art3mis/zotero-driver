"""Audit check 4: duplicate items — same DOI, or near-identical title + year."""

from __future__ import annotations

import re
from collections import defaultdict

from zelador.audit.library import Library, finding

_DOI_PREFIX = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_doi(raw: str) -> str:
    return _DOI_PREFIX.sub("", raw.strip()).lower()


def _title_year(item: dict) -> str | None:
    title = _NON_ALNUM.sub(" ", item["data"].get("title", "").lower()).strip()
    date = item.get("meta", {}).get("parsedDate") or item["data"].get("date", "")
    year = date[:4]
    if not title or not year.isdigit():
        return None
    return f"{title}|{year}"


def check(library: Library) -> list[dict]:
    findings = []
    by_doi: dict[str, list] = defaultdict(list)
    by_title_year: dict[str, list] = defaultdict(list)
    for item in library.regular_items():
        doi = normalize_doi(item["data"].get("DOI", "") or "")
        if doi:
            by_doi[doi].append(item)
        signature = _title_year(item)
        if signature:
            by_title_year[signature].append(item)

    doi_grouped: set[str] = set()
    for doi, group in sorted(by_doi.items()):
        if len(group) > 1:
            keys = sorted(i["key"] for i in group)
            doi_grouped.update(keys)
            findings.append(
                finding("duplicates", "same_doi", keys, f"{len(group)} items share DOI {doi}")
            )
    for signature, group in sorted(by_title_year.items()):
        keys = sorted(i["key"] for i in group)
        if len(group) > 1 and not set(keys) <= doi_grouped:
            title, year = signature.rsplit("|", 1)
            findings.append(
                finding(
                    "duplicates",
                    "same_title_year",
                    keys,
                    f"{len(group)} items titled {title!r} from {year}",
                )
            )
    return findings
