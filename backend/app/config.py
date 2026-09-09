import os
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # General
    APP_NAME: str = "Defibrillarr"
    DEBUG: bool = False
    DRY_RUN: bool = False  # If True, log actions without modifying qBittorrent or Servarr
    HOST: str = "0.0.0.0"
    PORT: int = 8787

    # Polling & Thresholds
    POLL_INTERVAL_SECONDS: int = 60
    STALL_THRESHOLD_MINUTES: int = 15  # Minutes stalled/slow before Stage 1 (Rescue/Trackers)
    RESCUE_GRACE_PERIOD_MINUTES: int = 60  # Minutes in rescue mode before Stage 2 (Failover)
    MIN_DOWNLOAD_SPEED_KBPS: float = 10.0  # Speeds below this count as stalled
    AUTO_FAILOVER_ENABLED: bool = True  # Automatically remove, blocklist, and re-search
    AUTO_BOOST_CADENCE_MINUTES: int = 120  # Automatic recurring tracker re-boost cadence (0 = disabled)

    # Torrent Client Selection ("qbittorrent" or "transmission")
    TORRENT_CLIENT: str = "qbittorrent"

    # qBittorrent Settings
    QBIT_URL: str = "http://localhost:8080"
    QBIT_USERNAME: str = "admin"
    QBIT_PASSWORD: str = "adminadmin"
    QBIT_TAG_BOOSTED: str = "defibrillarr-boosted"
    QBIT_TAG_PROBATION: str = "defibrillarr-probation"

    # Transmission Settings
    TRANSMISSION_URL: str = "http://localhost:9091/transmission/rpc"
    TRANSMISSION_USERNAME: str = ""
    TRANSMISSION_PASSWORD: str = ""

    # Servarr Settings (Sonarr, Radarr, Lidarr)
    SONARR_URL: Optional[str] = None
    SONARR_API_KEY: Optional[str] = None

    RADARR_URL: Optional[str] = None
    RADARR_API_KEY: Optional[str] = None

    LIDARR_URL: Optional[str] = None
    LIDARR_API_KEY: Optional[str] = None

    # Trackers List Sources
    TRACKER_LIST_URLS: List[str] = Field(
        default_factory=lambda: [
            "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
            "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_all_udp.txt",
            "https://newtrackon.com/api/stable"
        ]
    )

settings = Settings()
