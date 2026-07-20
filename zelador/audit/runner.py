"""Audit orchestration: one library dump, N checks, stamped JSON + report on disk."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from zelador import citekeys
from zelador.audit import citations, completeness, conformance, duplicates, hygiene, report, tagmess
from zelador.audit.library import Library
from zelador.client import ZoteroClient
from zelador.taxonomy import Taxonomy

CHECKS = {
    "completeness": completeness.check,
    "tags": tagmess.check,
    "collections": hygiene.check,
    "duplicates": duplicates.check,
}


class UnknownCheck(Exception):
    pass


def run_audit(
    client: ZoteroClient,
    audit_dir: Path,
    check: str | None = None,
    since: int | None = None,
    style: str = "apa",
    taxonomy: Taxonomy | None = None,
    citekey_sources: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Run all checks (or one), write <check>.json per check plus audit-report.md."""
    checks = dict(CHECKS)
    if taxonomy is not None:
        checks["registry"] = lambda library: conformance.check(library, taxonomy)
    if citekey_sources:
        # Sources are scanned only when the check actually runs — a broken
        # source must not take down an unrelated single-check run.
        checks["citekeys"] = lambda library: citations.check(
            library, citekeys.scan_sources(citekey_sources)
        )
    if check is not None and check not in checks:
        if check == "registry":
            raise UnknownCheck("the registry check needs taxonomy.yaml — copy the example first")
        if check == "citekeys":
            raise UnknownCheck("the citekeys check needs citekey_sources in config.yaml")
        raise UnknownCheck(f"unknown check {check!r} — one of: {', '.join(checks)}")
    names = [check] if check else list(checks)

    tag_colors = []
    if "registry" in names:
        tag_colors = (client.setting("tagColors") or {}).get("value", [])
    library = Library(
        items=client.all_items(include="data,bib", style=style),
        collections=client.all_collections(),
        tags=client.all_tags(),
        tag_colors=tag_colors,
    )
    version = client.last_modified_version
    timestamp = (now or datetime.now(UTC)).isoformat()
    scoped = _scope_keys(library, since) if since is not None else None

    counts = {}
    for name in names:
        findings = checks[name](library)
        if scoped is not None:
            findings = [f for f in findings if any(key in scoped for key in f["keys"])]
        payload = {
            "check": name,
            "library_version": version,
            "timestamp": timestamp,
            "since": since,
            "findings": findings,
        }
        (audit_dir / f"{name}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        )
        counts[name] = len(findings)

    report_path = report.write_report(audit_dir)
    return {
        "library_version": version,
        "timestamp": timestamp,
        "counts": counts,
        "report": str(report_path),
    }


def _scope_keys(library: Library, since: int) -> set[str]:
    """Keys of items and collections added or modified after the marker version."""
    objects = list(library.items) + list(library.collections)
    return {o["key"] for o in objects if o["version"] > since}
