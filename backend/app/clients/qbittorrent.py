import logging
from typing import List, Dict, Any, Optional
import httpx
from app.models import TorrentInfo

logger = logging.getLogger("defibrillarr.qbittorrent")

class QBittorrentClient:
    def __init__(self, base_url: str, username: str = "admin", password: str = "adminadmin", dry_run: bool = False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.dry_run = dry_run
        self._client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        self._authenticated = False

    async def close(self):
        await self._client.aclose()

    async def login(self) -> bool:
        """Authenticate with qBittorrent Web API."""
        try:
            url = f"{self.base_url}/api/v2/auth/login"
            data = {"username": self.username, "password": self.password}
            response = await self._client.post(url, data=data)
            # qBittorrent returns:
            # - 200 with "Ok." in legacy versions
            # - 204 No Content in qBittorrent 4.x / 5.x
            # - 200 with "Fails." or 403 on invalid credentials
            is_success = (
                response.status_code in (200, 204) and
                response.text.strip().lower() != "fails."
            )
            if is_success:
                self._authenticated = True
                logger.info("Successfully authenticated with qBittorrent")
                return True
            else:
                logger.warning(f"qBittorrent login failed: status {response.status_code}, response: {response.text}")
                self._authenticated = False
                return False
        except Exception as e:
            logger.error(f"Error connecting to qBittorrent at {self.base_url}: {e}")
            self._authenticated = False
            return False

    async def _ensure_auth(self) -> bool:
        if not self._authenticated:
            return await self.login()
        return True

    async def get_version(self) -> Optional[str]:
        """Fetch qBittorrent application version."""
        if not await self._ensure_auth():
            return None
        try:
            resp = await self._client.get(f"{self.base_url}/api/v2/app/version")
            if resp.status_code == 200:
                return resp.text.strip()
            elif resp.status_code in (401, 403):
                # Retry auth once
                if await self.login():
                    resp = await self._client.get(f"{self.base_url}/api/v2/app/version")
                    return resp.text.strip() if resp.status_code == 200 else None
            return None
        except Exception as e:
            logger.error(f"Failed to get qBittorrent version: {e}")
            return None

    async def get_torrents(self, filter_type: str = "all") -> List[TorrentInfo]:
        """Fetch list of torrents."""
        if not await self._ensure_auth():
            return []
        try:
            resp = await self._client.get(f"{self.base_url}/api/v2/torrents/info?filter={filter_type}")
            if resp.status_code in (401, 403):
                if await self.login():
                    resp = await self._client.get(f"{self.base_url}/api/v2/torrents/info?filter={filter_type}")
                else:
                    return []
            if resp.status_code == 200:
                raw_list = resp.json()
                results = []
                for item in raw_list:
                    results.append(
                        TorrentInfo(
                            hash=item.get("hash", "").lower(),
                            name=item.get("name", "Unknown"),
                            state=item.get("state", "unknown"),
                            progress=float(item.get("progress", 0.0)),
                            dlspeed=int(item.get("dlspeed", 0)),
                            upspeed=int(item.get("upspeed", 0)),
                            eta=int(item.get("eta", 0)),
                            num_seeds=int(item.get("num_seeds", 0)),
                            num_leechs=int(item.get("num_leechs", 0)),
                            added_on=int(item.get("added_on", 0)),
                            tags=item.get("tags", ""),
                            category=item.get("category", "")
                        )
                    )
                return results
            return []
        except Exception as e:
            logger.error(f"Error fetching torrents from qBittorrent: {e}")
            return []

    async def add_trackers(self, torrent_hash: str, trackers: List[str]) -> bool:
        """Inject a list of tracker URLs into a torrent."""
        if not trackers:
            return True
        if self.dry_run:
            logger.info(f"[DRY RUN] Would inject {len(trackers)} trackers into torrent {torrent_hash}")
            return True
        if not await self._ensure_auth():
            return False
        try:
            urls_payload = "\n\n".join(trackers)
            url = f"{self.base_url}/api/v2/torrents/addTrackers"
            data = {"hash": torrent_hash, "urls": urls_payload}
            resp = await self._client.post(url, data=data)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Error adding trackers to {torrent_hash}: {e}")
            return False

    async def reannounce(self, torrent_hash: str) -> bool:
        """Force re-announce to all trackers."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would force re-announce for torrent {torrent_hash}")
            return True
        if not await self._ensure_auth():
            return False
        try:
            url = f"{self.base_url}/api/v2/torrents/reannounce"
            data = {"hashes": torrent_hash}
            resp = await self._client.post(url, data=data)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Error re-announcing {torrent_hash}: {e}")
            return False

    async def add_tags(self, torrent_hash: str, tags: List[str]) -> bool:
        """Add tags to a torrent."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would add tags {tags} to torrent {torrent_hash}")
            return True
        if not await self._ensure_auth():
            return False
        try:
            url = f"{self.base_url}/api/v2/torrents/addTags"
            data = {"hashes": torrent_hash, "tags": ",".join(tags)}
            resp = await self._client.post(url, data=data)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Error adding tags to {torrent_hash}: {e}")
            return False

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = True) -> bool:
        """Delete torrent and optionally files from qBittorrent."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would delete torrent {torrent_hash} (delete_files={delete_files})")
            return True
        if not await self._ensure_auth():
            return False
        try:
            url = f"{self.base_url}/api/v2/torrents/delete"
            data = {"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"}
            resp = await self._client.post(url, data=data)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Error deleting torrent {torrent_hash}: {e}")
            return False

    async def recheck(self, torrent_hash: str) -> bool:
        """Force recheck torrent data integrity."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would force recheck torrent {torrent_hash}")
            return True
        if not await self._ensure_auth():
            return False
        try:
            url = f"{self.base_url}/api/v2/torrents/recheck"
            data = {"hashes": torrent_hash}
            resp = await self._client.post(url, data=data)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Error rechecking torrent {torrent_hash}: {e}")
            return False

    async def resume(self, torrent_hash: str) -> bool:
        """Resume / unpause a torrent."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would resume torrent {torrent_hash}")
            return True
        if not await self._ensure_auth():
            return False
        try:
            url = f"{self.base_url}/api/v2/torrents/resume"
            data = {"hashes": torrent_hash}
            resp = await self._client.post(url, data=data)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Error resuming torrent {torrent_hash}: {e}")
            return False

