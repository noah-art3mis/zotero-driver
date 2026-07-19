"""Thin Zotero Web API v3 client on httpx.

Reads only (writes land in M3a). Musts from SPEC: pagination via Link headers,
50-key batch reads, Backoff/Retry-After honoured including on 200s, real 429
retries, explicit timeouts, user-scoped endpoints only, API-key redaction on
every error path.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from itertools import batched

import httpx

from zelador.config import Credentials

API_BASE = "https://api.zotero.org"
PAGE_LIMIT = 100
BATCH_SIZE = 50
MAX_RETRIES = 5


class ZoteroError(Exception):
    """API or transport failure, message guaranteed free of the API key."""


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

    def _request(self, method: str, url: str, params=None, headers=None) -> httpx.Response:
        if self._pending_backoff:
            self._announce(f"server asked for backoff — waiting {self._pending_backoff:g}s")
            self._sleep(self._pending_backoff)
            self._pending_backoff = 0.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._http.request(method, url, params=params, headers=headers)
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
        """Follow Link: next until exhausted. None means 304 Not Modified."""
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
            results.extend(response.json())
            next_link = response.links.get("next")
            if not next_link:
                return results
            url = next_link["url"]  # carries start/limit and original query
            params = None
            headers = None

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
        self, keys: list[str], include: str | None = None, style: str | None = None
    ) -> list:
        """Read exactly the given item keys, chunked at the API's 50-key cap."""
        results: list = []
        for chunk in batched(keys, BATCH_SIZE):
            params: dict = {"itemKey": ",".join(chunk), "limit": BATCH_SIZE}
            if include:
                params["include"] = include
            if style:
                params["style"] = style
            results.extend(self._get(f"{self._prefix}/items", params).json())
        return results

    def all_collections(self) -> list:
        return self._paginated(f"{self._prefix}/collections")

    def all_tags(self) -> list:
        return self._paginated(f"{self._prefix}/tags")

    def setting(self, name: str) -> dict | None:
        """A library setting (e.g. tagColors); None when unset."""
        response = self._request("GET", f"{self._prefix}/settings/{name}")
        if response.status_code == 404:
            return None
        return response.json()

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
