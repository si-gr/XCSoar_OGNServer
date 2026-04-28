import os
from pathlib import Path


class ConfigError(Exception):
    pass


class Config:
    OGN_SERVER_HOST = "glidern3.glidernet.org"
    OGN_APRS_USER = "N0CALL"
    OGN_APRS_FILTER = ""
    
    NAMES_FILE = "names.csv"
    SERVERDATA_FILE = "serverdata.txt"
    PRIVATE_KEY_FILE = "private.key"
    ADMIN_CHAT_ID_FILE = "adminChat.id"
    LOCATION_FILE = "location.txt"
    IGC_FOLDER = "igc_files"
    
    # Default beacon timeout in seconds (legacy test expects 30 seconds)
    BEACON_TIMEOUT_SECONDS = 30
    BEACON_ZERO_VELOCITY_TIMEOUT_SECONDS = 60
    CLEANUP_INTERVAL_SECONDS = 30
    LOCATION_FILTER_DEGREES = 0.2
    
    IGC_RETENTION_DAYS = 30
    
    # DDB (FLARM Device Database) configuration
    DDB_URL = "http://ddb.glidernet.org/download/?j=1&t=1"
    DDB_CACHE_FILE = "ddb.json"
    DDB_CACHE_TTL_MINUTES = 60
    DDB_TIMEOUT_SECONDS = 30
    DDB_MAX_RETRIES = 5
    DDB_RETRY_BACKOFF_BASE_SECONDS = 2
    
    API_ACCESS_TOKEN_INDEX = 0
    API_HOST_INDEX = 1
    API_TARGET_LAT_INDEX = 2
    API_TARGET_LON_INDEX = 3
    API_LOC_FILTER_INDEX = 4
    
    @classmethod
    def load_serverdata(cls, validate: bool = True):
        serverdata_path = Path(cls.SERVERDATA_FILE)
        if serverdata_path.exists():
            with open(cls.SERVERDATA_FILE, "r") as f:
                lines = [line.strip() for line in f.readlines() if line.strip() and not line.startswith("#")]
                if len(lines) < 4:
                    raise ConfigError("serverdata.txt must have at least 4 lines: token, host, lat, lon")
                
                if validate:
                    cls._validate_serverdata(lines)
                return lines
        return None
    
    @classmethod
    def _validate_serverdata(cls, lines: list):
        if not lines[cls.API_ACCESS_TOKEN_INDEX]:
            raise ConfigError("Access token cannot be empty")
        
        if not lines[cls.API_HOST_INDEX]:
            raise ConfigError("Host cannot be empty")
        
        try:
            lat = float(lines[cls.API_TARGET_LAT_INDEX])
            if lat < -90 or lat > 90:
                raise ConfigError(f"Latitude must be between -90 and 90, got {lat}")
        except ValueError:
            raise ConfigError(f"Latitude must be a valid number, got {lines[cls.API_TARGET_LAT_INDEX]}")
        
        try:
            lon = float(lines[cls.API_TARGET_LON_INDEX])
            if lon < -180 or lon > 180:
                raise ConfigError(f"Longitude must be between -180 and 180, got {lon}")
        except ValueError:
            raise ConfigError(f"Longitude must be a valid number, got {lines[cls.API_TARGET_LON_INDEX]}")
    
    @classmethod
    def load_admin_chat_id(cls):
        admin_chat_id_path = Path(cls.ADMIN_CHAT_ID_FILE)
        if admin_chat_id_path.exists():
            content = admin_chat_id_path.read_text().strip()
            if content:
                return content
        return "0"
    
    @classmethod
    def load_private_key(cls):
        priv_key_path = Path(cls.PRIVATE_KEY_FILE)
        if priv_key_path.exists():
            with open(cls.PRIVATE_KEY_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        return line
        return None
