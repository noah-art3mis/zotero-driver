"""Audit check 1: metadata completeness, judged against APA 7 citation needs per item type."""

from __future__ import annotations

from zelador.audit.library import Library, finding
from zelador.output import strip_html

# Required fields per Zotero itemType, on top of creators/date/title required
# everywhere. Calibrated to what APA 7 asks per type (SPEC → Audit check 1).
APA_RULES: dict[str, list[str]] = {
    "journalArticle": ["publicationTitle", "volume", "issue", "pages", "DOI"],
    "preprint": ["repository"],  # arXiv id or DOI checked separately
    "book": ["publisher"],
    "bookSection": ["bookTitle", "pages", "publisher"],  # editors checked separately
    "conferencePaper": ["proceedingsTitle", "pages", "DOI"],
    "newspaperArticle": ["publicationTitle"],
    "magazineArticle": ["publicationTitle"],
    "blogPost": ["blogTitle", "url"],
    "webpage": ["websiteTitle", "url"],
    "videoRecording": ["url"],
    "podcast": ["url"],
    "report": ["institution", "reportNumber"],
    "thesis": ["university", "thesisType"],
}

# Types APA cites with a full year-month-day date.
EXACT_DATE_TYPES = {
    "newspaperArticle",
    "magazineArticle",
    "blogPost",
    "webpage",
    "videoRecording",
    "podcast",
}

SKIPPED_TYPES = {"note", "annotation"}


def check(library: Library) -> list[dict]:
    findings = []
    for item in library.items:
        data = item["data"]
        item_type = data["itemType"]
        if item_type in SKIPPED_TYPES:
            continue
        if item_type == "attachment":
            if not data.get("parentItem"):
                findings.append(
                    finding(
                        "completeness",
                        "standalone_attachment",
                        [item["key"]],
                        f"standalone attachment, invisible to bibliographies: "
                        f"{data.get('title', '')}",
                    )
                )
            continue
        if item_type not in APA_RULES:
            findings.append(
                finding(
                    "completeness",
                    "no_rule",
                    [item["key"]],
                    f"item type '{item_type}' has no completeness rule — add one",
                )
            )
            continue
        missing = _missing_fields(item, item_type)
        bib = strip_html(item.get("bib", ""))
        if missing:
            findings.append(
                finding(
                    "completeness",
                    "incomplete",
                    [item["key"]],
                    f"{data.get('title', '')!r} missing: {', '.join(missing)}",
                    missing=missing,
                    bib=bib,
                )
            )
        elif "bib" in item and _looks_broken(bib):
            findings.append(
                finding(
                    "completeness",
                    "broken_render",
                    [item["key"]],
                    f"{data.get('title', '')!r} renders visibly broken: {bib!r}",
                    bib=bib,
                )
            )
    return findings


def _missing_fields(item: dict, item_type: str) -> list[str]:
    data = item["data"]
    missing = [name for name in ("title", "date") if not data.get(name)]
    if not data.get("creators"):
        missing.append("creators")
    missing += [name for name in APA_RULES[item_type] if not data.get(name)]
    if item_type == "preprint" and not (data.get("DOI") or data.get("archiveID")):
        missing.append("DOI or archiveID")
    if item_type == "bookSection":
        creators = data.get("creators") or []
        if not any(c.get("creatorType") == "editor" for c in creators):
            missing.append("editors")
    if item_type in EXACT_DATE_TYPES and data.get("date"):
        parsed = item.get("meta", {}).get("parsedDate", "")
        if len(parsed) < 10:
            missing.append("exact date (year-month-day)")
    return missing


def _looks_broken(bib: str) -> bool:
    return not bib or "(n.d.)" in bib
