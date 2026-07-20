"""Fulltext of an item's PDF: server extraction first, local pypdf as fallback.

`render_page` rasterizes page one for visual inspection of scanned PDFs —
imports pypdfium2/Pillow lazily so the text paths never need them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zelador.client import ZoteroClient
from zelador.lookup.sources import SourceError

PDF_MIME = "application/pdf"


@dataclass(frozen=True)
class Fulltext:
    item: str  # the requested key
    attachment: str  # the resolved PDF attachment key
    origin: str  # "server" | "local"
    content: str


def resolve_attachment(client: ZoteroClient, key: str) -> dict:
    """The PDF attachment behind a key — the key itself, or the item's first PDF child."""
    found = client.items_batch([key])
    if not found:
        raise SourceError(f"no such item: {key}")
    item = found[0]
    if item["data"]["itemType"] == "attachment":
        return item
    pdfs = [c for c in client.children(key) if c["data"].get("contentType") == PDF_MIME]
    if not pdfs:
        raise SourceError(f"item {key} has no PDF attachment")
    return sorted(pdfs, key=lambda c: c["key"])[0]


def attachment_pdf_path(zotero_dir: Path, attachment: dict) -> Path:
    filename = attachment["data"].get("filename", "")
    path = zotero_dir / "storage" / attachment["key"] / filename
    if not filename or not path.exists():
        raise SourceError(
            f"attachment {attachment['key']} has no local file under {zotero_dir}/storage"
        )
    return path


def fetch_fulltext(client: ZoteroClient, key: str, zotero_dir: Path | None) -> Fulltext:
    attachment = resolve_attachment(client, key)
    content = client.fulltext(attachment["key"])
    if content:
        return Fulltext(item=key, attachment=attachment["key"], origin="server", content=content)
    if zotero_dir is None:
        raise SourceError(
            f"server has no fulltext for {attachment['key']} and no local Zotero "
            "data dir is available for pypdf extraction"
        )
    return Fulltext(
        item=key,
        attachment=attachment["key"],
        origin="local",
        content=_extract_text(attachment_pdf_path(zotero_dir, attachment)),
    )


def _extract_text(pdf_path: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(pdf_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:  # pypdf raises a zoo of parse errors
        raise SourceError(f"pypdf could not read {pdf_path}: {exc}") from None


def render_page(pdf_path: Path, out_path: Path) -> Path:
    """Rasterize page one to PNG (2x scale) for eyeballing scanned PDFs."""
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(pdf_path)
        page = document[0]
        page.render(scale=2.0).to_pil().save(out_path, format="PNG")
    except Exception as exc:
        raise SourceError(f"could not render {pdf_path}: {exc}") from None
    return out_path
