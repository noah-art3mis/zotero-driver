"""The in-memory library snapshot audit checks run against."""

from __future__ import annotations

from dataclasses import dataclass, field

NON_BIBLIOGRAPHIC = {"note", "annotation", "attachment"}


@dataclass(frozen=True)
class Library:
    items: list = field(default_factory=list)
    collections: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    tag_colors: list = field(default_factory=list)  # live tagColors setting value

    def regular_items(self) -> list:
        """Items that belong in a bibliography — no notes, annotations, or attachments."""
        return [i for i in self.items if i["data"]["itemType"] not in NON_BIBLIOGRAPHIC]


def finding(check: str, kind: str, keys: list[str], message: str, **detail) -> dict:
    return {"check": check, "kind": kind, "keys": keys, "message": message, "detail": detail}
