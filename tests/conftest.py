"""Shared fixtures: an in-memory fake of the Zotero Web API served over httpx.MockTransport."""

from __future__ import annotations

import json
from collections import deque
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

USER_ID = "11868292"


def make_item(
    key: str,
    version: int = 1,
    item_type: str = "journalArticle",
    title: str = "A title",
    deleted: bool = False,
    parsed_date: str | None = None,
    bib: str | None = None,
    **data,
):
    payload = {"key": key, "version": version, "itemType": item_type, "title": title}
    payload.setdefault("tags", [])
    payload.setdefault("collections", [])
    payload.update(data)
    if deleted:
        payload["deleted"] = 1
    item = {
        "key": key,
        "version": version,
        "library": {"type": "user"},
        "meta": {"parsedDate": parsed_date} if parsed_date else {},
        "data": payload,
    }
    if bib is not None:
        item["bib"] = bib
    return item


def make_collection(key: str, name: str, version: int = 1, parent: str | None = None):
    return {
        "key": key,
        "version": version,
        "meta": {},
        "data": {
            "key": key,
            "version": version,
            "name": name,
            "parentCollection": parent or False,
            "deleted": False,
        },
    }


def make_tag(tag: str, num_items: int = 1, tag_type: int = 0):
    return {"tag": tag, "meta": {"type": tag_type, "numItems": num_items}}


class FakeZotero:
    """Minimal stand-in for api.zotero.org: pagination, versions, backoff scripting."""

    def __init__(
        self,
        items: list | None = None,
        collections: list | None = None,
        tags: list | None = None,
        settings: dict | None = None,
        library_version: int = 42,
        page_size: int = 2,
    ):
        self.items = items or []
        self.collections = collections or []
        self.tags = tags or []
        self.settings = settings or {}
        self.library_version = library_version
        self.page_size = page_size
        self.requests: list[httpx.Request] = []
        self.script: deque[httpx.Response] = deque()  # scripted responses jump the queue

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.script:
            return self.script.popleft()
        parsed = urlparse(str(request.url))
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        path = parsed.path

        if path == "/keys/current":
            return self._json(
                {
                    "userID": int(USER_ID),
                    "username": "noah-art3mis",
                    "access": {"user": {"library": True, "write": True}},
                }
            )
        if path == f"/users/{USER_ID}/items":
            return self._items_response(request, params, path)
        if path == f"/users/{USER_ID}/collections":
            return self._paginated(self.collections, params, path)
        if path == f"/users/{USER_ID}/tags":
            return self._paginated(self.tags, params, path)
        if path.startswith(f"/users/{USER_ID}/settings/"):
            name = path.rsplit("/", 1)[1]
            if name in self.settings:
                return self._json(self.settings[name])
            return httpx.Response(404, text="Not found")
        return httpx.Response(404, text=f"no fake route for {path}")

    def _items_response(self, request: httpx.Request, params: dict, path: str) -> httpx.Response:
        cond = request.headers.get("If-Modified-Since-Version")
        if cond is not None and int(cond) >= self.library_version:
            return httpx.Response(304, headers=self._version_header())
        pool = self.items
        if params.get("includeTrashed") != "1":
            pool = [i for i in pool if not i["data"].get("deleted")]
        if "since" in params:
            pool = [i for i in pool if i["version"] > int(params["since"])]
        if "itemKey" in params:
            keys = params["itemKey"].split(",")
            pool = [i for i in pool if i["key"] in keys]
        if "bib" in params.get("include", ""):
            pool = [
                {**i, "bib": f'<div class="csl-bib-body">{i["data"].get("title", "")}</div>'}
                for i in pool
            ]
        return self._paginated(pool, params, path)

    def _paginated(self, pool: list, params: dict, path: str) -> httpx.Response:
        start = int(params.get("start", 0))
        limit = min(int(params.get("limit", self.page_size)), self.page_size)
        page = pool[start : start + limit]
        headers = self._version_header()
        headers["Total-Results"] = str(len(pool))
        if start + limit < len(pool):
            carried = {k: v for k, v in params.items() if k not in ("start", "limit")}
            query = "".join(f"&{k}={v}" for k, v in carried.items())
            headers["Link"] = (
                f"<https://api.zotero.org{path}"
                f"?start={start + limit}&limit={limit}{query}>; rel=\"next\""
            )
        return httpx.Response(200, json=page, headers=headers)

    def _version_header(self) -> dict:
        return {"Last-Modified-Version": str(self.library_version)}

    def _json(self, obj) -> httpx.Response:
        return httpx.Response(200, json=obj, headers=self._version_header())


@pytest.fixture
def fake():
    return FakeZotero()


def response_json(response_text: str) -> list:
    return json.loads(response_text)
