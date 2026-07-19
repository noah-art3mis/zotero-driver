"""CLI tests for zel local."""

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from zelador import cli
from zelador.config import Config

runner = CliRunner()


@pytest.fixture
def zotero_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ZELADOR_DATA_DIR", str(tmp_path / "data"))
    d = tmp_path / "Zotero"
    d.mkdir()
    conn = sqlite3.connect(d / "zotero.sqlite")
    conn.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT)")
    conn.execute("INSERT INTO items VALUES (1, 'AAAA1111')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cli.config, "load_config", lambda: Config(zotero_data_dir=d))
    return d


class TestLocalCommand:
    def test_query_renders_table(self, zotero_dir):
        result = runner.invoke(cli.app, ["local", "SELECT key FROM items"])
        assert result.exit_code == 0
        assert "AAAA1111" in result.output
        assert "key" in result.output

    def test_json_rows(self, zotero_dir):
        result = runner.invoke(cli.app, ["local", "SELECT itemID, key FROM items", "--json"])
        assert result.exit_code == 0
        rows = [json.loads(line) for line in result.stdout.strip().splitlines()]
        assert rows == [{"itemID": 1, "key": "AAAA1111"}]

    def test_write_attempt_exits_1(self, zotero_dir):
        result = runner.invoke(cli.app, ["local", "DELETE FROM items"])
        assert result.exit_code == 1

    def test_missing_zotero_dir_exits_1(self, zotero_dir, tmp_path, monkeypatch):
        gone = tmp_path / "gone"
        gone.mkdir()
        monkeypatch.setattr(cli.config, "load_config", lambda: Config(zotero_data_dir=gone))
        result = runner.invoke(cli.app, ["local", "SELECT 1"])
        assert result.exit_code == 1
