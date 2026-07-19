"""Output helpers: NDJSON for agents on stdout, diagnostics on stderr, HTML stripping."""

from __future__ import annotations

import html
import json
import re
import sys

_TAG_RE = re.compile(r"<[^>]+>")


def emit_ndjson(obj) -> None:
    """One compact JSON object per line — the form agents consume."""
    print(json.dumps(obj, ensure_ascii=False))


def note(message: str) -> None:
    """Diagnostics and defaulting notices go to stderr, never stdout."""
    print(message, file=sys.stderr)


def strip_html(fragment: str) -> str:
    """Flatten a server-rendered bibliography fragment to terminal text."""
    text = _TAG_RE.sub("", fragment)
    return html.unescape(text).strip()


def render_table(columns: list[str], rows: list[tuple]) -> list[str]:
    """Space-aligned text table: header, rule, rows."""
    cells = [[str(value) for value in row] for row in rows]
    widths = [
        max(len(name), *(len(row[i]) for row in cells)) if cells else len(name)
        for i, name in enumerate(columns)
    ]
    lines = [
        "  ".join(name.ljust(widths[i]) for i, name in enumerate(columns)).rstrip(),
        "  ".join("-" * width for width in widths),
    ]
    lines += [
        "  ".join(row[i].ljust(widths[i]) for i in range(len(columns))).rstrip() for row in cells
    ]
    return lines
