"""Tests for the taxonomy registry: parse, lint, colour rules, helper views."""

import pytest

from zelador import taxonomy
from zelador.config import REPO_ROOT

VALID = """
families:
  status: {description: workflow state, coloured: true, exclusive: true}
  topic: {description: subject vocabulary}
tags:
  - tag: status:to-read
    description: waiting to be read
    aliases: [ler, to-read]
    colour: "#E69F00"
  - tag: status:read
    description: finished reading
    aliases: [lido]
    colour: "#009E73"
  - tag: topic:machine-learning
    aliases: [Machine Learning, ML]
"""


def load(tmp_path, text):
    path = tmp_path / "taxonomy.yaml"
    path.write_text(text)
    return taxonomy.load_taxonomy(path)


class TestLoad:
    def test_valid_registry_loads(self, tmp_path):
        tax = load(tmp_path, VALID)
        assert tax.families["status"].exclusive
        assert not tax.families["topic"].coloured
        assert tax.canonical() == {"status:to-read", "status:read", "topic:machine-learning"}
        assert tax.alias_map() == {
            "ler": "status:to-read",
            "to-read": "status:to-read",
            "lido": "status:read",
            "Machine Learning": "topic:machine-learning",
            "ML": "topic:machine-learning",
        }

    def test_known_covers_canonical_and_aliases(self, tmp_path):
        tax = load(tmp_path, VALID)
        assert tax.is_known("status:read")
        assert tax.is_known("ML")
        assert not tax.is_known("weird tag")

    def test_tag_colors_value_in_declared_order(self, tmp_path):
        tax = load(tmp_path, VALID)
        assert tax.tag_colors_value() == [
            {"name": "status:to-read", "color": "#E69F00"},
            {"name": "status:read", "color": "#009E73"},
        ]

    def test_missing_file_fails_loudly(self, tmp_path):
        with pytest.raises(taxonomy.TaxonomyError, match="no taxonomy"):
            taxonomy.load_taxonomy(tmp_path / "absent.yaml")

    def test_root_must_be_mapping_with_families_and_tags(self, tmp_path):
        with pytest.raises(taxonomy.TaxonomyError):
            load(tmp_path, "- just\n- a list\n")
        with pytest.raises(taxonomy.TaxonomyError, match="tags"):
            load(tmp_path, "families:\n  status: {}\n")

    def test_example_registry_is_valid(self):
        tax = taxonomy.load_taxonomy(REPO_ROOT / "taxonomy.example.yaml")
        assert "status" in tax.families
        assert tax.tag_colors_value()[0] == {"name": "status:to-read", "color": "#E69F00"}


class TestLint:
    def test_tag_name_must_be_lowercase_family_value(self, tmp_path):
        bad = "families:\n  status: {}\ntags:\n  - tag: 'Status:Read'\n"
        with pytest.raises(taxonomy.TaxonomyError, match="family:value"):
            load(tmp_path, bad)
        with pytest.raises(taxonomy.TaxonomyError, match="family:value"):
            load(tmp_path, "families:\n  status: {}\ntags:\n  - tag: nocolon\n")

    def test_undeclared_family_rejected(self, tmp_path):
        bad = "families:\n  status: {}\ntags:\n  - tag: rating:fav\n"
        with pytest.raises(taxonomy.TaxonomyError, match="rating"):
            load(tmp_path, bad)

    def test_duplicate_canonical_rejected(self, tmp_path):
        bad = "families:\n  status: {}\ntags:\n  - tag: status:read\n  - tag: status:read\n"
        with pytest.raises(taxonomy.TaxonomyError, match="duplicated"):
            load(tmp_path, bad)

    def test_alias_under_two_tags_rejected(self, tmp_path):
        bad = (
            "families:\n  status: {}\n"
            "tags:\n"
            "  - tag: status:read\n    aliases: [done]\n"
            "  - tag: status:to-read\n    aliases: [done]\n"
        )
        with pytest.raises(taxonomy.TaxonomyError, match="done"):
            load(tmp_path, bad)

    def test_alias_shadowing_canonical_rejected(self, tmp_path):
        bad = (
            "families:\n  status: {}\n"
            "tags:\n"
            "  - tag: status:read\n"
            "  - tag: status:to-read\n    aliases: ['status:read']\n"
        )
        with pytest.raises(taxonomy.TaxonomyError, match="shadow"):
            load(tmp_path, bad)

    def test_colour_outside_okabe_ito_rejected(self, tmp_path):
        bad = (
            "families:\n  status: {coloured: true}\n"
            "tags:\n  - tag: status:read\n    colour: '#FF0000'\n"
        )
        with pytest.raises(taxonomy.TaxonomyError, match="Okabe-Ito"):
            load(tmp_path, bad)

    def test_colour_on_uncoloured_family_rejected(self, tmp_path):
        bad = (
            "families:\n  topic: {}\n"
            "tags:\n  - tag: topic:ai\n    colour: '#0072B2'\n"
        )
        with pytest.raises(taxonomy.TaxonomyError, match="coloured"):
            load(tmp_path, bad)

    def test_more_than_nine_coloured_tags_rejected(self, tmp_path):
        palette = sorted(taxonomy.OKABE_ITO)
        entries = "".join(
            f"  - tag: status:s{i}\n    colour: '{palette[i % len(palette)]}'\n" for i in range(10)
        )
        bad = f"families:\n  status: {{coloured: true}}\ntags:\n{entries}"
        with pytest.raises(taxonomy.TaxonomyError, match="9"):
            load(tmp_path, bad)
