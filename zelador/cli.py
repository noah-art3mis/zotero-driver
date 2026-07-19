"""zel — deterministic CLI over the Zotero Web API. Thin Typer bodies over pure functions."""

from __future__ import annotations

import json
from collections import defaultdict
from contextlib import contextmanager
from typing import Annotated

import typer

from zelador import backup as backup_mod
from zelador import config
from zelador import status as status_mod
from zelador.client import ZoteroClient, ZoteroError
from zelador.output import emit_ndjson, note, strip_html

app = typer.Typer(
    help="Caretaker for a personal Zotero library. The agent proposes; you approve.",
    no_args_is_help=True,
)
debug_app = typer.Typer(help="Inspection and recovery utilities.", no_args_is_help=True)
app.add_typer(debug_app, name="debug", rich_help_panel="Utilities")

_state = {"verbose": False}


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Trace each API request on stderr.")
    ] = False,
):
    _state["verbose"] = verbose


def make_client() -> ZoteroClient:
    creds = config.load_credentials()
    trace = note if _state["verbose"] else None
    return ZoteroClient(creds, trace=trace)


@contextmanager
def guard():
    """Operational failures exit 1 with the reason on stderr, never a traceback."""
    try:
        yield
    except (config.ConfigError, ZoteroError) as exc:
        note(f"error: {exc}")
        raise typer.Exit(1) from None


# -- Library ---------------------------------------------------------------


@app.command(rich_help_panel="Library")
def status(
    as_json: Annotated[bool, typer.Option("--json", help="One status object.")] = False,
):
    """One-screen session orientation — the skills' mandatory opener.

    Live library version, last backup and its counts, unresolved session
    logs, latest audit stamp, and config presence. The API being down is
    reported, not fatal: the local half still prints.

    Examples:
        zel status
        zel status --json | jq .backup.library_version
    """
    with guard():
        cfg = config.load_config()
        result = status_mod.local_status(
            config.ensure_dir("backups"), config.ensure_dir("log"), config.ensure_dir("audit"), cfg
        )
        try:
            result["api"] = {"library_version": make_client().library_version(), "error": None}
        except (ZoteroError, config.ConfigError) as exc:
            result["api"] = {"library_version": None, "error": str(exc)}
        if as_json:
            emit_ndjson(result)
            return
        for line in status_mod.render_status(result):
            print(line)


# -- Change loop -----------------------------------------------------------


@app.command(rich_help_panel="Change loop")
def backup(
    as_json: Annotated[bool, typer.Option("--json", help="Final outcome object.")] = False,
):
    """Full-library JSONL snapshot (items incl. trash, collections, tagColors).

    A verified no-op when the library hasn't changed since the last backup.

    Examples:
        zel backup
        zel backup --json | jq .noop
    """
    with guard():
        backups_dir = config.ensure_dir("backups")
        client = make_client()
        path = backup_mod.run_backup(client, backups_dir)
        if path is None:
            info = backup_mod.latest_backup(backups_dir)
            note(f"library unchanged since backup {info.timestamp} — nothing to do")
            if as_json:
                emit_ndjson({"noop": True, "library_version": info.library_version})
            return
        stats = backup_mod.backup_stats(path)
        version = backup_mod.latest_backup(backups_dir).library_version
        if as_json:
            emit_ndjson(
                {
                    "noop": False,
                    "path": str(path),
                    "library_version": version,
                    "items": stats.items,
                    "collections": stats.collections,
                    "tags": stats.tags,
                }
            )
        else:
            print(
                f"wrote {path} — version {version}, {stats.items} items, "
                f"{stats.collections} collections, {stats.tags} tags"
            )


@app.command(rich_help_panel="Library")
def items(
    keys: Annotated[list[str] | None, typer.Argument(help="Item keys for a batch read.")] = None,
    since: Annotated[
        int | None, typer.Option(help="Only items added/modified after this library version.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="NDJSON, one item per line.")] = False,
    bib: Annotated[bool, typer.Option("--bib", help="Render bibliography entries.")] = False,
    style: Annotated[str | None, typer.Option(help="CSL style for --bib.")] = None,
):
    """Full paginated dump, or batch read of the given keys.

    Examples:
        zel items --json | jq .key
        zel items AAAA1111 BBBB2222 --bib --style apa
        zel items --since 3410 --json
    """
    with guard():
        if bib and style is None:
            style = config.load_config().style
            note(f"using style: {style}")
        include = "bib" if bib else None
        client = make_client()
        if keys:
            found = client.items_batch(list(keys), include=include, style=style)
        else:
            found = client.all_items(since=since, include=include, style=style)
        for item in found:
            if as_json:
                emit_ndjson(item)
            elif bib:
                print(strip_html(item.get("bib", "")))
            else:
                data = item["data"]
                print(f"{item['key']}  {data.get('itemType', '?'):<18} {data.get('title', '')}")


@app.command(rich_help_panel="Library")
def tags(
    as_json: Annotated[bool, typer.Option("--json", help="NDJSON, one tag per line.")] = False,
):
    """Tag list with item counts and type (manual/auto).

    Examples:
        zel tags
        zel tags --json | jq -r .tag
    """
    with guard():
        found = make_client().all_tags()
        for tag in found:
            if as_json:
                emit_ndjson(tag)
            else:
                meta = tag.get("meta", {})
                kind = "auto" if meta.get("type") == 1 else "manual"
                print(f"{meta.get('numItems', 0):>6}  {kind:<7} {tag['tag']}")


@app.command(rich_help_panel="Library")
def collections(
    as_json: Annotated[
        bool, typer.Option("--json", help="NDJSON, one collection per line.")
    ] = False,
):
    """Collection tree.

    Examples:
        zel collections
        zel collections --json | jq -r .data.name
    """
    with guard():
        found = make_client().all_collections()
        if as_json:
            for coll in found:
                emit_ndjson(coll)
            return
        for line in render_collection_tree(found):
            print(line)


def render_collection_tree(collections: list) -> list[str]:
    by_key = {c["key"]: c["data"] for c in collections}
    children: dict = defaultdict(list)
    for key, data in by_key.items():
        children[data.get("parentCollection") or False].append(key)
    lines: list[str] = []
    seen: set[str] = set()

    def walk(parent, depth: int):
        for key in sorted(children[parent], key=lambda k: by_key[k]["name"].lower()):
            lines.append("  " * depth + f"{by_key[key]['name']}  [{key}]")
            seen.add(key)
            walk(key, depth + 1)

    walk(False, 0)
    for key, data in by_key.items():  # subtrees whose parent key no longer exists
        if key not in seen and data.get("parentCollection") not in by_key:
            lines.append(f"{data['name']}  [{key}]  (orphaned)")
            seen.add(key)
            walk(key, 1)
    return lines


# -- Utilities -------------------------------------------------------------


@debug_app.command()
def whoami():
    """Identity and access of the configured API key (/keys/current)."""
    with guard():
        info = make_client().key_info()
        print(f"username: {info.get('username', '?')}")
        print(f"userID:   {info.get('userID', '?')}")
        print(f"access:   {json.dumps(info.get('access', {}))}")


@debug_app.command()
def paths():
    """Resolved paths: repo root, data dir, config files, Zotero data dir."""
    print(f"repo root:     {config.REPO_ROOT}")
    print(f"data dir:      {config.data_dir()}")
    for sub in config.DATA_SUBDIRS:
        print(f"  {sub + '/':<12} {config.data_dir() / sub}")
    named = (("config.yaml", config.CONFIG_FILE), ("taxonomy.yaml", config.TAXONOMY_FILE))
    for label, path in named:
        print(f"{label}:   {path} ({'present' if path.exists() else 'absent'})")
    try:
        cfg = config.load_config()
        print(f"zotero dir:    {config.discover_zotero_dir(override=cfg.zotero_data_dir)}")
    except config.ConfigError as exc:
        print(f"zotero dir:    not found ({exc})")


@debug_app.command()
def probe(
    path: Annotated[str, typer.Argument(help="User-scoped API path suffix, e.g. 'items?limit=1'.")],
):
    """Raw API probe: GET a user-scoped path and pretty-print the JSON response.

    Examples:
        zel debug probe "items?limit=1"
        zel debug probe settings/tagColors
    """
    with guard():
        print(json.dumps(make_client().raw(path), indent=2, ensure_ascii=False))
