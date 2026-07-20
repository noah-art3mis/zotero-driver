"""CLI tests for zel lookup."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from tests.conftest import FakeZotero, make_item
from tests.test_lookup_fulltext import pdf_attachment, storage_with_pdf
from tests.test_lookup_sources import CROSSREF_WORK
from zelador import cli, cli_lookup, config
from zelador.client import ZoteroClient
from zelador.config import Credentials
from zelador.lookup.cache import LookupCache
from zelador.lookup.sources import Web

runner = CliRunner()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZELADOR_DATA_DIR", str(tmp_path / "data"))
    fake = FakeZotero(
        items=[
            make_item(
                "AAAA1111",
                title="A Mathematical Theory of Communication",
                date="1948",
                creators=[{"creatorType": "author", "lastName": "Shannon"}],
            ),
            pdf_attachment(),
        ],
        children={"AAAA1111": [pdf_attachment()]},
        fulltexts={"ATTACH01": {"content": "word " * 30, "totalPages": 7}},
        library_version=42,
        page_size=100,
    )
    creds = Credentials(api_key="sekrit", user_id="11868292")
    monkeypatch.setattr(
        cli,
        "make_client",
        lambda: ZoteroClient(creds, transport=fake.transport, sleep=lambda s: None),
    )
    works = [
        {**CROSSREF_WORK, "DOI": f"10.9/n{n}", "title": [f"Result {n}"]} for n in range(5)
    ] + [CROSSREF_WORK]

    def external(request):
        return httpx.Response(200, json={"message": {"items": works}})

    monkeypatch.setattr(
        cli_lookup,
        "make_web",
        lambda: Web(
            LookupCache(tmp_path / "data" / "cache"),
            transport=httpx.MockTransport(external),
        ),
    )
    return fake, tmp_path


class TestCandidateLookup:
    def test_bounded_text_output(self, env):
        result = runner.invoke(cli.app, ["lookup", "crossref", "AAAA1111"])
        assert result.exit_code == 0
        assert "A Mathematical Theory of Communication" in result.output
        assert "3 more" in result.output  # 6 candidates, 3 shown

    def test_json_flags_truncation(self, env):
        result = runner.invoke(cli.app, ["lookup", "crossref", "AAAA1111", "--json"])
        lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
        assert len(lines) == 4  # 3 candidates + summary
        assert lines[0]["source"] == "crossref" and lines[0]["score"] == 1.0
        assert lines[-1] == {"shown": 3, "total": 6, "truncated": True}

    def test_full_returns_everything(self, env):
        result = runner.invoke(cli.app, ["lookup", "crossref", "AAAA1111", "--full", "--json"])
        lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
        assert lines[-1] == {"shown": 6, "total": 6, "truncated": False}

    def test_unknown_source_is_bad_input(self, env):
        assert runner.invoke(cli.app, ["lookup", "scholar", "AAAA1111"]).exit_code == 2

    def test_unknown_item_is_operational_failure(self, env):
        assert runner.invoke(cli.app, ["lookup", "crossref", "ZZZZ9999"]).exit_code == 1


class TestFulltextLookup:
    def test_server_head_bounded_with_pages_stated(self, env, monkeypatch):
        monkeypatch.setattr(cli_lookup.fulltext_mod, "HEAD_CHARS", 40)
        result = runner.invoke(cli.app, ["lookup", "fulltext", "AAAA1111", "--json"])
        payload = json.loads(result.stdout.strip())
        assert payload["origin"] == "server" and payload["attachment"] == "ATTACH01"
        assert payload["truncated"] is True and len(payload["content"]) == 40
        assert payload["pages"] == 7

    def test_full_content_untruncated(self, env):
        result = runner.invoke(cli.app, ["lookup", "fulltext", "AAAA1111", "--full", "--json"])
        payload = json.loads(result.stdout.strip())
        assert payload["truncated"] is False and payload["content"].count("word") == 30

    def test_local_fallback_states_the_pdf_path(self, env, monkeypatch, tmp_path):
        fake, _ = env
        fake.fulltexts.clear()
        storage = storage_with_pdf(tmp_path, pages=("First page", "Second page"))
        monkeypatch.setattr(config, "discover_zotero_dir", lambda override=None: storage)
        result = runner.invoke(cli.app, ["lookup", "fulltext", "AAAA1111", "--json"])
        payload = json.loads(result.stdout.strip())
        assert payload["origin"] == "local" and payload["pages"] == 2
        assert payload["content"] == "First page"  # the head is a real page
        assert payload["path"].endswith("paper.pdf")

    def test_image_renders_page_one(self, env, monkeypatch, tmp_path):
        storage = storage_with_pdf(tmp_path, pages=("Scan",))
        monkeypatch.setattr(config, "discover_zotero_dir", lambda override=None: storage)
        result = runner.invoke(
            cli.app, ["lookup", "fulltext", "AAAA1111", "--image", "--json"]
        )
        payload = json.loads(result.stdout.strip())
        image = payload["image"]
        assert image.endswith(".png")
        with open(image, "rb") as handle:
            assert handle.read(8) == b"\x89PNG\r\n\x1a\n"

    def test_image_survives_garbled_text_extraction(self, env, monkeypatch, tmp_path):
        # The whole point of --image: the render must land even when pypdf
        # cannot extract a single character.
        fake, _ = env
        fake.fulltexts.clear()
        storage = storage_with_pdf(tmp_path, pages=("Scan",))
        monkeypatch.setattr(config, "discover_zotero_dir", lambda override=None: storage)
        monkeypatch.setattr(
            cli_lookup.fulltext_mod,
            "_extract_pages",
            lambda path: (_ for _ in ()).throw(
                cli_lookup.sources.SourceError("pypdf could not read it")
            ),
        )
        result = runner.invoke(
            cli.app, ["lookup", "fulltext", "AAAA1111", "--image", "--json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout.strip())
        assert payload["content"] is None
        assert "pypdf" in payload["error"]
        assert payload["image"].endswith(".png")
