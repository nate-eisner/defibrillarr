from app.config import Settings
from app.clients.base import BaseTorrentClient
from app.clients.qbittorrent import QBittorrentClient
from app.clients.transmission import TransmissionClient


def create_torrent_client(config: Settings) -> BaseTorrentClient:
    """Factory creating the configured torrent client (qBittorrent or Transmission)."""
    client_type = (config.TORRENT_CLIENT or "qbittorrent").strip().lower()
    if client_type == "transmission":
        return TransmissionClient(
            base_url=config.TRANSMISSION_URL,
            username=config.TRANSMISSION_USERNAME,
            password=config.TRANSMISSION_PASSWORD,
            dry_run=config.DRY_RUN,
        )
    return QBittorrentClient(
        base_url=config.QBIT_URL,
        username=config.QBIT_USERNAME,
        password=config.QBIT_PASSWORD,
        dry_run=config.DRY_RUN,
    )
