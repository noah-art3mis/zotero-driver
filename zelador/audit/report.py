"""Generate audit-report.md from whatever check JSONs are on disk."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

MAX_EXAMPLES = 20

# Known checks first, in spec order; anything else alphabetical after.
CHECK_ORDER = ["completeness", "tags", "collections", "duplicates", "registry"]


def write_report(audit_dir: Path) -> Path:
    paths = sorted(
        audit_dir.glob("*.json"),
        key=lambda p: (
            CHECK_ORDER.index(p.stem) if p.stem in CHECK_ORDER else len(CHECK_ORDER),
            p.stem,
        ),
    )
    lines = ["# Audit report", ""]
    for path in paths:
        payload = json.loads(path.read_text())
        findings = payload["findings"]
        scope = f", scoped to changes since version {payload['since']}" if payload["since"] else ""
        lines += [
            f"## {payload['check']} — {len(findings)} finding(s)",
            "",
            f"Computed at library version {payload['library_version']}, "
            f"{payload['timestamp']}{scope}.",
            "",
        ]
        if not findings:
            lines += ["Nothing to report.", ""]
            continue
        by_kind = Counter(f["kind"] for f in findings)
        lines += [f"- **{kind}**: {count}" for kind, count in by_kind.most_common()]
        lines.append("")
        for f in findings[:MAX_EXAMPLES]:
            keys = ", ".join(f["keys"])
            lines.append(f"- `{keys or '—'}` {f['message']}")
        if len(findings) > MAX_EXAMPLES:
            lines.append(f"- … and {len(findings) - MAX_EXAMPLES} more (see {path.name})")
        lines.append("")
    path = audit_dir / "audit-report.md"
    path.write_text("\n".join(lines))
    return path
