import logging
from typing import List, Dict, Any, Optional, Tuple
import httpx
from app.models import ServarrType, ServarrQueueItem

logger = logging.getLogger("defibrillarr.servarr")

class ServarrClient:
    def __init__(self, app_type: ServarrType, base_url: str, api_key: str, dry_run: bool = False):
        self.app_type = app_type
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dry_run = dry_run
        self._headers = {"X-Api-Key": self.api_key, "Accept": "application/json"}
        self._client = httpx.AsyncClient(timeout=10.0, headers=self._headers, follow_redirects=True)

    async def close(self):
        await self._client.aclose()

    async def get_system_status(self) -> Optional[Dict[str, Any]]:
        """Fetch system status / version from Servarr instance."""
        try:
            resp = await self._client.get(f"{self.base_url}/api/v3/system/status")
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"[{self.app_type}] get_system_status returned {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"[{self.app_type}] Error reaching {self.base_url}: {e}")
            return None

    async def get_queue(self) -> List[Tuple[ServarrQueueItem, Dict[str, Any]]]:
        """
        Fetch active queue and return both parsed ServarrQueueItem and raw record
        (raw record is preserved for triggering specific search commands).
        """
        try:
            endpoint = f"{self.base_url}/api/v3/queue"
            params: Dict[str, Any] = {"page": 1, "pageSize": 100}
            if self.app_type == ServarrType.SONARR:
                params["includeSeries"] = "true"
                params["includeEpisode"] = "true"
            elif self.app_type == ServarrType.RADARR:
                params["includeMovie"] = "true"
            elif self.app_type == ServarrType.LIDARR:
                params["includeArtist"] = "true"
                params["includeAlbum"] = "true"

            resp = await self._client.get(endpoint, params=params)
            if resp.status_code != 200:
                logger.warning(f"[{self.app_type}] Failed to get queue: status {resp.status_code}")
                return []

            data = resp.json()
            records = data.get("records", []) if isinstance(data, dict) else data

            items = []
            for rec in records:
                download_id = (rec.get("downloadId") or "").lower()
                queue_id = rec.get("id")
                title = rec.get("title") or "Unknown"
                status = rec.get("status") or "Unknown"
                tracked_state = rec.get("trackedDownloadState")
                error_msg = rec.get("errorMessage")

                series_title = None
                episode_title = None
                movie_title = None
                artist_title = None
                album_title = None

                if self.app_type == ServarrType.SONARR:
                    series = rec.get("series", {})
                    episode = rec.get("episode", {})
                    series_title = series.get("title") if isinstance(series, dict) else None
                    episode_title = episode.get("title") if isinstance(episode, dict) else None
                elif self.app_type == ServarrType.RADARR:
                    movie = rec.get("movie", {})
                    movie_title = movie.get("title") if isinstance(movie, dict) else None
                elif self.app_type == ServarrType.LIDARR:
                    artist = rec.get("artist", {})
                    album = rec.get("album", {})
                    artist_title = artist.get("artistName") if isinstance(artist, dict) else None
                    album_title = album.get("title") if isinstance(album, dict) else None

                queue_item = ServarrQueueItem(
                    id=queue_id,
                    app=self.app_type,
                    title=title,
                    download_id=download_id,
                    series_title=series_title,
                    episode_title=episode_title,
                    movie_title=movie_title,
                    artist_title=artist_title,
                    album_title=album_title,
                    status=status,
                    tracked_download_state=tracked_state,
                    error_message=error_msg
                )
                items.append((queue_item, rec))

            return items
        except Exception as e:
            logger.error(f"[{self.app_type}] Error reading queue: {e}")
            return []

    async def remove_and_blocklist(self, queue_id: int) -> bool:
        """
        Delete queue item from Servarr, remove it from the download client,
        and add the release to the blocklist.
        """
        if self.dry_run:
            logger.info(f"[{self.app_type}] [DRY RUN] Would remove queue item {queue_id} & blocklist release")
            return True
        try:
            url = f"{self.base_url}/api/v3/queue/{queue_id}"
            params = {"removeFromClient": "true", "blocklist": "true"}
            resp = await self._client.delete(url, params=params)
            if resp.status_code in (200, 204):
                logger.info(f"[{self.app_type}] Successfully removed & blocklisted queue item {queue_id}")
                return True
            else:
                logger.warning(f"[{self.app_type}] Failed to remove & blocklist queue {queue_id}: status {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"[{self.app_type}] Error during remove_and_blocklist for {queue_id}: {e}")
            return False

    async def trigger_search_for_record(self, raw_record: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Triggers an automatic search for replacement media based on the raw queue record.
        Returns (success: bool, command_name: str).
        """
        command_body: Dict[str, Any] = {}

        if self.app_type == ServarrType.SONARR:
            episode_id = raw_record.get("episodeId") or (raw_record.get("episode", {}).get("id") if isinstance(raw_record.get("episode"), dict) else None)
            series_id = raw_record.get("seriesId") or (raw_record.get("series", {}).get("id") if isinstance(raw_record.get("series"), dict) else None)
            if episode_id:
                command_body = {"name": "EpisodeSearch", "episodeIds": [episode_id]}
            elif series_id:
                command_body = {"name": "SeriesSearch", "seriesId": series_id}

        elif self.app_type == ServarrType.RADARR:
            movie_id = raw_record.get("movieId") or (raw_record.get("movie", {}).get("id") if isinstance(raw_record.get("movie"), dict) else None)
            if movie_id:
                command_body = {"name": "MoviesSearch", "movieIds": [movie_id]}

        elif self.app_type == ServarrType.LIDARR:
            album_id = raw_record.get("albumId") or (raw_record.get("album", {}).get("id") if isinstance(raw_record.get("album"), dict) else None)
            artist_id = raw_record.get("artistId") or (raw_record.get("artist", {}).get("id") if isinstance(raw_record.get("artist"), dict) else None)
            if album_id:
                command_body = {"name": "AlbumSearch", "albumIds": [album_id]}
            elif artist_id:
                command_body = {"name": "ArtistSearch", "artistId": artist_id}

        if not command_body:
            logger.warning(f"[{self.app_type}] Could not determine search target IDs from record")
            return False, None

        cmd_name = command_body.get("name", "Search")
        if self.dry_run:
            logger.info(f"[{self.app_type}] [DRY RUN] Would execute command: {command_body}")
            return True, cmd_name

        try:
            url = f"{self.base_url}/api/v3/command"
            resp = await self._client.post(url, json=command_body)
            if resp.status_code in (200, 201):
                logger.info(f"[{self.app_type}] Successfully dispatched {cmd_name}")
                return True, cmd_name
            else:
                logger.warning(f"[{self.app_type}] Command {cmd_name} failed with status {resp.status_code}: {resp.text}")
                return False, cmd_name
        except Exception as e:
            logger.error(f"[{self.app_type}] Error executing command {cmd_name}: {e}")
            return False, cmd_name
