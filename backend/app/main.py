import logging
import os
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models import (
    SystemOverview,
    UnifiedTorrentItem,
    HistoryEvent,
    ManualActionRequest,
    CadenceUpdateRequest,
)
from app.services.tracker_service import TrackerService
from app.services.engine import DefibrillarrEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("defibrillarr")

tracker_service = TrackerService(urls=settings.TRACKER_LIST_URLS)
engine = DefibrillarrEngine(config=settings, tracker_service=tracker_service)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Defibrillarr starting up...")
    await engine.start()
    yield
    logger.info("Defibrillarr shutting down...")
    await engine.stop()

app = FastAPI(
    title="Defibrillarr API",
    description="Stalled Torrent Revival & Servarr Auto-Failover Service",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints

@app.get("/api/overview", response_model=SystemOverview)
async def get_overview():
    """Returns services health, counts of healthy/stalled/boosting torrents, and tracker info."""
    return await engine.get_overview()

@app.get("/api/queue", response_model=List[UnifiedTorrentItem])
async def get_queue():
    """Returns unified torrent list matched against Sonarr, Radarr, and Lidarr queues."""
    return await engine.get_unified_queue()

@app.post("/api/torrents/{torrent_hash}/boost")
async def boost_torrent(torrent_hash: str):
    """Manually triggers Stage 1: Injects fresh trackers and forces re-announce."""
    success = await engine.manual_boost(torrent_hash)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Torrent with hash {torrent_hash} not found in torrent client"
        )
    return {"status": "success", "message": f"Trackers injected and re-announce sent to {torrent_hash}"}

@app.post("/api/torrents/{torrent_hash}/failover")
async def failover_torrent(torrent_hash: str):
    """Manually triggers Stage 2: Removes dead torrent, blocklists release, and requests replacement search."""
    success = await engine.manual_failover(torrent_hash)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Torrent with hash {torrent_hash} not found in torrent client"
        )
    return {"status": "success", "message": f"Failover executed for {torrent_hash}"}

@app.post("/api/torrents/{torrent_hash}/reannounce")
async def reannounce_torrent(torrent_hash: str):
    """Triggers torrent client re-announce for a torrent."""
    success = await engine.client.reannounce(torrent_hash)
    return {"status": "success" if success else "failed"}

@app.post("/api/torrents/{torrent_hash}/recheck")
async def recheck_torrent(torrent_hash: str):
    """Manually triggers force recheck and resume in torrent client to repair errored download."""
    success = await engine.manual_recheck(torrent_hash)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Torrent with hash {torrent_hash} not found or recheck failed"
        )
    return {"status": "success", "message": f"Force recheck and resume sent to {torrent_hash}"}


@app.get("/api/history", response_model=List[HistoryEvent])
async def get_history():
    """Returns recent rescue and failover action events."""
    return engine.history

@app.get("/api/trackers")
async def get_trackers():
    """Returns all cached verified public trackers."""
    trackers = tracker_service.get_trackers()
    return {
        "status": "success",
        "total": len(trackers),
        "trackers": trackers,
        "sources": settings.TRACKER_LIST_URLS
    }

@app.post("/api/trackers/refresh")
async def refresh_trackers():
    """Forces refreshing live public tracker lists."""
    trackers = await tracker_service.refresh_trackers()
    return {"status": "success", "total_trackers": len(trackers)}

@app.post("/api/cycle")
async def run_immediate_cycle():
    """Forces an immediate inspection and rescue cycle."""
    await engine.run_cycle()
    return {"status": "success", "message": "Inspection cycle executed"}

@app.get("/api/config")
async def get_config():
    """Returns current configuration (sanitized)."""
    return {
        "APP_NAME": settings.APP_NAME,
        "DRY_RUN": settings.DRY_RUN,
        "POLL_INTERVAL_SECONDS": settings.POLL_INTERVAL_SECONDS,
        "STALL_THRESHOLD_MINUTES": settings.STALL_THRESHOLD_MINUTES,
        "RESCUE_GRACE_PERIOD_MINUTES": settings.RESCUE_GRACE_PERIOD_MINUTES,
        "MIN_DOWNLOAD_SPEED_KBPS": settings.MIN_DOWNLOAD_SPEED_KBPS,
        "AUTO_FAILOVER_ENABLED": settings.AUTO_FAILOVER_ENABLED,
        "AUTO_BOOST_CADENCE_MINUTES": settings.AUTO_BOOST_CADENCE_MINUTES,
        "QBIT_URL": settings.QBIT_URL,
        "QBIT_USERNAME": settings.QBIT_USERNAME,
        "SONARR_CONFIGURED": bool(settings.SONARR_URL and settings.SONARR_API_KEY),
        "RADARR_CONFIGURED": bool(settings.RADARR_URL and settings.RADARR_API_KEY),
        "LIDARR_CONFIGURED": bool(settings.LIDARR_URL and settings.LIDARR_API_KEY),
        "TRACKER_LISTS_COUNT": len(settings.TRACKER_LIST_URLS)
    }

@app.post("/api/config/cadence")
async def update_cadence(req: CadenceUpdateRequest):
    """Updates the automatic tracker boost cadence (in minutes). 0 = disabled."""
    cadence = max(0, req.cadence_minutes)
    settings.AUTO_BOOST_CADENCE_MINUTES = cadence
    engine.config.AUTO_BOOST_CADENCE_MINUTES = cadence
    logger.info(f"Auto-boost cadence updated to {cadence} minutes")
    return {
        "status": "success",
        "cadence_minutes": cadence,
        "message": f"Auto-boost cadence set to {cadence} minutes" if cadence > 0 else "Auto-boost cadence disabled"
    }

# Serve static web dashboard
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def serve_index():
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                return f.read()
        return "<h1>Defibrillarr is running. Static UI not built.</h1>"
