"""Tests for the audit runner: stamped per-check JSON, --since scoping, report generation."""

import json
from datetime import UTC, datetime

import pytest

from tests.conftest import FakeZotero, make_collection, make_item, make_tag
from zelador.audit import runner
from zelador.client import ZoteroClient
from zelador.config import Credentials
from zelador.taxonomy import Family, TagEntry, Taxonomy

NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


def client_for(fake: FakeZotero) -> ZoteroClient:
    creds = Credentials(api_key="k", user_id="11868292")
    return ZoteroClient(creds, transport=fake.transport, sleep=lambda s: None)


@pytest.fixture
def fake():
    return FakeZotero(
        items=[
            make_item("OLD00001", version=10, title="Old and incomplete", date=""),
            make_item("NEW00001", version=30, title="New and incomplete", date=""),
        ],
        collections=[make_collection("COLL0001", "Empty Shelf")],
        tags=[make_tag("AI"), make_tag("ai")],
        library_version=42,
        page_size=100,
    )


class TestRunAudit:
    def test_writes_stamped_json_per_check_and_report(self, fake, tmp_path):
        summary = runner.run_audit(client_for(fake), tmp_path, now=NOW)
        for name in ("completeness", "tags", "collections", "duplicates"):
            payload = json.loads((tmp_path / f"{name}.json").read_text())
            assert payload["library_version"] == 42
            assert payload["timestamp"] == NOW.isoformat()
        assert summary["counts"]["completeness"] == 2
        assert summary["counts"]["tags"] == 1
        report = (tmp_path / "audit-report.md").read_text()
        assert "completeness" in report
        assert "Old and incomplete" in report

    def test_single_check_writes_only_that_file(self, fake, tmp_path):
        summary = runner.run_audit(client_for(fake), tmp_path, check="tags", now=NOW)
        assert list(summary["counts"]) == ["tags"]
        assert (tmp_path / "tags.json").exists()
        assert not (tmp_path / "completeness.json").exists()

    def test_since_scopes_to_recently_changed_items(self, fake, tmp_path):
        summary = runner.run_audit(client_for(fake), tmp_path, since=20, now=NOW)
        payload = json.loads((tmp_path / "completeness.json").read_text())
        keys = [f["keys"] for f in payload["findings"]]
        assert keys == [["NEW00001"]]
        assert payload["since"] == 20
        # keyless findings (tag clusters) drop out of a scoped run
        assert summary["counts"]["tags"] == 0

    def test_unknown_check_fails_loudly(self, fake, tmp_path):
        with pytest.raises(runner.UnknownCheck, match="nonsense"):
            runner.run_audit(client_for(fake), tmp_path, check="nonsense")

    def test_bib_requested_for_rendered_entries(self, fake, tmp_path):
        runner.run_audit(client_for(fake), tmp_path, style="apa", now=NOW)
        assert any("include=data%2Cbib" in str(r.url) or "include=data,bib" in str(r.url)
                   for r in fake.requests)

    def test_registry_check_runs_when_taxonomy_given(self, fake, tmp_path):
        tax = Taxonomy(families={"status": Family()}, tags=(TagEntry("status:read"),))
        summary = runner.run_audit(client_for(fake), tmp_path, taxonomy=tax, now=NOW)
        assert summary["counts"]["registry"] > 0  # AI/ai tags are unregistered
        payload = json.loads((tmp_path / "registry.json").read_text())
        assert payload["library_version"] == 42

    def test_registry_check_without_taxonomy_is_unknown(self, fake, tmp_path):
        with pytest.raises(runner.UnknownCheck, match="taxonomy.yaml"):
            runner.run_audit(client_for(fake), tmp_path, check="registry")
