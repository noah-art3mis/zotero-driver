"""Never-expiring response cache keyed by URL; pruned by hand (prototype posture)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class LookupCache:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self.directory / f"{digest}.json"

    def get(self, url: str) -> str | None:
        path = self._path(url)
        if not path.exists():
            return None
        return json.loads(path.read_text())["body"]

    def put(self, url: str, body: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(url).write_text(json.dumps({"url": url, "body": body}, ensure_ascii=False))
