import logging
from typing import List, Dict, Any, Optional
import httpx
from app.models import TorrentInfo
from app.clients.base import BaseTorrentClient

logger = logging.getLogger("defibrillarr.transmission")


class TransmissionClient(BaseTorrentClient):
    client_id: str = "transmission"
    client_name: str = "Transmission"

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        dry_run: bool = False,
    ):
        self.raw_url = base_url.rstrip("/")
        # Normalize RPC endpoint: if base_url is e.g. http://host:9091, append /transmission/rpc
        if not self.raw_url.endswith("/rpc"):
            if self.raw_url.endswith("/transmission"):
                self.rpc_url = f"{self.raw_url}/rpc"
            else:
                self.rpc_url = f"{self.raw_url}/transmission/rpc"
        else:
            self.rpc_url = self.raw_url

        # Store web UI url for dashboard links
        if "/transmission/rpc" in self.rpc_url:
            self.web_url = self.rpc_url.replace("/transmission/rpc", "/transmission/web/")
        else:
            self.web_url = self.raw_url

        self.username = username
        self.password = password
        self.dry_run = dry_run

        auth = httpx.BasicAuth(username, password) if (username or password) else None
        self._client = httpx.AsyncClient(timeout=10.0, auth=auth, follow_redirects=True)
        self._session_id: Optional[str] = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _rpc_request(self, method: str, arguments: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Execute a JSON-RPC request to Transmission daemon.
        Handles Transmission's 409 CSRF session-id handshake automatically.
        """
        payload: Dict[str, Any] = {"method": method, "arguments": arguments or {}}
        headers = {}
        if self._session_id:
            headers["X-Transmission-Session-Id"] = self._session_id

        try:
            resp = await self._client.post(self.rpc_url, json=payload, headers=headers)
            
            # Transmission responds with 409 Conflict and sends the required session id header
            if resp.status_code == 409:
                session_id = resp.headers.get("x-transmission-session-id") or resp.headers.get("X-Transmission-Session-Id")
                if session_id:
                    self._session_id = session_id
                    headers["X-Transmission-Session-Id"] = self._session_id
                    resp = await self._client.post(self.rpc_url, json=payload, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("result") == "success":
                    return data.get("arguments", {})
                else:
                    logger.warning(f"Transmission RPC {method} returned non-success: {data.get('result')}")
                    return None
            else:
                logger.warning(f"Transmission RPC {method} failed with HTTP status {resp.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error communicating with Transmission at {self.rpc_url}: {e}")
            return None

    async def get_version(self) -> Optional[str]:
        """Fetch Transmission daemon version."""
        data = await self._rpc_request("session-get")
        if data and "version" in data:
            return str(data["version"])
        return None

    def _map_state(self, status: int, percent_done: float, rate_download: int, peers_sending: int, error_str: str) -> str:
        """
        Map Transmission integer status codes to normalized Defibrillarr states:
        0: STOPPED
        1: CHECK_WAIT
        2: CHECK
        3: DOWNLOAD_WAIT
        4: DOWNLOAD
        5: SEED_WAIT
        6: SEED
        """
        if error_str:
            return "error"

        if status == 0:
            return "pausedUP" if percent_done >= 1.0 else "pausedDL"
        elif status in (1, 2):
            return "checkingDL"
        elif status == 3:
            return "queuedDL"
        elif status == 4:
            if rate_download <= 0 and peers_sending == 0:
                return "stalledDL"
            return "downloading"
        elif status == 5:
            return "queuedUP"
        elif status == 6:
            return "uploading"
        return "unknown"

    async def get_torrents(self, filter_type: str = "all") -> List[TorrentInfo]:
        """Fetch list of torrents from Transmission normalized as TorrentInfo."""
        fields = [
            "id",
            "hashString",
            "name",
            "status",
            "percentDone",
            "rateDownload",
            "rateUpload",
            "eta",
            "peersSendingToUs",
            "peersGettingFromUs",
            "peersConnected",
            "addedDate",
            "labels",
            "error",
            "errorString",
        ]
        data = await self._rpc_request("torrent-get", {"fields": fields})
        if not data or "torrents" not in data:
            return []

        results: List[TorrentInfo] = []
        for item in data["torrents"]:
            hash_str = (item.get("hashString") or "").lower()
            name = item.get("name") or "Unknown"
            status = int(item.get("status", 0))
            percent_done = float(item.get("percentDone", 0.0))
            dlspeed = int(item.get("rateDownload", 0))
            upspeed = int(item.get("rateUpload", 0))
            eta = int(item.get("eta", 0))
            peers_sending = int(item.get("peersSendingToUs", 0))
            peers_getting = int(item.get("peersGettingFromUs", 0))
            peers_connected = int(item.get("peersConnected", 0))
            num_seeds = max(peers_sending, 0)
            num_leechs = max(peers_getting, peers_connected - peers_sending if peers_connected >= peers_sending else 0)
            added_on = int(item.get("addedDate", 0))
            labels = item.get("labels") or []
            tags_str = ",".join(labels) if isinstance(labels, list) else str(labels)
            error_msg = item.get("errorString") or ""

            state = self._map_state(status, percent_done, dlspeed, peers_sending, error_msg)

            results.append(
                TorrentInfo(
                    hash=hash_str,
                    name=name,
                    state=state,
                    progress=percent_done,
                    dlspeed=dlspeed,
                    upspeed=upspeed,
                    eta=eta,
                    num_seeds=num_seeds,
                    num_leechs=num_leechs,
                    added_on=added_on,
                    tags=tags_str,
                    category="",
                )
            )

        return results

    async def add_trackers(self, torrent_hash: str, trackers: List[str]) -> bool:
        """Inject a list of tracker URLs into a torrent swarm."""
        if not trackers:
            return True
        if self.dry_run:
            logger.info(f"[DRY RUN] Would inject {len(trackers)} trackers into Transmission torrent {torrent_hash}")
            return True

        args = {"ids": [torrent_hash], "trackerAdd": trackers}
        res = await self._rpc_request("torrent-set", args)
        return res is not None

    async def reannounce(self, torrent_hash: str) -> bool:
        """Force re-announce to all trackers."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would force re-announce Transmission torrent {torrent_hash}")
            return True

        args = {"ids": [torrent_hash]}
        res = await self._rpc_request("torrent-reannounce", args)
        return res is not None

    async def add_tags(self, torrent_hash: str, tags: List[str]) -> bool:
        """Add tags/labels to a torrent without overwriting existing labels."""
        if not tags:
            return True
        if self.dry_run:
            logger.info(f"[DRY RUN] Would add tags {tags} to Transmission torrent {torrent_hash}")
            return True

        try:
            # First fetch current labels
            data = await self._rpc_request("torrent-get", {"ids": [torrent_hash], "fields": ["labels"]})
            current_labels: List[str] = []
            if data and "torrents" in data and len(data["torrents"]) > 0:
                raw_labels = data["torrents"][0].get("labels") or []
                if isinstance(raw_labels, list):
                    current_labels = list(raw_labels)

            # Union unique labels
            for tag in tags:
                if tag not in current_labels:
                    current_labels.append(tag)

            res = await self._rpc_request("torrent-set", {"ids": [torrent_hash], "labels": current_labels})
            return res is not None
        except Exception as e:
            logger.error(f"Error adding tags to Transmission torrent {torrent_hash}: {e}")
            return False

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = True) -> bool:
        """Delete torrent and optionally delete local downloaded files."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would delete Transmission torrent {torrent_hash} (delete_files={delete_files})")
            return True

        args = {"ids": [torrent_hash], "delete-local-data": delete_files}
        res = await self._rpc_request("torrent-remove", args)
        return res is not None

    async def recheck(self, torrent_hash: str) -> bool:
        """Force recheck / verify torrent integrity."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would verify Transmission torrent {torrent_hash}")
            return True

        args = {"ids": [torrent_hash]}
        res = await self._rpc_request("torrent-verify", args)
        return res is not None

    async def resume(self, torrent_hash: str) -> bool:
        """Resume / unpause torrent."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would resume Transmission torrent {torrent_hash}")
            return True

        args = {"ids": [torrent_hash]}
        res = await self._rpc_request("torrent-start", args)
        return res is not None
