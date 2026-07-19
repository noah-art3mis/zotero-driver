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
