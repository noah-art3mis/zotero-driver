"""Tests for audit check 6: registry conformance (needs a taxonomy)."""

from tests.conftest import make_item, make_tag
from zelador.audit import conformance
from zelador.audit.library import Library
from zelador.taxonomy import Family, TagEntry, Taxonomy

TAX = Taxonomy(
    families={"status": Family(coloured=True, exclusive=True), "topic": Family()},
    tags=(
        TagEntry("status:to-read", aliases=("ler",), colour="#E69F00"),
        TagEntry("status:read", colour="#009E73"),
        TagEntry("topic:ai"),
    ),
)

REGISTRY_COLORS = [
    {"name": "status:to-read", "color": "#E69F00"},
    {"name": "status:read", "color": "#009E73"},
]


def lib(items=None, tags=None, tag_colors=None) -> Library:
    return Library(items=items or [], tags=tags or [], tag_colors=tag_colors or [])


def triaged_item(key="ITEM0001", **kwargs):
    kwargs.setdefault("tags", [{"tag": "status:read"}])
    return make_item(key, **kwargs)


class TestConformance:
    def test_unknown_tags_flagged(self):
        tags = [make_tag("status:read"), make_tag("ler"), make_tag("weird", 7)]
        found = conformance.check(lib(tags=tags, tag_colors=REGISTRY_COLORS), TAX)
        unknown = [f for f in found if f["kind"] == "unknown_tag"]
        assert len(unknown) == 1
        assert "weird" in unknown[0]["message"]
        assert unknown[0]["detail"]["numItems"] == 7

    def test_top_level_item_without_status_tag_is_untriaged(self):
        items = [
            triaged_item(),
            make_item("ITEM0002", tags=[{"tag": "topic:ai"}]),
            make_item("ATT00001", item_type="attachment", parentItem="ITEM0001"),
        ]
        found = conformance.check(lib(items=items, tag_colors=REGISTRY_COLORS), TAX)
        untriaged = [f for f in found if f["kind"] == "untriaged"]
        assert [f["keys"] for f in untriaged] == [["ITEM0002"]]

    def test_exclusive_family_violation_flagged(self):
        items = [triaged_item("ITEM0003", tags=[{"tag": "status:read"}, {"tag": "status:to-read"}])]
        found = conformance.check(lib(items=items, tag_colors=REGISTRY_COLORS), TAX)
        violations = [f for f in found if f["kind"] == "exclusive_violation"]
        assert violations[0]["keys"] == ["ITEM0003"]
        assert "status" in violations[0]["message"]

    def test_matching_tag_colors_no_drift(self):
        found = conformance.check(lib(tag_colors=REGISTRY_COLORS), TAX)
        assert [f for f in found if f["kind"] == "tag_colors_drift"] == []

    def test_tag_colors_drift_flagged(self):
        found = conformance.check(lib(tag_colors=[]), TAX)
        drift = [f for f in found if f["kind"] == "tag_colors_drift"]
        assert len(drift) == 1
        assert drift[0]["detail"]["registry"] == REGISTRY_COLORS
        assert drift[0]["detail"]["library"] == []
