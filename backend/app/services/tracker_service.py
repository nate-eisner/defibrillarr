import logging
import asyncio
from typing import List, Set
import httpx

logger = logging.getLogger("defibrillarr.tracker_service")

# Fallback top tier public trackers in case remote lists cannot be reached
DEFAULT_FALLBACK_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "udp://opentracker.i2p.rocks:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "https://tracker.tamersunion.org:443/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://retracker.lanta-net.ru:2710/announce",
    "udp://tracker.zerobytes.xyz:1337/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.theoks.net:6969/announce"
]

class TrackerService:
    def __init__(self, urls: List[str]):
        self.urls = urls
        self.cached_trackers: List[str] = list(DEFAULT_FALLBACK_TRACKERS)
        self.last_updated: float = 0

    async def refresh_trackers(self) -> List[str]:
        """Fetch fresh trackers from configured endpoints and update cache."""
        discovered: Set[str] = set()

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for url in self.urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        lines = resp.text.splitlines()
                        for line in lines:
                            cleaned = line.strip()
                            if cleaned and (
                                cleaned.startswith("udp://") or
                                cleaned.startswith("http://") or
                                cleaned.startswith("https://") or
                                cleaned.startswith("wss://")
                            ):
                                discovered.add(cleaned)
                    else:
                        logger.warning(f"Failed to fetch tracker list from {url}: status {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Error fetching tracker list from {url}: {e}")

        if discovered:
            self.cached_trackers = sorted(list(discovered))
            logger.info(f"Refreshed tracker cache: {len(self.cached_trackers)} verified trackers ready")
        else:
            logger.info(f"Using {len(self.cached_trackers)} default fallback trackers")

        return self.cached_trackers

    def get_trackers(self) -> List[str]:
        """Return currently cached tracker list."""
        return self.cached_trackers
