# XCSoar OGN Server

An OGN (Open Glider Network) server for XCSoar that connects to the glidernet.org APRS network to receive and process glider beacon data.

## Features

- Connects to the Open Glider Network (OGN) APRS server
- Filters beacons by geographic bounds via REST API
- Writes IGC flight recording files
- Telegram bot for managing glider names
- Web API for XCSoar to retrieve live beacon data

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

## License

MIT