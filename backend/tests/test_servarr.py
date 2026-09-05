import pytest
from app.models import ServarrType
from app.clients.servarr import ServarrClient

@pytest.mark.asyncio
async def test_servarr_search_command_generation():
    # Sonarr test
    sonarr_client = ServarrClient(
        app_type=ServarrType.SONARR,
        base_url="http://mock-sonarr:8989",
        api_key="mock_key",
        dry_run=True
    )
    mock_sonarr_record = {
        "id": 101,
        "episodeId": 456,
        "seriesId": 12,
        "title": "Breaking Bad - S01E01"
    }
    success, cmd = await sonarr_client.trigger_search_for_record(mock_sonarr_record)
    assert success is True
    assert cmd == "EpisodeSearch"

    # Radarr test
    radarr_client = ServarrClient(
        app_type=ServarrType.RADARR,
        base_url="http://mock-radarr:7878",
        api_key="mock_key",
        dry_run=True
    )
    mock_radarr_record = {
        "id": 202,
        "movieId": 789,
        "title": "Inception (2010)"
    }
    success, cmd = await radarr_client.trigger_search_for_record(mock_radarr_record)
    assert success is True
    assert cmd == "MoviesSearch"

    # Lidarr test
    lidarr_client = ServarrClient(
        app_type=ServarrType.LIDARR,
        base_url="http://mock-lidarr:8686",
        api_key="mock_key",
        dry_run=True
    )
    mock_lidarr_record = {
        "id": 303,
        "albumId": 999,
        "artistId": 55,
        "title": "Pink Floyd - The Dark Side of the Moon"
    }
    success, cmd = await lidarr_client.trigger_search_for_record(mock_lidarr_record)
    assert success is True
    assert cmd == "AlbumSearch"

    await sonarr_client.close()
    await radarr_client.close()
    await lidarr_client.close()
