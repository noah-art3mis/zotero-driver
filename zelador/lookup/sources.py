"""Crossref and arXiv lookups — candidates with scores, the agent judges the match.

Scores are a 0–1 similarity between the item's current title and the
candidate's, computed locally so they are deterministic and comparable
across sources; a direct DOI hit scores 1.0 outright.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import SequenceMatcher

import httpx

from zelador.audit.duplicates import normalize_doi
from zelador.lookup.cache import LookupCache

CROSSREF_API = "https://api.crossref.org"
ARXIV_API = "https://export.arxiv.org/api/query"
MAX_RESULTS = 5
USER_AGENT = "zelador (https://github.com/noah-art3mis/zotero-driver)"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_YEAR = re.compile(r"\d{4}")
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


class SourceError(Exception):
    """A lookup could not be made or answered — operational failure, exit 1."""


@dataclass(frozen=True)
class Candidate:
    source: str
    score: float
    doi: str
    title: str
    creators: list[str]
    year: str
    container: str
    volume: str
    issue: str
    pages: str
    publisher: str
    url: str


class Web:
    """Cache-through HTTP: every URL is fetched at most once, forever."""

    def __init__(
        self, cache: LookupCache, transport: httpx.BaseTransport | None = None, timeout=30.0
    ):
        self.cache = cache
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def get(self, url: str, params: dict | None = None) -> str:
        full = str(httpx.URL(url, params=params))
        cached = self.cache.get(full)
        if cached is not None:
            return cached
        try:
            response = self._http.get(full)
        except httpx.HTTPError as exc:
            raise SourceError(f"GET {full} failed: {exc}") from None
        if response.status_code >= 400:
            raise SourceError(f"HTTP {response.status_code} on GET {full}")
        self.cache.put(full, response.text)
        return response.text


def _similarity(a: str, b: str) -> float:
    def normalize(s: str) -> str:
        return _NON_ALNUM.sub(" ", s.lower()).strip()

    return round(SequenceMatcher(None, normalize(a), normalize(b)).ratio(), 3)


def _item_year(data: dict) -> str:
    found = _YEAR.search(data.get("date", "") or "")
    return found.group(0) if found else ""


def _first_author(data: dict) -> str:
    for creator in data.get("creators", []):
        if creator.get("creatorType") == "author":
            return creator.get("lastName", "") or creator.get("name", "")
    return ""


def _require_title(data: dict) -> str:
    title = data.get("title", "") or ""
    if not title:
        raise SourceError("item has no title to search by")
    return title


# -- Crossref ----------------------------------------------------------------


def crossref(data: dict, web: Web) -> list[Candidate]:
    """Direct /works/<doi> when the item has a DOI, else a bibliographic query."""
    doi = normalize_doi(data.get("DOI", "") or "")
    if doi:
        message = json.loads(web.get(f"{CROSSREF_API}/works/{doi}"))["message"]
        return [_crossref_candidate(message, score=1.0)]
    title = _require_title(data)
    query = " ".join(filter(None, [title, _first_author(data), _item_year(data)]))
    body = json.loads(
        web.get(f"{CROSSREF_API}/works", {"query.bibliographic": query, "rows": MAX_RESULTS})
    )
    works = body.get("message", {}).get("items", [])
    candidates = [_crossref_candidate(w, score=_similarity(title, _one(w, "title"))) for w in works]
    return sorted(candidates, key=lambda c: (-c.score, c.doi))


def _one(work: dict, field: str) -> str:
    values = work.get(field) or []
    return values[0] if values else ""


def _crossref_candidate(work: dict, score: float) -> Candidate:
    parts = (work.get("issued", {}).get("date-parts") or [[None]])[0]
    return Candidate(
        source="crossref",
        score=score,
        doi=normalize_doi(work.get("DOI", "") or ""),
        title=_one(work, "title"),
        creators=[
            ", ".join(filter(None, [a.get("family", ""), a.get("given", "")]))
            for a in work.get("author", [])
        ],
        year=str(parts[0]) if parts and parts[0] else "",
        container=_one(work, "container-title"),
        volume=work.get("volume", "") or "",
        issue=work.get("issue", "") or "",
        pages=work.get("page", "") or "",
        publisher=work.get("publisher", "") or "",
        url=work.get("URL", "") or "",
    )


# -- arXiv -------------------------------------------------------------------


def arxiv(data: dict, web: Web) -> list[Candidate]:
    """Title search over the arXiv Atom API."""
    title = _require_title(data)
    body = web.get(
        ARXIV_API, {"search_query": f'ti:"{title}"', "max_results": MAX_RESULTS, "start": 0}
    )
    try:
        feed = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceError(f"arXiv returned unparseable XML: {exc}") from None
    candidates = []
    for entry in feed.findall(f"{_ATOM}entry"):
        entry_title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        candidates.append(
            Candidate(
                source="arxiv",
                score=_similarity(title, entry_title),
                doi=normalize_doi(entry.findtext(f"{_ARXIV}doi") or ""),
                title=entry_title,
                creators=[
                    author.findtext(f"{_ATOM}name") or ""
                    for author in entry.findall(f"{_ATOM}author")
                ],
                year=(entry.findtext(f"{_ATOM}published") or "")[:4],
                container="",
                volume="",
                issue="",
                pages="",
                publisher="",
                url=entry.findtext(f"{_ATOM}id") or "",
            )
        )
    return sorted(candidates, key=lambda c: (-c.score, c.url))
