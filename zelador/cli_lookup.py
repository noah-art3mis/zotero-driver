"""zel lookup — deterministic enrichment lookups, registered onto the main app."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

import typer

from zelador import config
from zelador.lookup import fulltext as fulltext_mod
from zelador.lookup import sources
from zelador.lookup.cache import LookupCache
from zelador.output import emit_ndjson, note

MAX_CANDIDATES = 3

CANDIDATE_SOURCES = {"crossref": sources.crossref, "arxiv": sources.arxiv}


def _cli():
    from zelador import cli

    return cli


def make_web() -> sources.Web:
    return sources.Web(LookupCache(config.ensure_dir("cache")))


def register(app: typer.Typer) -> None:
    app.command(rich_help_panel="Findings")(lookup)


def lookup(
    source: Annotated[str, typer.Argument(help="Lookup source: crossref, arxiv, fulltext.")],
    key: Annotated[str, typer.Argument(help="Item key (fulltext also takes a PDF attachment).")],
    full: Annotated[bool, typer.Option("--full", help="Return everything, unbounded.")] = False,
    image: Annotated[
        bool, typer.Option("--image", help="fulltext only: render page one to PNG.")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="NDJSON candidates + summary, or one fulltext object.")
    ] = False,
):
    """Enrichment lookups: metadata candidates with scores, or a PDF's fulltext.

    Responses are cached forever under <data dir>/cache/ — repeat lookups
    are free. Output is bounded by default; --full lifts the bound.

    Examples:
        zel lookup crossref AAAA1111 --json
        zel lookup fulltext AAAA1111 --full
        zel lookup fulltext AAAA1111 --image
    """
    if source not in (*CANDIDATE_SOURCES, "fulltext"):
        raise typer.BadParameter(f"unknown source {source!r} — crossref, arxiv, or fulltext")
    with _cli().guard():
        client = _cli().make_client()
        try:
            if source == "fulltext":
                _fulltext(client, key, full, image, as_json)
            else:
                _candidates(client, source, key, full, as_json)
        except sources.SourceError as exc:
            note(f"error: {exc}")
            raise typer.Exit(1) from None


def _item_data(client, key: str) -> dict:
    found = client.items_batch([key])
    if not found:
        raise sources.SourceError(f"no such item: {key}")
    return found[0]["data"]


def _candidates(client, source: str, key: str, full: bool, as_json: bool) -> None:
    candidates = CANDIDATE_SOURCES[source](_item_data(client, key), make_web())
    shown = candidates if full else candidates[:MAX_CANDIDATES]
    if as_json:
        for candidate in shown:
            emit_ndjson(asdict(candidate))
        emit_ndjson(
            {
                "shown": len(shown),
                "total": len(candidates),
                "truncated": len(shown) < len(candidates),
            }
        )
        return
    for candidate in shown:
        creators = candidate.creators[0] if candidate.creators else "?"
        doi = f"  DOI {candidate.doi}" if candidate.doi else ""
        print(f"{candidate.score:5.3f}  {candidate.year or '----'}  {candidate.title}")
        print(f"       {creators} — {candidate.container or candidate.url}{doi}")
    if len(shown) < len(candidates):
        note(f"… and {len(candidates) - len(shown)} more (--full to see all)")


def _fulltext(client, key: str, full: bool, image: bool, as_json: bool) -> None:
    zotero_dir = _zotero_dir()
    attachment = fulltext_mod.resolve_attachment(client, key)
    image_path = None
    if image:
        # Rendered before text extraction: --image exists for exactly the
        # scanned/garbled PDFs whose extraction fails.
        if zotero_dir is None:
            raise sources.SourceError("--image needs the local Zotero data dir")
        pdf = fulltext_mod.attachment_pdf_path(zotero_dir, attachment)
        out = config.ensure_dir("cache") / f"{attachment['key']}-page1.png"
        image_path = str(fulltext_mod.render_page(pdf, out))
    cache = LookupCache(config.ensure_dir("cache"))
    try:
        result = fulltext_mod.attachment_fulltext(client, key, attachment, zotero_dir, cache)
    except sources.SourceError as exc:
        if image_path is None:
            raise
        _emit_image_only(key, attachment["key"], str(exc), image_path, as_json)
        return
    content = result.content if full else result.head
    truncated = len(content) < len(result.content)
    if as_json:
        payload = {**asdict(result), "content": content, "truncated": truncated}
        del payload["head"]
        if image:
            payload["image"] = image_path
        emit_ndjson(payload)
        return
    print(content)
    pages = f", {result.pages} page(s)" if result.pages else ""
    note(f"fulltext of {result.attachment} via {result.origin}{pages}")
    if result.path:
        note(f"pdf: {result.path}")
    if truncated:
        note("head only — --full for everything")
    if image_path:
        note(f"page one rendered: {image_path}")


def _emit_image_only(key, attachment_key, error, image_path, as_json) -> None:
    """Text extraction failed but the render succeeded — deliver the image."""
    if as_json:
        emit_ndjson(
            {"item": key, "attachment": attachment_key, "content": None,
             "error": error, "image": image_path}
        )
    else:
        note(f"no text: {error}")
        note(f"page one rendered: {image_path}")


def _zotero_dir():
    try:
        return config.discover_zotero_dir(override=config.load_config().zotero_data_dir)
    except config.ConfigError:
        return None
