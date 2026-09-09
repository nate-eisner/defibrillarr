from abc import ABC, abstractmethod
from typing import List, Optional
from app.models import TorrentInfo


class BaseTorrentClient(ABC):
    client_id: str
    client_name: str
    web_url: str
    dry_run: bool

    @abstractmethod
    async def close(self) -> None:
        """Close any open client sessions/connections."""
        pass

    @abstractmethod
    async def get_version(self) -> Optional[str]:
        """Fetch daemon/application version string."""
        pass

    @abstractmethod
    async def get_torrents(self, filter_type: str = "all") -> List[TorrentInfo]:
        """Fetch list of torrents normalized as TorrentInfo objects."""
        pass

    @abstractmethod
    async def add_trackers(self, torrent_hash: str, trackers: List[str]) -> bool:
        """Inject a list of tracker URLs into a torrent swarm."""
        pass

    @abstractmethod
    async def reannounce(self, torrent_hash: str) -> bool:
        """Force re-announce to all trackers / DHT."""
        pass

    @abstractmethod
    async def add_tags(self, torrent_hash: str, tags: List[str]) -> bool:
        """Add tags / labels to a torrent."""
        pass

    @abstractmethod
    async def delete_torrent(self, torrent_hash: str, delete_files: bool = True) -> bool:
        """Delete torrent and optionally delete local downloaded files."""
        pass

    @abstractmethod
    async def recheck(self, torrent_hash: str) -> bool:
        """Force recheck / verify torrent integrity."""
        pass

    @abstractmethod
    async def resume(self, torrent_hash: str) -> bool:
        """Resume / unpause torrent."""
        pass
