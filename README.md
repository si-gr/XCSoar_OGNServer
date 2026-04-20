# XCSoar OGN Server

An OGN (Open Glider Network) server for XCSoar that connects to the glidernet.org APRS network to receive and process glider beacon data.

## Features

- Connects to the Open Glider Network (OGN) APRS server
- Filters beacons by geographic bounds via REST API
- Writes IGC flight recording files
- Telegram bot for managing glider names
- Web API for XCSoar to retrieve live beacon data
- **DDB Integration**: Automatic download of FLARM Device Database on startup, using aircraft registration as primary display name

## Quick Start (Docker Compose)

1. Create configuration files:
   ```bash
   cp names.csv.example names.csv
   cp serverdata.txt.example serverdata.txt
   cp private.key.example private.key
   cp location.txt.example location.txt
   ```

2. Edit `serverdata.txt`:
   ```
   <access_token>
   <host>
   <target_latitude>
   <target_longitude>
   ```

3. Add your Telegram bot token to `private.key`

4. Start the server:
   ```bash
   docker compose up --build
   ```

The API will be available at `http://localhost:8000`

## Configuration

### `serverdata.txt`

```
<access_token>
<host>
<target_latitude>
<target_longitude>
```

Example:
```
mysecrettoken
0.0.0.0
47.5
13.0
```

### `private.key`

Your Telegram bot token (single line).

### `adminChat.id`

Your Telegram chat ID (binary file).

### `names.csv`

CSV file with flarm ID to pilot name mappings:
```
fid,name
FLR123456,John Doe
FLR789012,Jane Smith
```

### DDB (FLARM Device Database)

The server automatically downloads the FLARM Device Database from [glidernet.org](https://github.com/glidernet/ogn-ddb) on startup. This provides automatic registration lookup for known gliders.

**Name resolution priority:**
1. **DDB registration** (e.g., "D-1234") - downloaded automatically
2. **names.csv nickname** (e.g., "John Doe") - user-defined via Telegram bot
3. **FLARM ID suffix** (last 4 chars) - fallback if neither available

**Cache behavior:**
- Downloaded DDB is cached in `ddb.json`
- Cache TTL: 60 minutes (re-downloaded after expiry)
- If DDB download fails, server starts with names.csv only and retries DDB in background

**Rate limiting:** The DDB API enforces rate limits. If 429 Too Many Requests is received, the server waits and retries up to 3 times before falling back to names.csv.

## API

### Get Beacons

```
GET /?access_token=<token>&bounds=<min_lat>,<max_lat>,<min_lon>,<max_lon>
```

Returns CSV-formatted beacon data:
```
<count>,<count>
<name>,<lat>,<lon>,<track>,<alt>,<speed>,<climb>,<timestamp>,<type>
```

Example:
```
GET /?access_token=mysecrettoken&bounds=47.0,48.0,12.0,14.0
```

Response:
```
2,2
John Doe,47.51234,13.01234,180,1500,100,2.5,1705312245,^
Jane,47.52345,13.02345,90,1600,120,1.5,1705312246,>
```

## Telegram Bot Commands

- `/a <fid>,<name>` - Add a new glider name (e.g., `/a FLR123456,John Doe`)
- `/d <fid>` - Delete a glider name (e.g., `/d FLR123456`)

Changes to `names.csv` are persisted to the host file via Docker volume mount.

## Project Structure

```
.
├── main.py                 # Entry point
├── src/ogn_server/         # Main package
│   ├── __init__.py
│   ├── beacon.py           # Beacon dataclass
│   ├── client.py           # OGN client
│   ├── config.py           # Configuration
│   ├── api.py              # Flask API
│   └── telegram_bot.py     # Telegram bot
├── tests/                  # Test suite
├── requirements.txt        # Python dependencies
├── pyproject.toml         # Package configuration
└── docker-compose.yml     # Docker Compose configuration
```

## Testing

Run tests:
```bash
pytest tests/ -v
```

With coverage:
```bash
pytest tests/ --cov=src --cov-report=html
```

## Docker Commands

Build and start:
```bash
docker compose up --build
```

Run in background:
```bash
docker compose up --build -d
```

View logs:
```bash
docker compose logs -f
```

Stop:
```bash
docker compose down
```

## Local Development

If you want to run without Docker:

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Start the server:
   ```bash
   python main.py
   ```

## External Restart Management

The server supports graceful shutdown via SIGTERM. For automatic daily restarts, configure an external cronjob instead of using internal scheduling.

### Option A: Docker Deployment (Recommended)

**Host-level cronjob** (runs on your server):

```bash
# Edit system crontab: sudo crontab -e
# Add this line to restart container daily at 3 AM UTC:
0 3 * * * docker restart xcsoar-ogn-server >> /var/log/ogn-restart.log 2>&1
```

**Systemd timer** (modern alternative to cron):

Create `/etc/systemd/system/ogn-server-restart.service`:
```ini
[Unit]
Description=Daily restart of XCSoar OGN Server
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/docker restart xcsoar-ogn-server
User=youruser
```

Create `/etc/systemd/system/ogn-server-restart.timer`:
```ini
[Unit]
Description=Restart XCSoar OGN Server daily at 3 AM

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable with:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ogn-server-restart.timer
```

### Option B: Non-Docker Deployment

**Systemd timer**:

Create `/etc/systemd/system/ogn-server-restart.service`:
```ini
[Unit]
Description=Daily restart of XCSoar OGN Server
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart ogn-server
User=youruser
```

Create `/etc/systemd/system/ogn-server-restart.timer`:
```ini
[Unit]
Description=Restart XCSoar OGN Server daily at 3 AM

[Timer]
OnCalendar=*-* escapes-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

**Cron equivalent**:
```bash
# Edit user crontab: crontab -e
0 3 * * * /bin/systemctl restart ogn-server >> /var/log/ogn-restart.log 2>&1
```

### Logging

- **Docker**: `docker logs xcsoar-ogn-server` or `journalctl -u xcsoar-ogn-server`
- **Systemd**: `journalctl -u ogn-server` for server logs, `journalctl -u ogn-server-restart.service` for restart logs
- **Cron**: Logs redirected to `/var/log/ogn-restart.log`

### Graceful Shutdown

The server handles SIGTERM gracefully:
- OGN client disconnects cleanly
- Telegram bot stops polling and closes connections
- Flask API shuts down properly

This ensures no data loss during restart.

## License

MIT

## TODO

- [x] Add health check endpoint (`/health`) for monitoring and container orchestration
- [x] Implement API rate limiting to prevent abuse
- [x] Add Prometheus metrics endpoint for observability
- [x] Add unit tests for telegram_bot module
- [x] Add integration tests for the full system
- [x] Improve IGC file handling with automatic file rotation and cleanup
- [x] Add structured logging (JSON format) for better log analysis
- [x] Implement input validation for configuration files on startup
- [x] Add caching layer for frequently requested beacon data
- [x] Improve error handling and recovery in OGN client with retry logic

## Changelog

### v1.1.0 (2025-03-16)
- **Features**
  - Added `/health` endpoint for container orchestration monitoring
  - Added `/metrics` endpoint for Prometheus observability
  - Added API rate limiting (60 requests/minute per client)
  - Added caching layer for beacon data (5 second TTL)
  - Added IGC file retention (30 days) with automatic cleanup
  - Added configuration validation on startup

- **Testing**
  - Added unit tests for telegram_bot module
  - Added integration tests for full system

- **Logging**
  - Added structured JSON logging for better log analysis