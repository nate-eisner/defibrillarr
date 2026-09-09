import pytest
import httpx
from unittest.mock import AsyncMock, patch
from app.clients.transmission import TransmissionClient
from app.config import Settings
from app.clients.factory import create_torrent_client


def test_transmission_url_normalization():
    c1 = TransmissionClient(base_url="http://192.168.1.100:9091")
    assert c1.rpc_url == "http://192.168.1.100:9091/transmission/rpc"
    assert c1.web_url == "http://192.168.1.100:9091/transmission/web/"

    c2 = TransmissionClient(base_url="http://192.168.1.100:9091/transmission")
    assert c2.rpc_url == "http://192.168.1.100:9091/transmission/rpc"

    c3 = TransmissionClient(base_url="http://192.168.1.100:9091/transmission/rpc")
    assert c3.rpc_url == "http://192.168.1.100:9091/transmission/rpc"


def test_factory_creation():
    qbit_settings = Settings(TORRENT_CLIENT="qbittorrent")
    client_qbit = create_torrent_client(qbit_settings)
    assert client_qbit.client_id == "qbittorrent"

    trans_settings = Settings(
        TORRENT_CLIENT="transmission",
        TRANSMISSION_URL="http://my-host:9091/transmission/rpc",
    )
    client_trans = create_torrent_client(trans_settings)
    assert client_trans.client_id == "transmission"
    assert client_trans.rpc_url == "http://my-host:9091/transmission/rpc"


@pytest.mark.asyncio
async def test_transmission_csrf_and_get_version():
    client = TransmissionClient(base_url="http://mock-trans:9091")

    call_count = 0

    async def mock_post(url, json=None, headers=None):
        nonlocal call_count
        call_count += 1
        # First call: return 409 Conflict with session id
        if call_count == 1:
            return httpx.Response(
                status_code=409,
                headers={"x-transmission-session-id": "test-session-token-123"},
                request=httpx.Request("POST", url),
            )
        # Second call: verify header was sent
        assert headers.get("X-Transmission-Session-Id") == "test-session-token-123"
        assert json.get("method") == "session-get"
        return httpx.Response(
            status_code=200,
            json={"result": "success", "arguments": {"version": "4.0.5", "rpc-version": 17}},
            request=httpx.Request("POST", url),
        )

    with patch.object(client._client, "post", side_effect=mock_post):
        ver = await client.get_version()
        assert ver == "4.0.5"
        assert client._session_id == "test-session-token-123"
        assert call_count == 2


@pytest.mark.asyncio
async def test_transmission_get_torrents_and_mapping():
    client = TransmissionClient(base_url="http://mock-trans:9091")
    client._session_id = "existing-token"

    mock_torrents_payload = [
        # 1. Healthy downloading
        {
            "id": 1,
            "hashString": "1111AAAA2222BBBB3333CCCC4444DDDD5555EEEE",
            "name": "Active.Download.1080p",
            "status": 4,  # DOWNLOAD
            "percentDone": 0.45,
            "rateDownload": 2500000,
            "rateUpload": 50000,
            "eta": 300,
            "peersSendingToUs": 12,
            "peersGettingFromUs": 3,
            "peersConnected": 20,
            "addedDate": 1700000000,
            "labels": ["sonarr"],
            "error": 0,
            "errorString": "",
        },
        # 2. Stalled downloading (speed 0, 0 seeds)
        {
            "id": 2,
            "hashString": "AAAA1111BBBB2222CCCC3333DDDD4444EEEE5555",
            "name": "Stalled.Show.720p",
            "status": 4,  # DOWNLOAD
            "percentDone": 0.10,
            "rateDownload": 0,
            "rateUpload": 0,
            "eta": -1,
            "peersSendingToUs": 0,
            "peersGettingFromUs": 0,
            "peersConnected": 1,
            "addedDate": 1700001000,
            "labels": ["defibrillarr-boosted"],
            "error": 0,
            "errorString": "",
        },
        # 3. Completed / Seeding
        {
            "id": 3,
            "hashString": "FFFF0000FFFF0000FFFF0000FFFF0000FFFF0000",
            "name": "Completed.Movie.2160p",
            "status": 6,  # SEED
            "percentDone": 1.0,
            "rateDownload": 0,
            "rateUpload": 100000,
            "eta": 0,
            "peersSendingToUs": 0,
            "peersGettingFromUs": 5,
            "peersConnected": 5,
            "addedDate": 1700002000,
            "labels": [],
            "error": 0,
            "errorString": "",
        },
        # 4. Errored torrent
        {
            "id": 4,
            "hashString": "EEEE9999EEEE9999EEEE9999EEEE9999EEEE9999",
            "name": "Corrupt.Data.Torrent",
            "status": 0,
            "percentDone": 0.30,
            "rateDownload": 0,
            "rateUpload": 0,
            "eta": 0,
            "peersSendingToUs": 0,
            "peersGettingFromUs": 0,
            "peersConnected": 0,
            "addedDate": 1700003000,
            "labels": [],
            "error": 3,
            "errorString": "No data found! Ensure drive is mounted.",
        },
    ]

    async def mock_post(url, json=None, headers=None):
        return httpx.Response(
            status_code=200,
            json={"result": "success", "arguments": {"torrents": mock_torrents_payload}},
            request=httpx.Request("POST", url),
        )

    with patch.object(client._client, "post", side_effect=mock_post):
        torrents = await client.get_torrents()
        assert len(torrents) == 4

        # Verify active
        t1 = torrents[0]
        assert t1.name == "Active.Download.1080p"
        assert t1.state == "downloading"
        assert t1.progress == 0.45
        assert t1.num_seeds == 12
        assert t1.tags == "sonarr"

        # Verify stalled
        t2 = torrents[1]
        assert t2.state == "stalledDL"
        assert t2.dlspeed == 0
        assert t2.num_seeds == 0
        assert "defibrillarr-boosted" in t2.tags

        # Verify completed
        t3 = torrents[2]
        assert t3.state == "uploading"
        assert t3.progress == 1.0

        # Verify errored
        t4 = torrents[3]
        assert t4.state == "error"


@pytest.mark.asyncio
async def test_transmission_actions():
    client = TransmissionClient(base_url="http://mock-trans:9091")
    client._session_id = "token"

    last_request = {}

    async def mock_post(url, json=None, headers=None):
        nonlocal last_request
        last_request = json
        method = json.get("method")
        if method == "torrent-get":
            return httpx.Response(
                status_code=200,
                json={"result": "success", "arguments": {"torrents": [{"id": 1, "labels": ["existing-tag"]}]}},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            status_code=200,
            json={"result": "success", "arguments": {}},
            request=httpx.Request("POST", url),
        )

    with patch.object(client._client, "post", side_effect=mock_post):
        # 1. Add Trackers
        trackers = ["udp://tracker.open.org:1337/announce"]
        ok = await client.add_trackers("hash123", trackers)
        assert ok is True
        assert last_request["method"] == "torrent-set"
        assert last_request["arguments"]["trackerAdd"] == trackers

        # 2. Reannounce
        ok = await client.reannounce("hash123")
        assert ok is True
        assert last_request["method"] == "torrent-reannounce"
        assert last_request["arguments"]["ids"] == ["hash123"]

        # 3. Add Tags (merging existing labels)
        ok = await client.add_tags("hash123", ["defibrillarr-boosted"])
        assert ok is True
        assert last_request["method"] == "torrent-set"
        assert set(last_request["arguments"]["labels"]) == {"existing-tag", "defibrillarr-boosted"}

        # 4. Delete Torrent
        ok = await client.delete_torrent("hash123", delete_files=True)
        assert ok is True
        assert last_request["method"] == "torrent-remove"
        assert last_request["arguments"]["delete-local-data"] is True

        # 5. Recheck (verify)
        ok = await client.recheck("hash123")
        assert ok is True
        assert last_request["method"] == "torrent-verify"

        # 6. Resume
        ok = await client.resume("hash123")
        assert ok is True
        assert last_request["method"] == "torrent-start"


@pytest.mark.asyncio
async def test_transmission_dry_run():
    client = TransmissionClient(base_url="http://mock-trans:9091", dry_run=True)
    # None of these should make network calls in dry_run mode
    assert await client.add_trackers("hash", ["tracker1"]) is True
    assert await client.reannounce("hash") is True
    assert await client.add_tags("hash", ["tag"]) is True
    assert await client.delete_torrent("hash") is True
    assert await client.recheck("hash") is True
    assert await client.resume("hash") is True
