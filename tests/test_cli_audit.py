"""CLI tests for zel audit."""

import json

import pytest
from typer.testing import CliRunner

from tests.conftest import FakeZotero, make_item, make_tag
from zelador import cli
from zelador.client import ZoteroClient
from zelador.config import Credentials

runner = CliRunner()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("ZELADOR_DATA_DIR", str(tmp_path / "data"))
    fake = FakeZotero(
        items=[make_item("BARE0001", title="Bare item", date="")],
        tags=[make_tag("AI"), make_tag("ai")],
        library_version=42,
        page_size=100,
    )
    creds = Credentials(api_key="sekrit", user_id="11868292")

    def fake_client():
        return ZoteroClient(creds, transport=fake.transport, sleep=lambda s: None)

    monkeypatch.setattr(cli, "make_client", fake_client)
    return fake


class TestAuditCommand:
    def test_runs_all_checks_and_reports_counts(self, fake, tmp_path):
        result = runner.invoke(cli.app, ["audit"])
        assert result.exit_code == 0
        assert "completeness" in result.output
        assert (tmp_path / "data" / "audit" / "audit-report.md").exists()

    def test_single_check(self, fake, tmp_path):
        result = runner.invoke(cli.app, ["audit", "tags"])
        assert result.exit_code == 0
        assert (tmp_path / "data" / "audit" / "tags.json").exists()

    def test_json_summary(self, fake):
        result = runner.invoke(cli.app, ["audit", "--json"])
        summary = json.loads(result.stdout.strip())
        assert summary["library_version"] == 42
        assert summary["counts"]["tags"] == 1

    def test_unknown_check_is_bad_input(self, fake):
        result = runner.invoke(cli.app, ["audit", "nonsense"])
        assert result.exit_code == 2

    def test_since_passed_through(self, fake):
        result = runner.invoke(cli.app, ["audit", "--since", "41", "--json"])
        summary = json.loads(result.stdout.strip())
        assert summary["counts"]["tags"] == 0  # keyless tag clusters drop out when scoped
