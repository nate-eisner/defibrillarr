import pytest
from datetime import datetime, timedelta, timezone
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

@pytest.mark.asyncio
async def test_engine_cadence_autoboost():
    config = Settings(
        DRY_RUN=True,
        AUTO_BOOST_CADENCE_MINUTES=60,
    )
    tracker_service = TrackerService(urls=[])
    engine = DefibrillarrEngine(config=config, tracker_service=tracker_service)

    active_torrent = TorrentInfo(
        hash="1122334455667788",
        name="Active.Show.S02E05",
        state="downloading",
        progress=0.45,
        dlspeed=50000,
        upspeed=0,
        eta=1200,
        num_seeds=2,
        num_leechs=3,
        added_on=1600000000
    )

    # First cycle initializes last_boosted_timestamps
    now = datetime.now(timezone.utc)
    engine.last_boosted_timestamps[active_torrent.hash] = now - timedelta(minutes=65)

    # Mock qbit.get_torrents
    async def mock_get_torrents(filter_type="all"):
        return [active_torrent]

    engine.qbit.get_torrents = mock_get_torrents

    await engine.run_cycle()

    # Verify that cadence boost was executed
    assert len(engine.history) == 1
    assert engine.history[0].action == "cadence_boost"
    assert "Periodic auto-boost" in engine.history[0].details

    await engine.stop()

@pytest.mark.asyncio
async def test_engine_ignores_100_percent_completed_stalled_items():
    config = Settings(
        DRY_RUN=True,
        STALL_THRESHOLD_MINUTES=1,
        AUTO_FAILOVER_ENABLED=True
    )
    tracker_service = TrackerService(urls=[])
    engine = DefibrillarrEngine(config=config, tracker_service=tracker_service)

    # 100% complete torrent sitting in stalledUP (finished seeding, 0 B/s)
    completed_stalled_torrent = TorrentInfo(
        hash="complete12345678",
        name="Finished.Movie.2024.1080p",
        state="stalledUP",
        progress=1.0,
        dlspeed=0,
        upspeed=0,
        eta=86400,
        num_seeds=0,
        num_leechs=0,
        added_on=1600000000
    )

    # Pre-populate stalled_records to test that it gets removed
    rec = StalledRecord(completed_stalled_torrent.hash)
    engine.stalled_records[completed_stalled_torrent.hash] = rec

    async def mock_get_torrents(filter_type="all"):
        return [completed_stalled_torrent]

    engine.qbit.get_torrents = mock_get_torrents

    # Run cycle
    await engine.run_cycle()

    # Must be removed from stalled records!
    assert completed_stalled_torrent.hash not in engine.stalled_records
    # History must NOT contain failover or boost for 100% complete items
    assert len(engine.history) == 0

    # In get_unified_queue it must be marked as COMPLETED
    queue = await engine.get_unified_queue()
    assert len(queue) == 1
    assert queue[0].defibrillarr_state == DefibrillarrState.COMPLETED
    assert "100% complete" in queue[0].status_message

    # Overview count check
    overview = await engine.get_overview()
    assert overview.stalled_count == 0
    assert overview.completed_count == 1

    await engine.stop()

@pytest.mark.asyncio
async def test_engine_errored_torrent_handling():
    config = Settings(
        DRY_RUN=True,
        STALL_THRESHOLD_MINUTES=1,
        AUTO_FAILOVER_ENABLED=True
    )
    tracker_service = TrackerService(urls=[])
    engine = DefibrillarrEngine(config=config, tracker_service=tracker_service)

    # 1. Test qBittorrent errored torrent
    errored_torrent = TorrentInfo(
        hash="err1234567890abc",
        name="Errored.Corrupt.Download.1080p",
        state="error",
        progress=0.45,
        dlspeed=0,
        upspeed=0,
        eta=86400,
        num_seeds=0,
        num_leechs=0,
        added_on=1600000000
    )

    async def mock_get_torrents(filter_type="all"):
        return [errored_torrent]

    engine.qbit.get_torrents = mock_get_torrents

    # Run cycle to detect error and trigger auto-recheck attempt
    await engine.run_cycle()

    # Verify state in stalled_records
    rec = engine.stalled_records.get(errored_torrent.hash)
    assert rec is not None
    assert rec.state == DefibrillarrState.ERROR
    assert rec.recheck_attempted is True
    assert "error" in rec.status_message.lower()

    # Verify history event logged
    assert len(engine.history) == 1
    assert engine.history[0].action == "auto_recheck_attempted"

    # Verify get_unified_queue marks as ERROR and is_errored
    queue = await engine.get_unified_queue()
    assert len(queue) == 1
    assert queue[0].defibrillarr_state == DefibrillarrState.ERROR
    assert queue[0].is_errored is True
    assert "qBittorrent error" in queue[0].error_message

    # Verify overview statistics include error_count
    overview = await engine.get_overview()
    assert overview.error_count == 1

    # 2. Test manual recheck action
    recheck_success = await engine.manual_recheck(errored_torrent.hash)
    assert recheck_success is True
    assert engine.history[0].action == "manual_recheck"

    await engine.stop()

