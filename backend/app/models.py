from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class ServarrType(str, Enum):
    SONARR = "sonarr"
    RADARR = "radarr"
    LIDARR = "lidarr"

class DefibrillarrState(str, Enum):
    HEALTHY = "healthy"
    STALLED = "stalled"
    BOOSTING = "boosting"
    PROBATION_EXPIRED = "probation_expired"
    FAILED_OVER = "failed_over"

class TorrentInfo(BaseModel):
    hash: str
    name: str
    state: str
    progress: float  # 0.0 to 1.0
    dlspeed: int  # bytes per sec
    upspeed: int  # bytes per sec
    eta: int  # seconds
    num_seeds: int
    num_leechs: int
    added_on: int
    tags: str = ""
    category: str = ""

class ServarrQueueItem(BaseModel):
    id: int
    app: ServarrType
    title: str
    download_id: str  # Hash or client identifier
    series_title: Optional[str] = None
    episode_title: Optional[str] = None
    movie_title: Optional[str] = None
    artist_title: Optional[str] = None
    album_title: Optional[str] = None
    status: str
    tracked_download_state: Optional[str] = None
    error_message: Optional[str] = None

class UnifiedTorrentItem(BaseModel):
    hash: str
    name: str
    state: str
    progress: float
    dlspeed_kbps: float
    num_seeds: int
    num_leechs: int
    servarr_app: Optional[ServarrType] = None
    servarr_media_title: Optional[str] = None
    servarr_queue_id: Optional[int] = None
    defibrillarr_state: DefibrillarrState = DefibrillarrState.HEALTHY
    first_stalled_at: Optional[datetime] = None
    boosted_at: Optional[datetime] = None
    grace_period_expires_at: Optional[datetime] = None
    status_message: str = "Operating normally"

class ServiceHealth(BaseModel):
    name: str
    type: str
    url: Optional[str] = None
    connected: bool
    version: Optional[str] = None
    error: Optional[str] = None

class SystemOverview(BaseModel):
    services: Dict[str, ServiceHealth]
    total_torrents: int
    healthy_count: int
    stalled_count: int
    boosting_count: int
    cached_trackers_count: int
    dry_run: bool

class HistoryEvent(BaseModel):
    id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    torrent_hash: str
    torrent_name: str
    action: str
    servarr_app: Optional[ServarrType] = None
    details: str
    success: bool = True

class ManualActionRequest(BaseModel):
    reason: Optional[str] = "Manual action triggered by user"
