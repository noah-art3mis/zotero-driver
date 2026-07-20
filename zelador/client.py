"""Thin Zotero Web API v3 client on httpx.

Musts from SPEC: pagination via Link headers, 50-key batch reads and writes
with per-object success/unchanged/failed maps, Backoff/Retry-After honoured
including on 200s, real 429 retries, explicit timeouts, user-scoped endpoints
only, API-key redaction on every error path.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import batched

import httpx

from zelador.config import Credentials

API_BASE = "https://api.zotero.org"
PAGE_LIMIT = 100
BATCH_SIZE = 50
MAX_RETRIES = 5


class ZoteroError(Exception):
    """API or transport failure, message guaranteed free of the API key."""


@dataclass(frozen=True)
class WriteResult:
    """Merged per-object outcome of a batch write, keyed by object key."""

    applied: dict[str, int] = field(default_factory=dict)  # key -> resulting version
    unchanged: dict[str, int] = field(default_factory=dict)  # key -> pinned version
    failed: dict[str, dict] = field(default_factory=dict)  # key -> {code, message}


class ZoteroClient:
    def __init__(
        self,
        creds: Credentials,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        trace: Callable[[str], None] | None = None,
    ):
        self._api_key = creds.api_key
        self._prefix = f"/users/{creds.user_id}"
        self._sleep = sleep
        self._trace = trace
        self._pending_backoff = 0.0
        self._field_cache: dict[str, set[str]] = {}
        self.last_modified_version: int | None = None
        self._http = httpx.Client(
            base_url=API_BASE,
            headers={"Zotero-API-Key": creds.api_key, "Zotero-API-Version": "3"},
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def _redact(self, message: str) -> str:
        return message.replace(self._api_key, "***")

    def _announce(self, message: str) -> None:
        print(f"zelador: {message}", file=sys.stderr)

    def _request(
        self, method: str, url: str, params=None, headers=None, json=None
    ) -> httpx.Response:
        if self._pending_backoff:
            self._announce(f"server asked for backoff — waiting {self._pending_backoff:g}s")
            self._sleep(self._pending_backoff)
            self._pending_backoff = 0.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._http.request(
                    method, url, params=params, headers=headers, json=json
                )
            except httpx.HTTPError as exc:
                raise ZoteroError(self._redact(f"{method} {url} failed: {exc}")) from None
            if self._trace:
                self._trace(
                    f"{method} {url} -> {response.status_code} "
                    f"(version {response.headers.get('Last-Modified-Version', '-')})"
                )
            if response.status_code in (429, 503) and attempt < MAX_RETRIES:
                wait = float(response.headers.get("Retry-After", 2**attempt))
                self._announce(f"HTTP {response.status_code} — retrying in {wait:g}s")
                self._sleep(wait)
                continue
            break
        if "Backoff" in response.headers:
            self._pending_backoff = float(response.headers["Backoff"])
        if "Last-Modified-Version" in response.headers:
            self.last_modified_version = int(response.headers["Last-Modified-Version"])
        if response.status_code >= 400 and response.status_code != 404:
            raise ZoteroError(
                self._redact(
                    f"HTTP {response.status_code} on {method} {url}: {response.text[:200]}"
                )
            )
        return response

    def _get(self, url: str, params=None, headers=None) -> httpx.Response:
        response = self._request("GET", url, params=params, headers=headers)
        if response.status_code == 404:
            raise ZoteroError(self._redact(f"HTTP 404 on GET {url}: {response.text[:200]}"))
        return response

    def _paginated(self, path: str, params=None, headers=None) -> list | None:
        """Follow Link: next until exhausted. None means 304 Not Modified.

        A full page with no next link is not trusted as the end: Zotero's
        pagination metadata can lie (/tags under-reports Total-Results and
        drops the next link early), so the client probes the next offset
        until a short page proves the listing is exhausted.
        """
        url = path
        params = dict(params or {})
        params.setdefault("limit", PAGE_LIMIT)
        results: list = []
        while True:
            response = self._request("GET", url, params=params, headers=headers)
            if response.status_code == 304:
                return None
            if response.status_code == 404:
                raise ZoteroError(f"HTTP 404 on GET {url}")
            page = response.json()
            results.extend(page)
            headers = None
            next_link = response.links.get("next")
            if next_link:
                url = next_link["url"]  # carries start/limit and original query
                params = None
                continue
            sent = dict(response.request.url.params)
            if len(page) < int(sent.get("limit", PAGE_LIMIT)):
                return results
            sent["start"] = int(sent.get("start", 0)) + len(page)
            url = str(response.request.url.copy_with(query=None))
            params = sent

    # -- reads ---------------------------------------------------------------

    def all_items(
        self,
        since: int | None = None,
        include_trashed: bool = False,
        include: str | None = None,
        style: str | None = None,
        if_modified_since: int | None = None,
    ) -> list | None:
        """Full paginated dump. None when if_modified_since says nothing changed."""
        params: dict = {}
        if since is not None:
            params["since"] = since
        if include_trashed:
            params["includeTrashed"] = 1
        if include:
            params["include"] = include
        if style:
            params["style"] = style
        headers = None
        if if_modified_since is not None:
            headers = {"If-Modified-Since-Version": str(if_modified_since)}
        return self._paginated(f"{self._prefix}/items", params, headers)

    def items_batch(
        self,
        keys: list[str],
        include: str | None = None,
        style: str | None = None,
        include_trashed: bool = False,
    ) -> list:
        """Read exactly the given item keys, chunked at the API's 50-key cap."""
        results: list = []
        for chunk in batched(keys, BATCH_SIZE):
            params: dict = {"itemKey": ",".join(chunk), "limit": BATCH_SIZE}
            if include:
                params["include"] = include
            if style:
                params["style"] = style
            if include_trashed:
                params["includeTrashed"] = 1
            results.extend(self._get(f"{self._prefix}/items", params).json())
        return results

    def collections_batch(self, keys: list[str]) -> list:
        """Read exactly the given collection keys, chunked at the API's 50-key cap."""
        results: list = []
        for chunk in batched(keys, BATCH_SIZE):
            params = {"collectionKey": ",".join(chunk), "limit": BATCH_SIZE}
            results.extend(self._get(f"{self._prefix}/collections", params).json())
        return results

    def all_collections(self) -> list:
        return self._paginated(f"{self._prefix}/collections")

    def all_tags(self) -> list:
        return self._paginated(f"{self._prefix}/tags")

    def children(self, key: str) -> list:
        """Child items (attachments, notes) of one item."""
        return self._paginated(f"{self._prefix}/items/{key}/children")

    def fulltext(self, key: str) -> dict | None:
        """Server-side extracted fulltext of an attachment (content plus page/char
        totals); None when the server has none."""
        response = self._request("GET", f"{self._prefix}/items/{key}/fulltext")
        if response.status_code == 404:
            return None
        return response.json()

    def setting(self, name: str) -> dict | None:
        """A library setting (e.g. tagColors); None when unset."""
        response = self._request("GET", f"{self._prefix}/settings/{name}")
        if response.status_code == 404:
            return None
        return response.json()

    # -- writes --------------------------------------------------------------

    def write_items(self, objects: list[dict]) -> WriteResult:
        """Batch item write (partial updates and creates), chunked at 50."""
        return self._write(f"{self._prefix}/items", objects)

    def write_collections(self, objects: list[dict]) -> WriteResult:
        """Batch collection write, same mechanics as items."""
        return self._write(f"{self._prefix}/collections", objects)

    def _write(self, url: str, objects: list[dict]) -> WriteResult:
        """POST in 50-object chunks; merge per-object result maps keyed by object key.

        Each object carries its own version pin — per-object conflicts arrive
        in the failed map, never as a whole-batch rejection (SPEC: no
        library-level If-Unmodified-Since-Version on item writes).
        """
        result = WriteResult()
        for chunk in batched(objects, BATCH_SIZE):
            response = self._request("POST", url, json=list(chunk))
            maps = response.json()
            version = int(response.headers["Last-Modified-Version"])
            for key in maps.get("success", {}).values():
                result.applied[key] = version
            for idx, key in maps.get("unchanged", {}).items():
                result.unchanged[key] = chunk[int(idx)]["version"]
            for idx, error in maps.get("failed", {}).items():
                result.failed[chunk[int(idx)]["key"]] = {
                    "code": error.get("code"),
                    "message": self._redact(str(error.get("message", ""))),
                }
        return result

    def write_setting(self, name: str, value, if_unmodified_since: int) -> int:
        """PUT a library setting, pinned to the library version; returns the new version.

        Settings are the one endpoint using the library-level header — a stale
        pin fails the whole operation loudly (HTTP 412).
        """
        self._request(
            "PUT",
            f"{self._prefix}/settings/{name}",
            headers={"If-Unmodified-Since-Version": str(if_unmodified_since)},
            json={"value": value},
        )
        assert self.last_modified_version is not None
        return self.last_modified_version

    # -- schema --------------------------------------------------------------

    def item_type_fields(self, item_type: str) -> set[str]:
        """Valid data fields for an item type (/itemTypeFields), cached per session."""
        if item_type not in self._field_cache:
            data = self._get("/itemTypeFields", params={"itemType": item_type}).json()
            self._field_cache[item_type] = {f["field"] for f in data}
        return self._field_cache[item_type]

    def raw(self, path_suffix: str):
        """Raw user-scoped GET for `zel debug probe` — returns the parsed JSON body."""
        return self._get(f"{self._prefix}/{path_suffix.lstrip('/')}").json()

    def key_info(self) -> dict:
        """Identity and access of the current API key (/keys/current)."""
        return self._get("/keys/current").json()

    def library_version(self) -> int:
        """Current library version in one cheap request."""
        self._get(f"{self._prefix}/items", params={"limit": 1, "format": "keys"})
        assert self.last_modified_version is not None
        return self.last_modified_version
