"""Tests for zelador.client — pagination, batching, backoff, redaction, endpoint scoping."""

import httpx
import pytest

from tests.conftest import USER_ID, FakeZotero, make_item, make_tag
from zelador.client import ZoteroClient, ZoteroError
from zelador.config import Credentials

CREDS = Credentials(api_key="topsecret123", user_id=USER_ID)


def client_for(fake: FakeZotero, **kwargs) -> ZoteroClient:
    kwargs.setdefault("sleep", lambda s: None)
    return ZoteroClient(CREDS, transport=fake.transport, **kwargs)


class TestPagination:
    def test_follows_link_next_across_pages(self):
        fake = FakeZotero(items=[make_item(f"K{i}", version=i + 1) for i in range(5)], page_size=2)
        client = client_for(fake)
        items = client.all_items()
        assert [i["key"] for i in items] == ["K0", "K1", "K2", "K3", "K4"]
        assert len(fake.requests) == 3

    def test_captures_library_version(self):
        fake = FakeZotero(items=[make_item("A")], library_version=99)
        client = client_for(fake)
        client.all_items()
        assert client.last_modified_version == 99

    def test_since_param_passed_through(self):
        fake = FakeZotero(items=[make_item("OLD", version=5), make_item("NEW", version=50)])
        client = client_for(fake)
        items = client.all_items(since=10)
        assert [i["key"] for i in items] == ["NEW"]

    def test_trashed_items_included_when_asked(self):
        fake = FakeZotero(items=[make_item("LIVE"), make_item("GONE", deleted=True)])
        client = client_for(fake)
        assert len(client.all_items()) == 1
        assert len(client.all_items(include_trashed=True)) == 2


class TestBatchReads:
    def test_chunks_keys_at_50(self):
        keys = [f"K{i:03d}" for i in range(120)]
        fake = FakeZotero(items=[make_item(k) for k in keys], page_size=100)
        client = client_for(fake)
        items = client.items_batch(keys)
        assert len(items) == 120
        item_requests = [r for r in fake.requests if "itemKey" in str(r.url)]
        assert len(item_requests) == 3
        first_batch = str(item_requests[0].url)
        assert "K000" in first_batch and "K049" in first_batch and "K050" not in first_batch


class TestBackoffAndRetry:
    def test_retries_429_after_retry_after(self):
        fake = FakeZotero(items=[make_item("A")])
        fake.script.append(httpx.Response(429, headers={"Retry-After": "3"}))
        sleeps = []
        client = client_for(fake, sleep=sleeps.append)
        items = client.all_items()
        assert [i["key"] for i in items] == ["A"]
        assert 3 in sleeps

    def test_gives_up_after_max_retries(self):
        fake = FakeZotero()
        for _ in range(10):
            fake.script.append(httpx.Response(429, headers={"Retry-After": "1"}))
        client = client_for(fake)
        with pytest.raises(ZoteroError, match="429"):
            client.all_items()

    def test_backoff_header_on_200_delays_next_request(self):
        fake = FakeZotero(items=[make_item("A"), make_item("B"), make_item("C")], page_size=2)
        first_page = fake._paginated(fake.items, {"limit": "2"}, f"/users/{USER_ID}/items")
        first_page.headers["Backoff"] = "5"
        fake.script.append(first_page)
        sleeps = []
        client = client_for(fake, sleep=sleeps.append)
        client.all_items()
        assert 5 in sleeps


class TestErrors:
    def test_http_error_raises_zotero_error(self):
        fake = FakeZotero()
        fake.script.append(httpx.Response(500, text="server exploded"))
        client = client_for(fake)
        with pytest.raises(ZoteroError, match="500"):
            client.all_items()

    def test_api_key_redacted_from_errors(self):
        fake = FakeZotero()
        fake.script.append(httpx.Response(403, text="bad key topsecret123 rejected"))
        client = client_for(fake)
        with pytest.raises(ZoteroError) as exc_info:
            client.all_items()
        assert "topsecret123" not in str(exc_info.value)

    def test_network_error_redacts_key(self):
        def explode(request):
            raise httpx.ConnectError("cannot reach host with topsecret123 attached")

        client = ZoteroClient(CREDS, transport=httpx.MockTransport(explode), sleep=lambda s: None)
        with pytest.raises(ZoteroError) as exc_info:
            client.all_items()
        assert "topsecret123" not in str(exc_info.value)


class TestEndpointScoping:
    def test_all_paths_are_user_scoped(self):
        fake = FakeZotero(items=[make_item("A")], tags=[make_tag("x")])
        client = client_for(fake)
        client.all_items()
        client.all_tags()
        client.all_collections()
        for request in fake.requests:
            assert f"/users/{USER_ID}/" in str(request.url)

    def test_key_info_uses_keys_current(self):
        fake = FakeZotero()
        client = client_for(fake)
        info = client.key_info()
        assert info["username"] == "noah-art3mis"
        assert str(fake.requests[-1].url).endswith("/keys/current")

    def test_api_key_sent_as_header(self):
        fake = FakeZotero(items=[])
        client = client_for(fake)
        client.all_items()
        assert fake.requests[0].headers["Zotero-API-Key"] == "topsecret123"
        assert fake.requests[0].headers["Zotero-API-Version"] == "3"


class TestConditionalGet:
    def test_returns_none_when_not_modified(self):
        fake = FakeZotero(items=[make_item("A")], library_version=42)
        client = client_for(fake)
        assert client.all_items(if_modified_since=42) is None

    def test_returns_items_when_modified(self):
        fake = FakeZotero(items=[make_item("A")], library_version=42)
        client = client_for(fake)
        items = client.all_items(if_modified_since=41)
        assert [i["key"] for i in items] == ["A"]


class TestSettings:
    def test_reads_setting(self):
        colors = {"value": [{"name": "x", "color": "#FF0000"}], "version": 7}
        fake = FakeZotero(settings={"tagColors": colors})
        client = client_for(fake)
        assert client.setting("tagColors")["version"] == 7

    def test_missing_setting_is_none(self):
        fake = FakeZotero()
        client = client_for(fake)
        assert client.setting("tagColors") is None


class TestLibraryVersion:
    def test_single_cheap_request(self):
        fake = FakeZotero(items=[make_item(f"K{i}") for i in range(10)], library_version=77)
        client = client_for(fake)
        assert client.library_version() == 77
        assert len(fake.requests) == 1


class TestLyingPaginationMetadata:
    def test_full_page_without_next_link_probes_further(self):
        # Zotero's /tags endpoint under-reports Total-Results and stops
        # emitting Link: next while more pages exist (observed live: 586
        # claimed, 1510 real). A full page therefore never means done —
        # the client must probe the next offset until a short page.
        fake = FakeZotero(
            tags=[make_tag(f"t{i}") for i in range(250)],
            page_size=100,
            emit_next_links=False,
        )
        client = client_for(fake)
        assert len(client.all_tags()) == 250
        assert len(fake.requests) == 3  # 100 + 100 + 50

    def test_short_page_without_next_link_still_terminates(self):
        fake = FakeZotero(tags=[make_tag("only")], page_size=100, emit_next_links=False)
        client = client_for(fake)
        assert len(client.all_tags()) == 1
        assert len(fake.requests) == 1
