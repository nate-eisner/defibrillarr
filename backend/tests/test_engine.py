import pytest
from datetime import datetime, timedelta
from app.config import Settings
from app.models import TorrentInfo, DefibrillarrState, ServarrType, ServarrQueueItem
from app.services.tracker_service import TrackerService
from app.services.engine import DefibrillarrEngine, StalledRecord

@pytest.mark.asyncio
async def test_tracker_service_fallback():
    service = TrackerService(urls=["http://invalid-url-that-fails.mock"])
    # Fetch should safely fail to remote and retain default fallback trackers
    trackers = await service.refresh_trackers()
    assert len(trackers) > 0
    assert any("tracker.opentrackr.org" in t for t in trackers)

@pytest.mark.asyncio
async def test_engine_stalled_lifecycle():
    config = Settings(
        DRY_RUN=True,
        STALL_THRESHOLD_MINUTES=1,
        RESCUE_GRACE_PERIOD_MINUTES=5,
        MIN_DOWNLOAD_SPEED_KBPS=10.0,
        AUTO_FAILOVER_ENABLED=True
    )
    tracker_service = TrackerService(urls=[])
    engine = DefibrillarrEngine(config=config, tracker_service=tracker_service)

    # Mock stalled torrent
    stalled_torrent = TorrentInfo(
        hash="abcd1234efgh5678",
        name="Test.Show.S01E01.1080p",
        state="stalledDL",
        progress=0.15,
        dlspeed=0,
        upspeed=0,
        eta=86400,
        num_seeds=0,
        num_leechs=1,
        added_on=1600000000
    )

    # 1. Test stage 1 boost execution
    rec = StalledRecord(stalled_torrent.hash)
    await engine._execute_stage_1_boost(stalled_torrent, rec)

    assert rec.state == DefibrillarrState.BOOSTING
    assert rec.boosted_at is not None
    assert rec.grace_period_expires_at is not None
    assert len(engine.history) == 1
    assert engine.history[0].action == "trackers_injected"

    # 2. Test stage 2 failover execution with linked Servarr
    rec.servarr_app = ServarrType.SONARR
    rec.servarr_queue_id = 42
    rec.raw_servarr_record = {"id": 42, "episodeId": 99, "title": "Test Show"}

    # Mock Sonarr client in engine
    from app.clients.servarr import ServarrClient
    engine.servarr_clients[ServarrType.SONARR] = ServarrClient(
        app_type=ServarrType.SONARR,
        base_url="http://mock-sonarr:8989",
        api_key="mock",
        dry_run=True
    )

    success = await engine._execute_stage_2_failover(stalled_torrent, rec)
    assert success is True
    assert rec.state == DefibrillarrState.FAILED_OVER
    assert len(engine.history) == 2
    assert engine.history[0].action == "failover_executed"

    await engine.stop()

@pytest.mark.asyncio
async def test_qbittorrent_login_204_handling():
    import httpx
    from app.clients.qbittorrent import QBittorrentClient

    qbit = QBittorrentClient("http://mock-qbit:8080", "admin", "adminadmin")

    # Mock response returning 204
    async def mock_handler(request):
        if "/api/v2/auth/login" in str(request.url):
            return httpx.Response(204, content=b"")
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    qbit._client = httpx.AsyncClient(transport=transport)

    login_ok = await qbit.login()
    assert login_ok is True
    assert qbit._authenticated is True

    await qbit.close()
