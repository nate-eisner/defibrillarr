import logging
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

from app.config import Settings
from app.models import (
    TorrentInfo,
    ServarrType,
    ServarrQueueItem,
    UnifiedTorrentItem,
    DefibrillarrState,
    ServiceHealth,
    SystemOverview,
    HistoryEvent,
)
from app.clients.qbittorrent import QBittorrentClient
from app.clients.servarr import ServarrClient
from app.services.tracker_service import TrackerService

logger = logging.getLogger("defibrillarr.engine")

class StalledRecord:
    def __init__(self, torrent_hash: str):
        self.torrent_hash = torrent_hash
        self.first_stalled_at: datetime = datetime.now(timezone.utc)
        self.boosted_at: Optional[datetime] = None
        self.grace_period_expires_at: Optional[datetime] = None
        self.state: DefibrillarrState = DefibrillarrState.STALLED
        self.servarr_app: Optional[ServarrType] = None
        self.servarr_queue_id: Optional[int] = None
        self.raw_servarr_record: Optional[Dict[str, Any]] = None
        self.status_message: str = "Stalled download detected"

class DefibrillarrEngine:
    def __init__(self, config: Settings, tracker_service: TrackerService):
        self.config = config
        self.trackers = tracker_service
        self.qbit = QBittorrentClient(
            base_url=config.QBIT_URL,
            username=config.QBIT_USERNAME,
            password=config.QBIT_PASSWORD,
            dry_run=config.DRY_RUN
        )

        self.servarr_clients: Dict[ServarrType, ServarrClient] = {}
        if config.SONARR_URL and config.SONARR_API_KEY:
            self.servarr_clients[ServarrType.SONARR] = ServarrClient(
                app_type=ServarrType.SONARR,
                base_url=config.SONARR_URL,
                api_key=config.SONARR_API_KEY,
                dry_run=config.DRY_RUN
            )
        if config.RADARR_URL and config.RADARR_API_KEY:
            self.servarr_clients[ServarrType.RADARR] = ServarrClient(
                app_type=ServarrType.RADARR,
                base_url=config.RADARR_URL,
                api_key=config.RADARR_API_KEY,
                dry_run=config.DRY_RUN
            )
        if config.LIDARR_URL and config.LIDARR_API_KEY:
            self.servarr_clients[ServarrType.LIDARR] = ServarrClient(
                app_type=ServarrType.LIDARR,
                base_url=config.LIDARR_URL,
                api_key=config.LIDARR_API_KEY,
                dry_run=config.DRY_RUN
            )

        self.stalled_records: Dict[str, StalledRecord] = {}
        self.last_boosted_timestamps: Dict[str, datetime] = {}
        self.history: List[HistoryEvent] = []
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None

    def add_history(self, torrent_hash: str, torrent_name: str, action: str, details: str, servarr_app: Optional[ServarrType] = None, success: bool = True):
        event = HistoryEvent(
            id=str(uuid.uuid4())[:8],
            timestamp=datetime.now(timezone.utc),
            torrent_hash=torrent_hash,
            torrent_name=torrent_name,
            action=action,
            servarr_app=servarr_app,
            details=details,
            success=success
        )
        self.history.insert(0, event)
        if len(self.history) > 500:
            self.history = self.history[:500]

    async def start(self):
        """Start background monitoring engine."""
        self._running = True
        logger.info("Starting Defibrillarr background engine...")
        # Preload trackers
        asyncio.create_task(self.trackers.refresh_trackers())
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self):
        """Stop background engine."""
        self._running = False
        if self._task:
            self._task.cancel()
        await self.qbit.close()
        for client in self.servarr_clients.values():
            await client.close()
        logger.info("Defibrillarr engine stopped.")

    async def _monitor_loop(self):
        while self._running:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during inspection cycle: {e}", exc_info=True)

            await asyncio.sleep(self.config.POLL_INTERVAL_SECONDS)

    async def get_services_health(self) -> Dict[str, ServiceHealth]:
        """Check connection and versions across all configured clients."""
        services: Dict[str, ServiceHealth] = {}

        # qBittorrent
        qbit_ver = await self.qbit.get_version()
        services["qbittorrent"] = ServiceHealth(
            name="qBittorrent",
            type="client",
            url=self.config.QBIT_URL,
            connected=bool(qbit_ver),
            version=qbit_ver,
            error=None if qbit_ver else "Unable to connect or authenticate"
        )

        # Servarr clients
        for app_type in [ServarrType.SONARR, ServarrType.RADARR, ServarrType.LIDARR]:
            client = self.servarr_clients.get(app_type)
            if client:
                status_data = await client.get_system_status()
                connected = bool(status_data)
                version = status_data.get("version") if status_data else None
                services[app_type.value] = ServiceHealth(
                    name=app_type.value.capitalize(),
                    type="servarr",
                    url=client.base_url,
                    connected=connected,
                    version=version,
                    error=None if connected else "Failed to connect with provided API key"
                )
            else:
                services[app_type.value] = ServiceHealth(
                    name=app_type.value.capitalize(),
                    type="servarr",
                    url=None,
                    connected=False,
                    version=None,
                    error="Not configured"
                )

        return services

    async def get_unified_queue(self) -> List[UnifiedTorrentItem]:
        """Merge qBittorrent downloads with correlated Servarr queue data and rescue states."""
        torrents = await self.qbit.get_torrents(filter_type="all")

        # Fetch Servarr queues
        servarr_queue_by_hash: Dict[str, Tuple[ServarrQueueItem, Dict[str, Any]]] = {}
        for app_type, client in self.servarr_clients.items():
            items = await client.get_queue()
            for q_item, raw in items:
                if q_item.download_id:
                    servarr_queue_by_hash[q_item.download_id.lower()] = (q_item, raw)

        unified_list: List[UnifiedTorrentItem] = []
        now = datetime.now(timezone.utc)

        for t in torrents:
            t_hash = t.hash.lower()
            dlspeed_kbps = t.dlspeed / 1024.0
            servarr_match = servarr_queue_by_hash.get(t_hash)

            servarr_app = servarr_match[0].app if servarr_match else None
            servarr_media_title = (
                servarr_match[0].series_title or
                servarr_match[0].movie_title or
                servarr_match[0].album_title or
                servarr_match[0].title
            ) if servarr_match else None
            servarr_queue_id = servarr_match[0].id if servarr_match else None

            rec = self.stalled_records.get(t_hash)
            state = DefibrillarrState.HEALTHY
            status_msg = "Operating normally"
            first_stalled = None
            boosted_at = None
            grace_expires = None

            if rec:
                state = rec.state
                first_stalled = rec.first_stalled_at
                boosted_at = rec.boosted_at
                grace_expires = rec.grace_period_expires_at
                status_msg = rec.status_message

            unified_list.append(
                UnifiedTorrentItem(
                    hash=t_hash,
                    name=t.name,
                    state=t.state,
                    progress=round(t.progress, 4),
                    dlspeed_kbps=round(dlspeed_kbps, 1),
                    num_seeds=t.num_seeds,
                    num_leechs=t.num_leechs,
                    servarr_app=servarr_app,
                    servarr_media_title=servarr_media_title,
                    servarr_queue_id=servarr_queue_id,
                    defibrillarr_state=state,
                    first_stalled_at=first_stalled,
                    boosted_at=boosted_at,
                    grace_period_expires_at=grace_expires,
                    status_message=status_msg
                )
            )

        return unified_list

    async def run_cycle(self):
        """Single inspection and action cycle."""
        torrents = await self.qbit.get_torrents(filter_type="all")
        if not torrents:
            return

        # Fetch active queues from all configured Servarr apps
        servarr_queue_by_hash: Dict[str, Tuple[ServarrQueueItem, Dict[str, Any]]] = {}
        for app_type, client in self.servarr_clients.items():
            items = await client.get_queue()
            for q_item, raw in items:
                if q_item.download_id:
                    servarr_queue_by_hash[q_item.download_id.lower()] = (q_item, raw)

        now = datetime.now(timezone.utc)
        active_hashes = set()

        for t in torrents:
            t_hash = t.hash.lower()
            active_hashes.add(t_hash)
            dlspeed_kbps = t.dlspeed / 1024.0

            # Match with Servarr
            servarr_match = servarr_queue_by_hash.get(t_hash)
            servarr_app = servarr_match[0].app if servarr_match else None
            servarr_queue_id = servarr_match[0].id if servarr_match else None
            raw_record = servarr_match[1] if servarr_match else None

            # Check if download is complete (seeding / completed)
            if t.progress >= 1.0 or t.state in ("uploading", "stalledUP", "pausedUP"):
                if t_hash in self.stalled_records:
                    logger.info(f"Torrent '{t.name}' completed! Removing from stalled records.")
                    del self.stalled_records[t_hash]
                continue

            # Check Periodic Cadence Auto-Boost
            if self.config.AUTO_BOOST_CADENCE_MINUTES > 0 and t.progress < 1.0 and t.state not in ("uploading", "stalledUP", "pausedUP"):
                last_boost = self.last_boosted_timestamps.get(t_hash)
                if last_boost is None:
                    self.last_boosted_timestamps[t_hash] = now
                elif (now - last_boost) >= timedelta(minutes=self.config.AUTO_BOOST_CADENCE_MINUTES):
                    trackers = self.trackers.get_trackers()
                    logger.info(f"[Cadence Auto-Boost] Injecting {len(trackers)} fresh trackers into '{t.name}' (cadence: {self.config.AUTO_BOOST_CADENCE_MINUTES}m)")
                    await self.qbit.add_trackers(t.hash, trackers)
                    await self.qbit.reannounce(t.hash)
                    self.last_boosted_timestamps[t_hash] = now
                    self.add_history(
                        t.hash, t.name, "cadence_boost",
                        f"Periodic auto-boost: Injected {len(trackers)} verified trackers & re-announced (cadence: {self.config.AUTO_BOOST_CADENCE_MINUTES}m).",
                        servarr_app=servarr_app
                    )

            # Check if downloading healthily
            is_healthy = (
                t.state in ("downloading", "forcedDL") and
                dlspeed_kbps >= self.config.MIN_DOWNLOAD_SPEED_KBPS and
                t.num_seeds > 0
            )

            if is_healthy:
                if t_hash in self.stalled_records:
                    rec = self.stalled_records[t_hash]
                    if rec.state == DefibrillarrState.BOOSTING:
                        self.add_history(
                            t_hash, t.name, "swarm_revived",
                            f"Torrent resumed downloading at {dlspeed_kbps:.1f} KB/s with {t.num_seeds} seeds after tracker injection!",
                            servarr_app=servarr_app
                        )
                        logger.info(f"Torrent '{t.name}' successfully REVIVED by Defibrillarr!")
                    del self.stalled_records[t_hash]
                continue

            # If here, download is stalled or slow
            # States: stalledDL, metaDL, allocating, queuedDL, or speed < min threshold with 0 seeds
            is_stalled = (
                t.state in ("stalledDL", "metaDL", "allocating") or
                (dlspeed_kbps < self.config.MIN_DOWNLOAD_SPEED_KBPS and t.num_seeds == 0)
            )

            if not is_stalled:
                continue

            # Torrent is stalled! Track or update state
            if t_hash not in self.stalled_records:
                rec = StalledRecord(t_hash)
                rec.servarr_app = servarr_app
                rec.servarr_queue_id = servarr_queue_id
                rec.raw_servarr_record = raw_record
                rec.status_message = f"Detected stalled (0 seeds, {dlspeed_kbps:.1f} KB/s). Evaluating..."
                self.stalled_records[t_hash] = rec
                logger.info(f"Tracking stalled torrent '{t.name}' ({t_hash})")
            else:
                rec = self.stalled_records[t_hash]
                if servarr_app:
                    rec.servarr_app = servarr_app
                    rec.servarr_queue_id = servarr_queue_id
                    rec.raw_servarr_record = raw_record

            # Check Stage 1: Swarm Booster (Tracker Injection)
            stalled_duration = now - rec.first_stalled_at
            if rec.state == DefibrillarrState.STALLED:
                if stalled_duration >= timedelta(minutes=self.config.STALL_THRESHOLD_MINUTES):
                    await self._execute_stage_1_boost(t, rec)

            # Check Stage 2: Servarr Failover
            elif rec.state == DefibrillarrState.BOOSTING:
                if rec.grace_period_expires_at and now >= rec.grace_period_expires_at:
                    rec.state = DefibrillarrState.PROBATION_EXPIRED
                    rec.status_message = "Rescue grace period expired. Seeds not found."
                    logger.warning(f"Torrent '{t.name}' grace period expired without seeds.")

                    if self.config.AUTO_FAILOVER_ENABLED:
                        await self._execute_stage_2_failover(t, rec)

        # Cleanup records for torrents that no longer exist in qBittorrent
        for stale_hash in list(self.stalled_records.keys()):
            if stale_hash not in active_hashes:
                del self.stalled_records[stale_hash]
        for stale_hash in list(self.last_boosted_timestamps.keys()):
            if stale_hash not in active_hashes:
                del self.last_boosted_timestamps[stale_hash]

    async def _execute_stage_1_boost(self, t: TorrentInfo, rec: StalledRecord):
        """Inject trackers, force re-announce, and tag torrent."""
        trackers = self.trackers.get_trackers()
        logger.info(f"[Stage 1] Injecting {len(trackers)} verified trackers into '{t.name}'...")

        added = await self.qbit.add_trackers(t.hash, trackers)
        await self.qbit.reannounce(t.hash)
        await self.qbit.add_tags(t.hash, [self.config.QBIT_TAG_BOOSTED])

        rec.boosted_at = datetime.now(timezone.utc)
        self.last_boosted_timestamps[t.hash.lower()] = rec.boosted_at
        rec.grace_period_expires_at = rec.boosted_at + timedelta(minutes=self.config.RESCUE_GRACE_PERIOD_MINUTES)
        rec.state = DefibrillarrState.BOOSTING
        rec.status_message = f"Trackers injected. In probation grace period until {rec.grace_period_expires_at.strftime('%H:%M:%S UTC')}."

        self.add_history(
            t.hash, t.name, "trackers_injected",
            f"Injected {len(trackers)} verified trackers and forced re-announce to discover seeds. Grace period: {self.config.RESCUE_GRACE_PERIOD_MINUTES}m.",
            servarr_app=rec.servarr_app
        )

    async def _execute_stage_2_failover(self, t: TorrentInfo, rec: StalledRecord) -> bool:
        """Remove torrent, blocklist in Servarr, and trigger replacement search."""
        logger.info(f"[Stage 2] Executing failover for dead torrent '{t.name}'...")

        if rec.servarr_app and rec.servarr_queue_id and rec.servarr_app in self.servarr_clients:
            client = self.servarr_clients[rec.servarr_app]

            # 1. Trigger search command first (before record is deleted)
            search_ok, cmd_name = False, None
            if rec.raw_servarr_record:
                search_ok, cmd_name = await client.trigger_search_for_record(rec.raw_servarr_record)

            # 2. Delete and blocklist from Servarr
            delete_ok = await client.remove_and_blocklist(rec.servarr_queue_id)

            rec.state = DefibrillarrState.FAILED_OVER
            rec.status_message = f"Failed over: Release blocklisted, {cmd_name or 'Search'} dispatched in {rec.servarr_app.value}."

            self.add_history(
                t.hash, t.name, "failover_executed",
                f"Release blocklisted in {rec.servarr_app.value} and triggered {cmd_name or 'automatic replacement search'}. Dead torrent removed.",
                servarr_app=rec.servarr_app,
                success=(delete_ok and search_ok)
            )
            return True
        else:
            # Not matched in Servarr queue or Servarr not configured
            # Delete directly from qBittorrent if enabled
            del_ok = await self.qbit.delete_torrent(t.hash, delete_files=True)
            rec.state = DefibrillarrState.FAILED_OVER
            rec.status_message = "Removed dead torrent from qBittorrent (not linked to Servarr queue)."
            self.add_history(
                t.hash, t.name, "torrent_deleted",
                "Removed dead torrent from qBittorrent. Not linked to any active Servarr queue item.",
                success=del_ok
            )
            return del_ok

    # Manual action triggers
    async def manual_boost(self, torrent_hash: str) -> bool:
        """Manually trigger immediate tracker injection & re-announce."""
        torrents = await self.qbit.get_torrents(filter_type="all")
        matched = next((t for t in torrents if t.hash.lower() == torrent_hash.lower()), None)
        if not matched:
            return False

        rec = self.stalled_records.get(torrent_hash.lower())
        if not rec:
            rec = StalledRecord(torrent_hash.lower())
            self.stalled_records[torrent_hash.lower()] = rec

        await self._execute_stage_1_boost(matched, rec)
        return True

    async def manual_failover(self, torrent_hash: str) -> bool:
        """Manually trigger immediate blocklist, delete, and replacement search."""
        torrents = await self.qbit.get_torrents(filter_type="all")
        matched = next((t for t in torrents if t.hash.lower() == torrent_hash.lower()), None)
        if not matched:
            return False

        # Find Servarr queue item if not already linked
        rec = self.stalled_records.get(torrent_hash.lower())
        if not rec or not rec.servarr_queue_id:
            for app_type, client in self.servarr_clients.items():
                items = await client.get_queue()
                for q_item, raw in items:
                    if q_item.download_id.lower() == torrent_hash.lower():
                        if not rec:
                            rec = StalledRecord(torrent_hash.lower())
                            self.stalled_records[torrent_hash.lower()] = rec
                        rec.servarr_app = app_type
                        rec.servarr_queue_id = q_item.id
                        rec.raw_servarr_record = raw
                        break

        if not rec:
            rec = StalledRecord(torrent_hash.lower())
            self.stalled_records[torrent_hash.lower()] = rec

        return await self._execute_stage_2_failover(matched, rec)

    async def get_overview(self) -> SystemOverview:
        """Compile complete system overview statistics."""
        health = await self.get_services_health()
        queue = await self.get_unified_queue()

        healthy = sum(1 for q in queue if q.defibrillarr_state == DefibrillarrState.HEALTHY)
        stalled = sum(1 for q in queue if q.defibrillarr_state in (DefibrillarrState.STALLED, DefibrillarrState.PROBATION_EXPIRED))
        boosting = sum(1 for q in queue if q.defibrillarr_state == DefibrillarrState.BOOSTING)

        return SystemOverview(
            services=health,
            total_torrents=len(queue),
            healthy_count=healthy,
            stalled_count=stalled,
            boosting_count=boosting,
            cached_trackers_count=len(self.trackers.get_trackers()),
            dry_run=self.config.DRY_RUN
        )
