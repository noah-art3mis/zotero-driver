"""Citekey sources — where the downstream reference workflow enters the tool.

The configured `.bib` export is the authority for current citekeys (Better
BibTeX recomputes unpinned keys, so only its export knows them); every other
source is a file glob whose matches are scanned for citekey uses: `[[@key]]`
wiki-links, `\\cite`/`\\citep`/`\\citet{...}` commands, and `@key.md` literature-note
basenames. Bib entries match back to items by DOI, falling back to normalized
title+year.
"""

from __future__ import annotations

import glob as globlib
import re
from dataclasses import dataclass
from pathlib import Path

from zelador.audit.duplicates import normalize_doi
from zelador.audit.library import NON_BIBLIOGRAPHIC
from zelador.config import ConfigError

_ENTRY_START = re.compile(r"@(\w+)\s*\{")
_NON_ENTRIES = {"comment", "preamble", "string"}
_FIELD = re.compile(r"(\w+)\s*=\s*")
_WIKI_LINK = re.compile(r"\[\[@([^\][|#]+?)(?:[|#][^\]]*)?\]\]")
_TEX_CITE = re.compile(r"\\cite[pt]?\*?(?:\[[^\]]*\]){0,2}\{([^{}]*)\}")
_NOTE_BASENAME = re.compile(r"^@(.+)\.md$")
_PIN_LINE = re.compile(r"^\s*citation key\s*:\s*(\S+)\s*$", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_YEAR = re.compile(r"\d{4}")


@dataclass(frozen=True)
class BibEntry:
    citekey: str
    doi: str  # normalized; "" when absent
    title: str  # raw, outer braces stripped
    year: str  # "" when absent


@dataclass(frozen=True)
class SourceScan:
    """Parsed citekey sources: bib entries plus every citekey use found."""

    entries: list[BibEntry]
    cited: dict[str, list[str]]  # citekey -> sorted files citing it

    def bib_keys(self) -> set[str]:
        return {e.citekey for e in self.entries}


@dataclass(frozen=True)
class Match:
    """Resolution of bib entries against the library, keyed by citekey."""

    item_for: dict[str, str]  # unique matches: citekey -> item key
    ambiguous: dict[str, list[str]]  # citekey -> the several items claiming it
    unmatched: list[str]  # citekeys whose entry matched no item


# -- pin detection -----------------------------------------------------------


def pinned_citekey(extra: str) -> str | None:
    """The citekey pinned by a `Citation Key:` line in `extra`, if any."""
    for line in extra.splitlines():
        found = _PIN_LINE.match(line)
        if found:
            return found.group(1)
    return None


# -- bib parsing -------------------------------------------------------------


def parse_bib(text: str) -> list[BibEntry]:
    """Entry keys plus the DOI/title/year fields matching needs. Regex-and-brace
    parsing calibrated to machine-generated exports, not hand-written BibTeX."""
    entries = []
    for started in _ENTRY_START.finditer(text):
        if started.group(1).lower() in _NON_ENTRIES:
            continue
        body, _ = _balanced(text, started.end() - 1)
        citekey, _, fields_text = body.partition(",")
        citekey = citekey.strip()
        if not citekey:
            continue
        fields = _parse_fields(fields_text)
        year = fields.get("year", "") or fields.get("date", "")
        year_match = _YEAR.search(year)
        entries.append(
            BibEntry(
                citekey=citekey,
                doi=normalize_doi(fields.get("doi", "")),
                title=fields.get("title", ""),
                year=year_match.group(0) if year_match else "",
            )
        )
    return entries


def _balanced(text: str, brace_at: int) -> tuple[str, int]:
    """Content of the brace group opening at `brace_at`, and the index after it."""
    depth = 0
    for i in range(brace_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_at + 1 : i], i + 1
    return text[brace_at + 1 :], len(text)


def _parse_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    at = 0
    while True:
        found = _FIELD.search(body, at)
        if not found:
            return fields
        name, at = found.group(1).lower(), found.end()
        if at >= len(body):
            return fields
        if body[at] == "{":
            value, at = _balanced(body, at)
        elif body[at] == '"':
            end = body.find('"', at + 1)
            end = end if end != -1 else len(body)
            value, at = body[at + 1 : end], end + 1
        else:
            end = body.find(",", at)
            end = end if end != -1 else len(body)
            value, at = body[at:end], end
        fields.setdefault(name, value.strip())


# -- citekey use scanning ----------------------------------------------------


def scan_text(text: str) -> set[str]:
    """Citekeys used in one file's text: wiki-links and TeX cite commands."""
    found = {match.group(1) for match in _WIKI_LINK.finditer(text)}
    for match in _TEX_CITE.finditer(text):
        found.update(key.strip() for key in match.group(1).split(",") if key.strip())
    return found


def scan_sources(sources: list[str]) -> SourceScan:
    """Load the configured sources: parse `.bib` paths, glob-scan everything else."""
    entries: list[BibEntry] = []
    cited: dict[str, set[str]] = {}
    for source in sources:
        pattern = str(Path(source).expanduser())
        if source.endswith(".bib"):
            for path in _glob_files(pattern, source):
                entries.extend(parse_bib(path.read_text(encoding="utf-8", errors="replace")))
            continue
        for path in _glob_files(pattern, source, allow_empty=True):
            used = scan_text(path.read_text(encoding="utf-8", errors="replace"))
            noted = _NOTE_BASENAME.match(path.name)
            if noted:
                used.add(noted.group(1))
            for citekey in used:
                cited.setdefault(citekey, set()).add(str(path))
    return SourceScan(
        entries=entries, cited={key: sorted(files) for key, files in sorted(cited.items())}
    )


def _glob_files(pattern: str, source: str, allow_empty: bool = False) -> list[Path]:
    paths = [Path(p) for p in sorted(globlib.glob(pattern, recursive=True)) if Path(p).is_file()]
    if not paths and not allow_empty:
        raise ConfigError(f"citekey source matches no such file: {source}")
    return paths


# -- matching entries back to items ------------------------------------------


def _title_year_signature(title: str, year: str) -> str | None:
    normalized = _NON_ALNUM.sub(" ", title.replace("{", "").replace("}", "").lower()).strip()
    if not normalized or not year:
        return None
    return f"{normalized}|{year}"


def _item_year(item: dict) -> str:
    date = item.get("meta", {}).get("parsedDate") or item["data"].get("date", "")
    found = _YEAR.search(date)
    return found.group(0) if found else ""


def match_entries(entries: list[BibEntry], items: list[dict]) -> Match:
    """Match each entry to its library item by DOI, falling back to title+year."""
    by_doi: dict[str, list[str]] = {}
    by_signature: dict[str, list[str]] = {}
    for item in items:
        data = item["data"]
        if data["itemType"] in NON_BIBLIOGRAPHIC:
            continue
        doi = normalize_doi(data.get("DOI", "") or "")
        if doi:
            by_doi.setdefault(doi, []).append(item["key"])
        signature = _title_year_signature(data.get("title", ""), _item_year(item))
        if signature:
            by_signature.setdefault(signature, []).append(item["key"])

    item_for: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for entry in entries:
        candidates = by_doi.get(entry.doi, []) if entry.doi else []
        if not candidates:
            signature = _title_year_signature(entry.title, entry.year)
            candidates = by_signature.get(signature, []) if signature else []
        if not candidates:
            unmatched.append(entry.citekey)
        elif len(candidates) == 1:
            item_for[entry.citekey] = candidates[0]
        else:
            ambiguous[entry.citekey] = sorted(candidates)
    return Match(item_for=item_for, ambiguous=ambiguous, unmatched=unmatched)
