"""Golden plan fixture: one changeset exercising the vocabulary, expansion pinned exactly.

Regenerate after a deliberate contract change with:
    uv run python -m tests.test_golden_plan
then eyeball the diff — the golden file is the reviewed artifact.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.conftest import USER_ID, FakeZotero, make_collection, make_item
from zelador.client import ZoteroClient
from zelador.config import Credentials
from zelador.taxonomy import Family, TagEntry, Taxonomy
from zelador.write.contracts import load_changeset, save_plan
from zelador.write.expand import expand

GOLDEN_DIR = Path(__file__).parent / "golden"
NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)

TAXONOMY = Taxonomy(
    families={"status": Family(coloured=True, exclusive=True), "topic": Family()},
    tags=(
        TagEntry(tag="status:to-read", colour="#E69F00"),
        TagEntry(tag="status:read", colour="#009E73"),
        TagEntry(tag="topic:ai", aliases=("AI", "Artificial Intelligence")),
    ),
)


def library() -> FakeZotero:
    return FakeZotero(
        items=[
            make_item(
                "AAAA1111",
                version=7,
                title="Attention is all you need",
                tags=[{"tag": "AI", "type": 1}, {"tag": "keep-me", "type": 0}],
                collections=["SHLF1111"],
            ),
            make_item(
                "BBBB2222",
                version=9,
                title="Deep learning",
                tags=[{"tag": "Artificial Intelligence", "type": 1}],
            ),
            make_item("CCCC3333", version=11, title="Unrelated", tags=[{"tag": "junk"}]),
        ],
        collections=[
            make_collection("SHLF1111", "AI shelf", version=3),
            make_collection("OLDC2222", "finished-projects", version=4),
        ],
        settings={"tagColors": {"value": [{"name": "ler", "color": "#FF0000"}], "version": 90}},
        library_version=200,
        page_size=100,
    )


def build_plan():
    fake = library()
    client = ZoteroClient(
        Credentials(api_key="k", user_id=USER_ID), transport=fake.transport, sleep=lambda s: None
    )
    changeset = load_changeset(GOLDEN_DIR / "changeset.json")
    counter = iter(range(1, 100))
    return expand(
        changeset,
        client,
        TAXONOMY,
        backup="20260719T115900Z",
        now=NOW,
        keygen=lambda: f"NEWC{next(counter):04d}",
    )


def test_expansion_matches_golden_plan(tmp_path):
    path = save_plan(build_plan(), tmp_path)
    assert json.loads(path.read_text()) == json.loads((GOLDEN_DIR / "plan.json").read_text())


if __name__ == "__main__":
    generated = save_plan(build_plan(), GOLDEN_DIR)
    generated.replace(GOLDEN_DIR / "plan.json")
    print((GOLDEN_DIR / "plan.json").read_text())
