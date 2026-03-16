# XCSoar OGN Server

An OGN (Open Glider Network) server for XCSoar that connects to the glidernet.org APRS network to receive and process glider beacon data.

## Features

- Connects to the Open Glider Network (OGN) APRS server
- Filters beacons by geographic bounds via REST API
- Writes IGC flight recording files
- Telegram bot for managing glider names
- Web API for XCSoar to retrieve live beacon data

## Requirements

- Python 3.10+
- Access token (configured in `serverdata.txt`)
- Telegram bot token (configured in `private.key`)
- Admin chat ID (configured in `adminChat.id`)

## Installation

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

Or with dev dependencies:

```bash
pip install -r requirements.txt -r pyproject.toml[dev]
```

## Configuration

Create the following configuration files in the project directory:

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

## Usage

Start the server:

```bash
python main.py
```

The server will:
1. Connect to the OGN APRS network
2. Start the Flask web API on port 8000
3. Start the Telegram bot

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

- `/a <fid>,<name>` - Add a new glider name
- `/d <fid>` - Delete a glider name

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
└── pyproject.toml         # Package configuration
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

## Docker

### Build

```bash
docker build -t xcsoar-ogn-server .
```

### Run

```bash
docker run -v $(pwd)/names.csv:/app/names.csv \
         -v $(pwd)/serverdata.txt:/app/serverdata.txt \
         -v $(pwd)/private.key:/app/private.key \
         -v $(pwd)/adminChat.id:/app/adminChat.id \
         -p 8000:8000 \
         xcsoar-ogn-server
```

Or use Docker Compose - create `docker-compose.yml`:

```yaml
services:
  ogn-server:
    build: .
    volumes:
      - ./names.csv:/app/names.csv
      - ./serverdata.txt:/app/serverdata.txt
      - ./private.key:/app/private.key
      - ./adminChat.id:/app/adminChat.id
      - ./location.txt:/app/location.txt
    ports:
      - "8000:8000"
```

Then:

```bash
docker compose up --build
```

## License

MIT
