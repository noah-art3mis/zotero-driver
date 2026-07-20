"""Tests for fulltext lookup: server first, local pypdf fallback, page-one render."""

import pytest

from tests.conftest import USER_ID, FakeZotero, make_item
from zelador.client import ZoteroClient
from zelador.config import Credentials
from zelador.lookup.fulltext import fetch_fulltext, render_page
from zelador.lookup.sources import SourceError


def make_pdf(text: str) -> bytes:
    """A minimal single-page PDF with a correct xref, extractable by pypdf."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


def client_for(fake: FakeZotero) -> ZoteroClient:
    creds = Credentials(api_key="k", user_id=USER_ID)
    return ZoteroClient(creds, transport=fake.transport, sleep=lambda s: None)


def pdf_attachment(key="ATTACH01", filename="paper.pdf"):
    return make_item(key, item_type="attachment", contentType="application/pdf",
                     filename=filename)


def storage_with_pdf(tmp_path, key="ATTACH01", filename="paper.pdf", text="Hello local"):
    storage = tmp_path / "zotero"
    (storage / "storage" / key).mkdir(parents=True)
    (storage / "storage" / key / filename).write_bytes(make_pdf(text))
    return storage


class TestFetchFulltext:
    def test_server_fulltext_for_an_attachment(self):
        fake = FakeZotero(
            items=[pdf_attachment()], fulltexts={"ATTACH01": "server says hello"}
        )
        result = fetch_fulltext(client_for(fake), "ATTACH01", zotero_dir=None)
        assert result.origin == "server"
        assert result.content == "server says hello"
        assert result.attachment == "ATTACH01"

    def test_parent_item_resolves_to_its_pdf_child(self):
        fake = FakeZotero(
            items=[make_item("PARENT01")],
            children={
                "PARENT01": [
                    make_item("NOTE0001", item_type="note"),
                    pdf_attachment(),
                ]
            },
            fulltexts={"ATTACH01": "via the parent"},
        )
        result = fetch_fulltext(client_for(fake), "PARENT01", zotero_dir=None)
        assert result.attachment == "ATTACH01" and result.content == "via the parent"

    def test_server_miss_falls_back_to_local_pypdf(self, tmp_path):
        storage = storage_with_pdf(tmp_path, text="Hello from the local PDF")
        fake = FakeZotero(items=[pdf_attachment()])
        result = fetch_fulltext(client_for(fake), "ATTACH01", zotero_dir=storage)
        assert result.origin == "local"
        assert "Hello from the local PDF" in result.content

    def test_no_server_text_and_no_local_dir_fails_loudly(self):
        fake = FakeZotero(items=[pdf_attachment()])
        with pytest.raises(SourceError, match="ATTACH01"):
            fetch_fulltext(client_for(fake), "ATTACH01", zotero_dir=None)

    def test_item_without_pdf_attachment_fails_loudly(self):
        fake = FakeZotero(items=[make_item("PARENT01")], children={"PARENT01": []})
        with pytest.raises(SourceError, match="PDF"):
            fetch_fulltext(client_for(fake), "PARENT01", zotero_dir=None)

    def test_unknown_item_fails_loudly(self):
        with pytest.raises(SourceError, match="ZZZZ9999"):
            fetch_fulltext(client_for(FakeZotero()), "ZZZZ9999", zotero_dir=None)


class TestRenderPage:
    def test_page_one_renders_to_png(self, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(make_pdf("Rendered"))
        out = render_page(pdf, tmp_path / "page1.png")
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
