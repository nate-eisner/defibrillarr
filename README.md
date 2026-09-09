<p align="center">
  <img src="assets/logo.png" alt="Defibrillarr Logo" width="180" style="border-radius: 28px;">
</p>

<h1 align="center">⚡ Defibrillarr</h1>

<p align="center">
  <strong>Stalled Torrent Revival & Servarr Auto-Failover Service for Unraid</strong>
</p>

<p align="center">
  Defibrillarr is a lightweight companion daemon for <strong>qBittorrent</strong>, <strong>Transmission</strong>, <strong>Sonarr</strong>, <strong>Radarr</strong>, and <strong>Lidarr</strong>. It eliminates the common headache of downloads getting stuck at 0% or stalled due to dead trackers or inactive swarms.
</p>

---

## 🎯 How It Works

Defibrillarr runs a continuous **two-stage rescue pipeline**:

```
                  [ Torrent Stalled in qBittorrent / Transmission ]
                                         │
                                         ▼
                          ┌─────────────────────────────┐
                          │ Stage 1: Swarm Booster      │
                          └──────────────┬──────────────┘
                                        │
                         • Injects live, verified public trackers
                         • Forces re-announce to DHT & trackers
                         • Gives swarm a probation grace period (default 60m)
                                        │
                                        ▼
                          { Did seeds or speed recover? }
                           ├── YES: Resumes download normally!
                           └── NO:  (Grace period expires)
                                        │
                                        ▼
                         ┌─────────────────────────────┐
                         │ Stage 2: Servarr Failover   │
                         └──────────────┬──────────────┘
                                        │
                         • Removes dead download from qBittorrent
                         • Blocklists the bad release in Sonarr/Radarr/Lidarr
                         • Automatically triggers search for a fresh healthy release
```

---

## 🚀 Why Defibrillarr Over qbit_manage / Cleanarr?

- **Zero Complex YAML**: Configured via a modern web interface or simple environment variables.
- **Queue Correlated**: Matches qBittorrent torrent hashes directly against Sonarr, Radarr, and Lidarr queues.
- **True Automatic Replacement**: Rather than just pausing or deleting a torrent, Defibrillarr tells Sonarr/Radarr/Lidarr to **blocklist the dead release** (so it is never grabbed again) and immediately dispatches an automatic search command (`EpisodeSearch`, `MoviesSearch`, `AlbumSearch`) to grab a working release.
- **Real-Time Web Dashboard**: View swarm health, seed counts, tracker status, and trigger 1-click manual overrides.

---

## 🛠️ Installation on Unraid

### Option 1: Unraid XML Template (Recommended)
1. Copy [`unraid-template.xml`](unraid-template.xml) to your Unraid flash drive:
   ```bash
   /boot/config/plugins/dockerMan/templates-user/my-defibrillarr.xml
   ```
2. In the Unraid Web GUI, navigate to **Docker** &rarr; **Add Container**.
3. Select the **Template** dropdown and choose **Defibrillarr**.
4. Fill in your `QBIT_URL`, `SONARR_URL`, `SONARR_API_KEY` (and optionally Radarr/Lidarr).
5. Click **Apply**!
6. Open Web UI at `http://<your-unraid-ip>:8787`.

### Option 2: Docker Compose
Create a `docker-compose.yml` or use the Compose Manager plugin on Unraid:

```yaml
services:
  defibrillarr:
    image: defibrillarr:latest
    build: .
    container_name: defibrillarr
    restart: unless-stopped
    ports:
      - "8787:8787"
    environment:
      - PORT=8787
      - DRY_RUN=false
      - POLL_INTERVAL_SECONDS=60
      - STALL_THRESHOLD_MINUTES=15
      - RESCUE_GRACE_PERIOD_MINUTES=60
      - MIN_DOWNLOAD_SPEED_KBPS=10.0
      - AUTO_FAILOVER_ENABLED=true
      - AUTO_BOOST_CADENCE_MINUTES=120
      - QBIT_URL=http://192.168.1.100:8080
      - QBIT_USERNAME=admin
      - QBIT_PASSWORD=adminadmin
      - SONARR_URL=http://192.168.1.100:8989
      - SONARR_API_KEY=your_sonarr_api_key_here
      - RADARR_URL=http://192.168.1.100:7878
      - RADARR_API_KEY=your_radarr_api_key_here
      - LIDARR_URL=http://192.168.1.100:8686
      - LIDARR_API_KEY=your_lidarr_api_key_here
```

---

## ⚙️ Configuration Reference

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8787` | Port for the Defibrillarr dashboard |
| `DRY_RUN` | `false` | If `true`, logs actions without modifying qBittorrent or Servarr |
| `POLL_INTERVAL_SECONDS` | `60` | Frequency of monitoring sweeps |
| `STALL_THRESHOLD_MINUTES`| `15` | Minutes stalled/0-seeds before Stage 1 tracker injection |
| `RESCUE_GRACE_PERIOD_MINUTES`| `60` | Minutes to wait in Stage 1 before executing Stage 2 failover |
| `MIN_DOWNLOAD_SPEED_KBPS` | `10.0` | Downloads slower than this with 0 seeds count as stalled |
| `AUTO_FAILOVER_ENABLED` | `true` | When `false`, requires 1-click confirmation in the Web UI |
| `AUTO_BOOST_CADENCE_MINUTES` | `120` | Interval to automatically re-inject verified trackers & re-announce (0 = disabled) |
| `TORRENT_CLIENT` | `qbittorrent` | Active download client (`qbittorrent` or `transmission`) |
| `QBIT_URL` | `http://localhost:8080` | URL to qBittorrent Web UI |
| `QBIT_USERNAME` | `admin` | qBittorrent username |
| `QBIT_PASSWORD` | `adminadmin` | qBittorrent password |
| `TRANSMISSION_URL` | `http://localhost:9091/transmission/rpc` | URL to Transmission RPC endpoint |
| `TRANSMISSION_USERNAME` | *(None)* | Transmission RPC username (optional) |
| `TRANSMISSION_PASSWORD` | *(None)* | Transmission RPC password (optional) |
| `SONARR_URL` | *(None)* | URL to Sonarr (e.g., `http://192.168.1.100:8989`) |
| `SONARR_API_KEY` | *(None)* | Sonarr API Key (`Settings` &rarr; `General`) |
| `RADARR_URL` | *(None)* | URL to Radarr (e.g., `http://192.168.1.100:7878`) |
| `RADARR_API_KEY` | *(None)* | Radarr API Key (`Settings` &rarr; `General`) |
| `LIDARR_URL` | *(None)* | URL to Lidarr (e.g., `http://192.168.1.100:8686`) |
| `LIDARR_API_KEY` | *(None)* | Lidarr API Key (`Settings` &rarr; `General`) |

---

## 🧪 Testing & Verification

Run the test suite locally:
```bash
source .venv/bin/activate
PYTHONPATH=backend pytest backend/tests
```
