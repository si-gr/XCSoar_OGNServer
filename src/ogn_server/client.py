import math
import os
import datetime
import logging
import sys
import json
import time
from pathlib import Path
from typing import Callable, Optional
import pandas as pd
from ogn.client import AprsClient, settings as ogn_settings
from ogn.parser import parse, AprsParseError
# Geofence utilities
from .geofence import load_geofences, is_off_field

from .beacon import Beacon
from .config import Config, get_log_level
from .ddb import get_ddb_devices, get_registration
from .formatters import JSONFormatter

# Initialize module-level JSON-formatted logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
logger.setLevel(get_log_level())

# Plausibility check thresholds for beacon validation
PLAUSIBILITY_MAX_GROUND_SPEED_MPS = 97.2      # 350 km/h in m/s (VNE + margin)
PLAUSIBILITY_MAX_CLIMB_RATE_MPS = 8.0         # m/s positive (exceptional thermal)
PLAUSIBILITY_MAX_SINK_RATE_MPS = -10.0        # m/s negative (spiral dive with airbrakes)
PLAUSIBILITY_MIN_TIME_DELTA_SEC = 0.5          # reject duplicates/sub-second noise
PLAUSIBILITY_MAX_TIME_DELTA_SEC = 3600.0      # reject 1-hour+ gaps
PLAUSIBILITY_MAX_ALTITUDE_M = 10000.0         # maximum valid altitude (10km)
EARTH_RADIUS_METERS = 6371000                  # Mean Earth radius for haversine


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points using haversine formula."""
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_lat / 2) ** 2 + 
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return EARTH_RADIUS_METERS * c


def _calculate_ground_speed(lat1: float, lon1: float, lat2: float, lon2: float, dt_seconds: float) -> float:
    """Calculate ground speed between two position fixes."""
    if dt_seconds <= 0:
        return float('inf')
    
    distance = _haversine_distance(lat1, lon1, lat2, lon2)
    return distance / dt_seconds


def _calculate_vertical_speed(alt1: float, alt2: float, dt_seconds: float) -> float:
    """Calculate vertical speed (climb/sink rate) between two altitude fixes."""
    if dt_seconds <= 0:
        return float('inf')
    
    return (alt2 - alt1) / dt_seconds


def _parse_timestamp(val) -> datetime.datetime | None:
    """Parse timestamp string to datetime object."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None
    if s.isdigit():
        try:
            return datetime.datetime.utcfromtimestamp(int(s))
        except Exception:
            return None
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _is_beacon_plausible(
    prev_beacon: dict | None,
    curr_beacon: dict,
    flarm_id: str
) -> tuple[bool, str | None]:
    """
    Validate if a beacon is physically plausible compared to previous beacon.
    
    Parameters:
        prev_beacon: Previous beacon dict or None if first
        curr_beacon: Current beacon dict with keys: latitude, longitude, altitude
        flarm_id: FLARM ID for logging
    
    Returns:
        Tuple of (is_plausible: bool, reason: str | None)
    
    Validation checks (in order):
        1. Altitude: <= 10,000m
        2. Ground speed: <= 97.2 m/s (350 km/h) - uses 1 second delta if no previous timestamp
        3. Climb rate: <= 8.0 m/s
        4. Sink rate: >= -10.0 m/s
    """
    # First beacon always accepted (no previous point to compare)
    if prev_beacon is None:
        # But still check absolute altitude limit
        alt = curr_beacon.get("altitude", 0)
        if alt > PLAUSIBILITY_MAX_ALTITUDE_M:
            return (False, f"altitude:{alt:.0f}m>{PLAUSIBILITY_MAX_ALTITUDE_M:.0f}m")
        return (True, None)
    
    # Check absolute altitude limit first
    alt = curr_beacon.get("altitude", 0)
    if alt > PLAUSIBILITY_MAX_ALTITUDE_M:
        return (False, f"altitude:{alt:.0f}m>{PLAUSIBILITY_MAX_ALTITUDE_M:.0f}m")
    
    # Parse positions and altitudes
    try:
        prev_lat = float(prev_beacon.get("latitude", 0))
        prev_lon = float(prev_beacon.get("longitude", 0))
        prev_alt = float(prev_beacon.get("altitude", 0))
        
        curr_lat = float(curr_beacon.get("latitude", 0))
        curr_lon = float(curr_beacon.get("longitude", 0))
        curr_alt = float(curr_beacon.get("altitude", 0))
    except (ValueError, TypeError):
        return (False, "invalid_position_or_altitude")
    
    # Use 1 second as default time delta (conservative for speed calculations)
    dt = 1.0
    
    # Check ground speed
    ground_speed = _calculate_ground_speed(prev_lat, prev_lon, curr_lat, curr_lon, dt)
    if ground_speed > PLAUSIBILITY_MAX_GROUND_SPEED_MPS:
        return (False, f"speed:{ground_speed:.1f}m/s")
    
    # Check vertical speed
    vertical_speed = _calculate_vertical_speed(prev_alt, curr_alt, dt)
    if vertical_speed > PLAUSIBILITY_MAX_CLIMB_RATE_MPS:
        return (False, f"climb:{vertical_speed:.1f}m/s")
    
    if vertical_speed < PLAUSIBILITY_MAX_SINK_RATE_MPS:
        return (False, f"sink:{vertical_speed:.1f}m/s")
    
    # All checks passed
    return (True, None)


class OGNCloseoutFilter(logging.Filter):
    """Filter to suppress the 'Read returns zero length string' warning from OGN library.
    
    This message is logged by ogn.client when the server closes the connection normally.
    It's not an error condition, just informational about connection closeout.
    """
    def filter(self, record):
        # Suppress the specific closeout warning message
        if "Read returns zero length string" in record.getMessage():
            return False
        return True


class OGNClient:
    def __init__(self, serverdata: list):
        self.serverdata = serverdata
        self.current_messages: list[Beacon] = []
        self.timestamp = 0
        self.igc_cleanup_timestamp = 0
        self.names_df = pd.DataFrame()
        self.names_df_time = 0
        self._load_names_df()
        
        # DDB (FLARM Device Database) initialization
        self.ddb_devices = get_ddb_devices()
        logger.info(f"DDB loaded: {len(self.ddb_devices)} devices")
        
        self._cache: dict = {}
        self._cache_ttl_seconds = 5
        self._error_count = 0
        self._last_error_time = 0
        self._climb_history: dict[str, list[tuple[datetime.datetime, float, float, float, float, float]]] = {}
        self.client = None
        self._last_rotation_check_time = 0
        # SAR: last known position caches for each FLARM address
        # Stores last known position data per aircraft
        self._last_position_cache: dict[str, dict] = {}
        # Stores last beacon timestamp per aircraft (Berlin time handling below)
        self._last_beacon_times: dict[str, datetime.datetime] = {}
        # Geofence: loaded geofences and offline/off-field aircraft cache
        try:
            self._geofences = load_geofences(Config.GEOFENCE_FILE)
        except Exception as e:
            logger.warning(f"Geofence file could not be loaded: {e}. Geofencing disabled.")
            self._geofences = []
        self._offline_aircraft: dict[str, dict] = {}
        
        # Statistics for /status command
        self._last_beacon_received: datetime.datetime | None = None
        self._total_beacons_received: int = 0
        
        # APRS-IS location-based filtering state
        self._last_aprs_filter: str | None = None
        self._last_filter_bounds: tuple[float, float, float, float] | None = None
        self._filter_needs_update: bool = False
        
        # Suppress OGN library's closeout warning (normal disconnection)
        ogn_client_logger = logging.getLogger('ogn.client.client')
        ogn_client_logger.addFilter(OGNCloseoutFilter())
        
        self._migrate_location_file()

    def _haversine_distance_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate great-circle distance between two points in kilometers."""
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = (math.sin(delta_lat / 2) ** 2 + 
             math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return (EARTH_RADIUS_METERS * c) / 1000.0
    
    def _should_update_filter(self, new_bounds: tuple[float, float, float, float]) -> bool:
        """Check if filter should be updated based on minimum change threshold.
        
        Args:
            new_bounds: (min_lat, max_lat, min_lon, max_lon)
            
        Returns:
            True if center point moved more than OGN_FILTER_MIN_CHANGE_KM
        """
        if self._last_filter_bounds is None:
            return True
        
        # Calculate center points
        old_center_lat = (self._last_filter_bounds[0] + self._last_filter_bounds[1]) / 2
        old_center_lon = (self._last_filter_bounds[2] + self._last_filter_bounds[3]) / 2
        new_center_lat = (new_bounds[0] + new_bounds[1]) / 2
        new_center_lon = (new_bounds[2] + new_bounds[3]) / 2
        
        # Check distance between centers
        distance_km = self._haversine_distance_km(
            old_center_lat, old_center_lon,
            new_center_lat, new_center_lon
        )
        
        return distance_km >= Config.OGN_FILTER_MIN_CHANGE_KM
    
    def _build_aprs_filter_string(self, bounds: tuple[float, float, float, float]) -> str:
        """Build APRS-IS filter string from bounds.
        
        Args:
            bounds: (min_lat, max_lat, min_lon, max_lon)
            
        Returns:
            Filter string in format "r/LAT/LON/RADIUS"
        """
        center_lat = (bounds[0] + bounds[1]) / 2
        center_lon = (bounds[2] + bounds[3]) / 2
        radius_km = Config.OGN_APRS_FILTER_RADIUS_KM
        
        return f"r/{center_lat:.4f}/{center_lon:.4f}/{radius_km}"
    
    def set_aprs_filter(self, bounds: tuple[float, float, float, float]) -> None:
        """Set APRS-IS location filter based on client request bounds.
        
        Filter update is deferred to next reconnection cycle to avoid connection churn.
        Auto-switches to port 14580 when filter is active.
        
        Args:
            bounds: (min_lat, max_lat, min_lon, max_lon) from API request
        """
        if not Config.OGN_APRS_FILTER_ENABLED:
            return
        
        if self._should_update_filter(bounds):
            old_center = None
            if self._last_filter_bounds:
                old_center = (
                    (self._last_filter_bounds[0] + self._last_filter_bounds[1]) / 2,
                    (self._last_filter_bounds[2] + self._last_filter_bounds[3]) / 2
                )
            new_center = ((bounds[0] + bounds[1]) / 2, (bounds[2] + bounds[3]) / 2)
            distance_km = self._haversine_distance_km(
                old_center[0] if old_center else new_center[0],
                old_center[1] if old_center else new_center[1],
                new_center[0],
                new_center[1]
            ) if old_center else 0
            
            logger.info(
                f"APRS filter update scheduled: center moved {distance_km:.1f}km "
                f"from {old_center if old_center else 'initial'} to {new_center}"
            )
            self._last_filter_bounds = bounds
            self._last_aprs_filter = self._build_aprs_filter_string(bounds)
            self._filter_needs_update = True
    
    def get_last_bounds(self) -> tuple[float, float, float, float] | None:
        """Get the last filter bounds that were applied.
        
        Returns:
            (min_lat, max_lat, min_lon, max_lon) or None if no filter set
        """
        return self._last_filter_bounds
    
    def _load_names_df(self):
        names_path = Path(Config.NAMES_FILE)
        current_file_time = 0
        if names_path.exists():
            current_file_time = os.stat(Config.NAMES_FILE).st_mtime
            if current_file_time > self.names_df_time:
                self.names_df = pd.read_csv(
                    Config.NAMES_FILE, 
                    names=["fid", "name"], 
                    header=0
                )
        self.names_df_time = current_file_time

    def _migrate_location_file(self):
        """Migrate existing location.txt to dated name based on file modification time."""
        location_path = Path(Config.LOCATION_FILE)
        if not location_path.exists():
            return
        
        # Skip empty files
        if location_path.stat().st_size == 0:
            return
        
        mtime = location_path.stat().st_mtime
        file_date = datetime.datetime.fromtimestamp(mtime)
        today = datetime.datetime.now()
        
        age_days = (today - file_date).days
        
        # Delete if older than retention period
        if age_days >= Config.LOCATION_RETENTION_DAYS:
            try:
                location_path.unlink()
                logger.info(f"Migrated/deleted old location.txt (age: {age_days} days)")
            except OSError:
                pass
            return
        
        # Rename to dated format
        date_str = file_date.strftime("%Y%m%d")
        rotated_name = f"location_{date_str}.txt"
        
        if not Path(rotated_name).exists():
            try:
                location_path.rename(rotated_name)
                logger.info(f"Migrated location.txt to {rotated_name}")
            except OSError:
                pass
    
    def _cleanup_old_igc_files(self):
        igc_dir = Path(Config.IGC_FOLDER)
        if not igc_dir.exists():
            return
        cutoff_time = time.time() - (Config.IGC_RETENTION_DAYS * 86400)
        for igc_file in igc_dir.glob("*.igc"):
            try:
                if os.stat(igc_file).st_mtime < cutoff_time:
                    igc_file.unlink()
            except OSError:
                pass

    def _cleanup_old_location_files(self):
        """Delete location files older than LOCATION_RETENTION_DAYS."""
        location_dir = Path(".")
        if not location_dir.exists():
            return
        cutoff_time = time.time() - (Config.LOCATION_RETENTION_DAYS * 86400)

        for location_file in location_dir.glob("location_*.txt"):
            # Skip current location.txt
            if location_file.name == Config.LOCATION_FILE:
                continue
            try:
                if os.stat(location_file).st_mtime < cutoff_time:
                    location_file.unlink()
                    logger.info(f"Deleted old location file: {location_file.name}")
            except OSError:
                pass
    
    def _get_entry_dates(self, entries: list[str]) -> tuple[list[str], dict[datetime.date, list[str]]]:
        today_entries = []
        historical_entries: dict[datetime.date, list[str]] = {}
        today = datetime.date.today()
        
        for entry in entries:
            entry = entry.strip()
            if not entry or entry.startswith("#"):
                continue
            
            parts = entry.split(",")
            if len(parts) < 8:
                continue
            
            try:
                ts_str = parts[7].strip()
                # Try Unix timestamp first (integer)
                if ts_str.isdigit():
                    timestamp = int(ts_str)
                else:
                    # Parse ISO format datetime string
                    dt = datetime.datetime.fromisoformat(ts_str)
                    timestamp = int(dt.timestamp())
                entry_date = datetime.date.fromtimestamp(timestamp)
                
                if entry_date == today:
                    today_entries.append(entry)
                else:
                    if entry_date not in historical_entries:
                        historical_entries[entry_date] = []
                    historical_entries[entry_date].append(entry)
            except (ValueError, IndexError):
                continue
        
        return today_entries, historical_entries
    
    def _rotate_location_file_if_needed(self):
        if time.time() <= self._last_rotation_check_time + Config.LOCATION_ROTATION_CHECK_INTERVAL_SECONDS:
            return
        
        self._last_rotation_check_time = time.time()
        
        location_path = Path(Config.LOCATION_FILE)
        if not location_path.exists() or location_path.stat().st_size == 0:
            return
        
        try:
            content = location_path.read_text()
            entries = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
            
            if not entries:
                return
            
            timestamps = []
            for entry in entries:
                parts = entry.split(",")
                if len(parts) >= 8:
                    try:
                        ts_str = parts[7].strip()
                        # Try Unix timestamp first (integer)
                        if ts_str.isdigit():
                            timestamps.append(int(ts_str))
                        else:
                            # Parse ISO format datetime string
                            dt = datetime.datetime.fromisoformat(ts_str)
                            timestamps.append(int(dt.timestamp()))
                    except (ValueError, IndexError):
                        pass
            
            if not timestamps:
                return
            
            oldest_ts = min(timestamps)
            oldest_date = datetime.date.fromtimestamp(oldest_ts)
            today = datetime.date.today()
            
            logger.info(f"Location file oldest entry check: {oldest_date} (vs today: {today})")
            
            if oldest_date >= today:
                logger.debug("Location file rotation skipped: all entries from today")
                return
            
            logger.info(f"Location file rotation triggered: oldest entry from {oldest_date}")
            
            today_entries, historical_entries = self._get_entry_dates(entries)
            
            for entry_date, date_entries in historical_entries.items():
                dated_name = f"location_{entry_date.strftime('%Y%m%d')}.txt"
                dated_path = Path(dated_name)
                
                mode = "a" if dated_path.exists() else "w"
                with open(dated_path, mode) as f:
                    if not dated_path.exists() or dated_path.stat().st_size == 0:
                        f.write("# Location Log File (auto-generated)\n")
                        f.write("# Format: address,latitude,longitude,track,altitude,ground_speed,climb_rate,timestamp,symbolcode\n\n")
                    for entry in date_entries:
                        f.write(entry + "\n")
            
            location_path.write_text("")
            for entry in today_entries:
                with open(location_path, "a") as f:
                    f.write(entry + "\n")
            
            logger.info(f"Rotated location.txt: archived {sum(len(v) for v in historical_entries.values())} historical entries across {len(historical_entries)} day(s), kept {len(today_entries)} today's entries")
        
        except Exception as e:
            logger.warning(f"Failed to rotate location file: {e}")
    
    def _get_nickname(self, flarm_id: str) -> Optional[str]:
        """
        Resolve display name for a beacon using FLARM ID.
        
        Priority:
        1. DDB registration (from FLARM Device Database)
        2. names.csv nickname (user-defined via Telegram bot)
        3. Raw input (truncated to last 4 chars if full ID)
        
        Args:
            flarm_id: Full FLARM ID (e.g., "FLR3ECA1B") or partial ID
        
        Returns:
            Display name string or None
        """
        lookup_key = flarm_id[-4:].upper() if len(flarm_id) > 4 else flarm_id.upper()
        
        ddb_registration = get_registration(flarm_id, self.ddb_devices)
        if ddb_registration:
            matches = self.names_df[self.names_df["fid"] == ddb_registration]
            if len(matches) > 0:
                nickname = matches["name"].iloc[0]
                if nickname != '....':
                    return nickname
            return ddb_registration
        
        matches = self.names_df[self.names_df["fid"] == lookup_key]
        if len(matches) > 0:
            nickname = matches["name"].iloc[0]
            if nickname != '....':
                return nickname
        
        return lookup_key
    
    def _is_valid_symbol(self, symbolcode: str) -> bool:
        invalid_symbols = ['n', 'X', '^', 'g']
        return symbolcode not in invalid_symbols
    
    def _write_igc_file(self, beacon: dict):
        address = beacon["address"][2:]
        if address in self.names_df["fid"].values or get_registration(beacon["address"], self.ddb_devices) in self.names_df["fid"].values:
            nickname = None
            nickname_address = self.names_df[self.names_df["fid"].str.contains(address)]
            if len(nickname_address) > 0:
                nickname = nickname_address.iloc[0]["name"]
            else:
                nickname = self.names_df[self.names_df["fid"].str.contains(get_registration(beacon["address"], self.ddb_devices))].iloc[0]["name"]

            dt = datetime.datetime.now()
            lat_d = beacon["latitude"]
            lat_m = (lat_d - int(lat_d)) * 60
            lat_s = (lat_m - int(lat_m)) * 60
            long_d = beacon["longitude"]
            long_m = (long_d - int(long_d)) * 60
            long_s = (long_m - int(long_m)) * 60

            igc_line = (
                f'B{beacon["reference_timestamp"].hour:02d}'
                f'{beacon["reference_timestamp"].minute:02d}'
                f'{beacon["reference_timestamp"].second:02d}'
                f'{int(lat_d):0d}{int(lat_m):02d}{int(lat_s*10):03d}N'
                f'{int(long_d):03d}{int(long_m):02d}{int(long_s*10):03d}E'
                f'A00000{int(beacon["altitude"]):05d}\n'
            )

            igc_dir = Path(Config.IGC_FOLDER)
            igc_dir.mkdir(exist_ok=True)
            # Use a date-stamped filename to avoid collisions across days
            igc_filename = igc_dir / f"{dt.year}{dt.month:02d}{dt.day:02d}{nickname}.igc"

            # Determine if we need to write header (new/empty file) or append only B-record
            file_is_new = not igc_filename.exists() or igc_filename.stat().st_size == 0

            if file_is_new:
                # Build A-record and minimal H-records, then first B-record
                a_record = f"A{Config.IGC_MANUFACTURER_CODE}{Config.IGC_DEVICE_SERIAL}OGNServer\n"
                day = dt.day
                month = dt.month
                year2 = dt.year % 100
                h_records = (
                    f"IGC_FILE_FORMAT_VERSION=6\n"
                    f"HFDTE{day:02d}{month:02d}{year2:02d}\n"
                )
                with open(igc_filename, "w") as igc_file:
                    igc_file.write(a_record)
                    igc_file.write(h_records)
                    igc_file.write(igc_line)
            else:
                with open(igc_filename, "a") as igc_file:
                    igc_file.write(igc_line)
    
    def _write_location(self, beacon: dict):
        self._rotate_location_file_if_needed()
        # Use current UTC timestamp instead of beacon timestamp
        current_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        with open(Config.LOCATION_FILE, "a") as loc_file:
            loc_file.write(
                f'{beacon["address"]},'
                f'{beacon["latitude"]},'
                f'{beacon["longitude"]},'
                f'{beacon["track"]},'
                f'{beacon["altitude"]},'
                f'{beacon["ground_speed"]},'
                f'{beacon["climb_rate"]},'
                f'{current_ts},'
                f'{beacon["symbolcode"]}\n'
            )
    
    def _is_in_target_area(self, beacon: dict) -> bool:
        if len(self.serverdata) < 4:
            return False
        try:
            target_lat = float(self.serverdata[Config.API_TARGET_LAT_INDEX])
            target_lon = float(self.serverdata[Config.API_TARGET_LON_INDEX])
            loc_filter = float(self.serverdata[Config.API_LOC_FILTER_INDEX]) if len(self.serverdata) > Config.API_LOC_FILTER_INDEX else Config.LOCATION_FILTER_DEGREES
            return (
                beacon["latitude"] < target_lat + loc_filter and
                beacon["latitude"] > target_lat - loc_filter and
                beacon["longitude"] < target_lon + loc_filter and
                beacon["longitude"] > target_lon - loc_filter
            )
        except (ValueError, IndexError):
            return False
    
    def _update_climb_history(self, address: str, timestamp: datetime.datetime, climb_rate: float, ground_speed: float, track: float, latitude: float, longitude: float):
        # Strip timezone info to make timestamps naive (consistent with _cleanup_old_beacons pattern)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)
        
        current_time = datetime.datetime.utcnow()
        cutoff_time = current_time - datetime.timedelta(seconds=30)
        
        if address not in self._climb_history:
            self._climb_history[address] = []
        
        self._climb_history[address].append((timestamp, climb_rate, ground_speed, track, latitude, longitude))
        self._climb_history[address] = [
            entry for entry in self._climb_history[address] 
            if entry[0] >= cutoff_time
        ][-20:]
    
    def _get_avg_climb_rate(self, address: str) -> float | None:
        if address not in self._climb_history or not self._climb_history[address]:
            return None
        
        total_climb = sum(entry[1] for entry in self._climb_history[address])
        count = len(self._climb_history[address])
        return total_climb / count
    
    def _are_positions_within_radius(self, positions: list[tuple], radius_m: float) -> bool:
        """Check if all positions are within radius of each other using max pairwise distance.
        
        Args:
            positions: List of history tuples (timestamp, climb, speed, track, lat, lon)
            radius_m: Maximum allowed distance in meters between any two points
        
        Returns:
            True if all pairwise distances are < radius_m
        """
        if len(positions) < 2:
            return False
        
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                lat1, lon1 = positions[i][4], positions[i][5]
                lat2, lon2 = positions[j][4], positions[j][5]
                distance = _haversine_distance(lat1, lon1, lat2, lon2)
                if distance >= radius_m:
                    return False
        return True
    
    def _is_circling(self, address: str) -> bool:
        """Determine if a glider is actively circling in a thermal.
        
        Criteria (all must be met):
        1. At least 5 position samples
        2. Average climb > 0.5 m/s
        3. All positions within 200m of each other (max pairwise distance < 200m)
        4. Current climb >= 0.2 m/s
        """
        # Criterion 1: Check minimum sample count
        if address not in self._climb_history or len(self._climb_history[address]) < 5:
            sample_count = len(self._climb_history.get(address, []))
            logger.debug(f"[CIRCLING] {address}: INSUFFICIENT_SAMPLES - have {sample_count}, need 5")
            return False
        
        history = self._climb_history[address]
        
        # Criterion 2: Calculate average climb rate
        avg_climb = sum(entry[1] for entry in history) / len(history)
        if avg_climb <= 0.5:
            logger.debug(f"[CIRCLING] {address}: LOW_AVG_CLIMB - avg={avg_climb:.2f} m/s, need > 0.5 m/s")
            return False

        # Criterion 3: Check all positions within 200m (strict less-than)
        max_distance = 0.0
        for i in range(len(history)):
            for j in range(i + 1, len(history)):
                lat1, lon1 = history[i][4], history[i][5]
                lat2, lon2 = history[j][4], history[j][5]
                dist = _haversine_distance(lat1, lon1, lat2, lon2)
                max_distance = max(max_distance, dist)
        
        if max_distance >= 200.0:
            logger.debug(f"[CIRCLING] {address}: TOO_SPREAD_OUT - max_pairwise_dist={max_distance:.1f}m, need < 200m")
            return False
        
        # Criterion 4: Current climb >= 0.2 m/s
        current_climb = history[-1][1]
        if current_climb is not None and current_climb < 0.2:
            logger.debug(f"[CIRCLING] {address}: LOW_CURRENT_CLIMB - current={current_climb:.2f} m/s, need >= 0.2 m/s")
            return False
        
        # All criteria met - circling detected
        logger.debug(f"[CIRCLING] {address}: CIRCLING_DETECTED - samples={len(history)}, avg_climb={avg_climb:.2f} m/s, max_dist={max_distance:.1f}m, current_climb={current_climb:.2f} m/s")
        return True
    
    def _cleanup_old_beacons(self):
        if time.time() > self.timestamp + Config.CLEANUP_INTERVAL_SECONDS:
            self.timestamp = time.time()
            i = 0
            while i < len(self.current_messages):
                ref_ts = self.current_messages[i].reference_timestamp
                if ref_ts.tzinfo is not None:
                    ref_ts = ref_ts.replace(tzinfo=None)
                age = (datetime.datetime.utcnow() - ref_ts).total_seconds()
                should_remove = age > Config.BEACON_TIMEOUT_SECONDS
                if not should_remove and self.current_messages[i].climb_rate == 0:
                    should_remove = age > Config.BEACON_ZERO_VELOCITY_TIMEOUT_SECONDS
                if should_remove:
                    address = self.current_messages[i].address
                    self.current_messages.pop(i)
                    # History persists independently - TTL cleanup happens in _update_climb_history() when new beacons arrive
                    i -= 1
                i += 1
        if time.time() > self.igc_cleanup_timestamp + 3600:
            self.igc_cleanup_timestamp = time.time()
            self._cleanup_old_igc_files()
            self._cleanup_old_location_files()  # ← ADD THIS LINE
    
    def process_beacon(self, raw_message: str):
        self._cleanup_old_beacons()
        self._load_names_df()
        
        try:
            beacon = parse(raw_message)
        except AprsParseError as e:
            if self._error_count > 10:
                return
            self._error_count += 1
            self._last_error_time = time.time()
            return
        except NotImplementedError as e:
            if self._error_count > 10:
                return
            self._error_count += 1
            self._last_error_time = time.time()
            print(f'{e}: {raw_message}')
            return
        except AttributeError as e:
            if self._error_count > 10:
                return
            self._error_count += 1
            self._last_error_time = time.time()
            print(f'{e}: {raw_message}')
            return
        except ValueError as e:
            if self._error_count > 10:
                return
            self._error_count += 1
            self._last_error_time = time.time()
            # Log full context for debugging malformed APRS packets
            # Truncate raw_message to avoid excessive log volume
            raw_msg_preview = raw_message[:200] + "..." if len(raw_message) > 200 else raw_message
            logger.warning(
                f"ValueError parsing beacon: {e} | raw_message: {raw_msg_preview}"
            )
            return
        
        if "address" not in beacon:
            return
        
        if "address" in beacon and "ground_speed" in beacon and "climb_rate" in beacon:
            if "symbolcode" in beacon and self._is_valid_symbol(beacon["symbolcode"]):
                if self._is_in_target_area(beacon):
                    # Plausibility check before writing
                    prev_beacon = self._last_position_cache.get(beacon["address"])
                    is_plausible, reason = _is_beacon_plausible(prev_beacon, beacon, beacon["address"])
                    
                    if not is_plausible:
                        logger.warning(f"Implausible beacon rejected: {beacon['address']} - {reason}")
                    else:
                        self._write_location(beacon)
                
                current_beacon = Beacon(
                    address=beacon["address"],
                    name=beacon["name"][-4:],
                    latitude=beacon["latitude"],
                    longitude=beacon["longitude"],
                    track=beacon["track"],
                    altitude=beacon["altitude"],
                    ground_speed=beacon["ground_speed"],
                    climb_rate=beacon["climb_rate"],
                    reference_timestamp=beacon["reference_timestamp"],
                    beacon_type=beacon["symbolcode"]
                )
                
                try:
                    ind = self.current_messages.index(current_beacon)
                    self.current_messages[ind] = current_beacon
                except ValueError:
                    self.current_messages.append(current_beacon)

                # SAR: update last-known-position cache for this aircraft
                address = beacon["address"]
                # Use current UTC timestamp (ignore beacon timestamp)
                current_ts = datetime.datetime.now(datetime.timezone.utc)
                timestamp_str = current_ts.isoformat()
                registration = get_registration(address, self.ddb_devices)
                self._last_position_cache[address] = {
                    "address": address,
                    "latitude": beacon["latitude"],
                    "longitude": beacon["longitude"],
                    "altitude": beacon["altitude"],
                    "timestamp": timestamp_str,
                    "registration": registration,
                }
                self._last_beacon_times[address] = current_ts
                self._last_beacon_received = current_ts
                self._total_beacons_received += 1
                # GEofence monitoring: determine if the beacon is off-field and track offline aircraft
                geofence_off = bool(is_off_field(beacon["latitude"], beacon["longitude"], self._geofences))
                if geofence_off:
                    self._offline_aircraft[address] = {
                        "address": address,
                        "last_position": {
                            "lat": beacon["latitude"],
                            "lon": beacon["longitude"],
                            "alt": beacon["altitude"]
                        },
                        "last_seen": beacon["reference_timestamp"],
                        "is_off_field": True
                    }
                else:
                    # Remove from offline tracking if back on-field
                    self._offline_aircraft.pop(address, None)
        
        if "address" in beacon:
            self._write_igc_file(beacon)
    
    def get_messages_in_bounds(self, bounds: list[str]) -> str:
        import time
        cache_key = ",".join(bounds)
        
        if cache_key in self._cache:
            cached_time, cached_result = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl_seconds:
                return cached_result
        
        try:
            center_lat = (float(bounds[0]) + float(bounds[1])) / 2
            center_lon = (float(bounds[2]) + float(bounds[3])) / 2
        except ValueError:
            return "invalid bound values"
        
        filtered_messages = []
        for msg in self.current_messages:
            if abs(float(msg.latitude) - center_lat) < 0.5:
                if abs(float(msg.longitude) - center_lon) < 0.5:
                    self._update_climb_history(msg.address, msg.reference_timestamp, msg.climb_rate, msg.ground_speed, msg.track, msg.latitude, msg.longitude)
                    
                    nickname = self._get_nickname(msg.address)
                    if nickname is not None:
                        avg_climb = self._get_avg_climb_rate(msg.address)
                        is_circling = self._is_circling(msg.address)
                        filtered_messages.append(msg.to_csv_row(nickname, avg_climb, is_circling))
        
        count = len(filtered_messages)
        header = f"{count},{count}\n"
        result = header + "".join(filtered_messages)
        
        self._cache[cache_key] = (time.time(), result)
        return result

    def get_offline_aircraft(self, threshold_minutes: int = 10) -> list[dict]:
        """Get aircraft that are off-field and stationary for >threshold_minutes."""
        offline: list[dict] = []
        if not getattr(self, "_offline_aircraft", None):
            return offline
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        for address, info in list(self._offline_aircraft.items()):
            last_seen = info.get("last_seen")
            last_seen_dt: datetime.datetime | None = None
            if isinstance(last_seen, datetime.datetime):
                if last_seen.tzinfo is None:
                    last_seen_dt = last_seen.replace(tzinfo=datetime.timezone.utc)
                else:
                    last_seen_dt = last_seen.astimezone(datetime.timezone.utc)
            else:
                try:
                    ts = int(last_seen)
                    last_seen_dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                except Exception:
                    last_seen_dt = None
            if last_seen_dt is None:
                continue
            delta = now_utc - last_seen_dt
            if delta.total_seconds() > threshold_minutes * 60:
                offline.append(info)
        return offline

    def get_missing_aircraft(self, threshold_minutes: int = 15) -> list[dict]:
        """Get aircraft that haven't sent any beacon for >threshold_minutes."""
        missing: list[dict] = []
        if not getattr(self, "_last_beacon_times", None):
            return missing
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        for address, last_beacon_time in list(self._last_beacon_times.items()):
            if isinstance(last_beacon_time, datetime.datetime):
                if last_beacon_time.tzinfo is None:
                    last_beacon_time = last_beacon_time.replace(tzinfo=datetime.timezone.utc)
                else:
                    last_beacon_time = last_beacon_time.astimezone(datetime.timezone.utc)
            else:
                try:
                    last_beacon_time = datetime.datetime.fromtimestamp(int(last_beacon_time), tz=datetime.timezone.utc)
                except Exception:
                    continue
            delta = now_utc - last_beacon_time
            if delta.total_seconds() > threshold_minutes * 60:
                missing.append({
                    "address": address,
                    "last_seen": last_beacon_time.isoformat(),
                    "seconds_ago": delta.total_seconds()
                })
        return missing

    def run(self, callback: Optional[Callable] = None, autoreconnect: bool = True):
        import socket
        import time
        logger.info("Starting OGN client...")
        max_retries = Config.OGN_CONNECT_MAX_RETRIES
        retry_delay = Config.OGN_CONNECT_RETRY_DELAY
        dns_retry_delay = Config.OGN_DNS_RETRY_DELAY
        host = Config.OGN_SERVER_HOST
        fallback_host = Config.OGN_SERVER_HOST_FALLBACK
        current_host = host
        # Ensure there is a fresh client reference point for potential shutdowns

        for attempt in range(max_retries):
            try:
                # Apply APRS filter if scheduled for update
                aprs_filter = Config.OGN_APRS_FILTER
                if self._filter_needs_update and self._last_aprs_filter:
                    aprs_filter = self._last_aprs_filter
                    self._filter_needs_update = False
                    logger.info(f"Applying APRS filter: {aprs_filter}")
                
                # Auto-switch to port 14580 when filter is active (supports filtering)
                if aprs_filter:
                    ogn_settings.APRS_SERVER_PORT = 14580
                    logger.info("Using port 14580 for filtered APRS-IS connection")
                else:
                    ogn_settings.APRS_SERVER_PORT = 14570  # Default unfiltered port
                
                ogn_settings.APRS_SERVER_HOST = current_host
                client = AprsClient(
                    aprs_user=Config.OGN_APRS_USER,
                    aprs_filter=aprs_filter,
                    settings=ogn_settings
                )
                # Expose the client for external shutdown via self.shutdown()
                self.client = client
                client.connect()
                logger.info(f"OGN client connected to OGN server ({current_host})")
                
                if callback is None:
                    callback = self.process_beacon
                
                client.run(callback=callback, autoreconnect=autoreconnect)
            except socket.gaierror as e:
                # DNS resolution failure - specific handling
                logger.error(f"DNS resolution failed: {current_host} - {e}")
                if current_host == host and fallback_host:
                    logger.info(f"Attempting fallback hostname: {fallback_host}")
                    current_host = fallback_host
                    continue
                if attempt < max_retries - 1:
                    logger.warning(f"Retrying DNS resolution in {dns_retry_delay}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(dns_retry_delay)
                else:
                    logger.critical(f"DNS resolution failed after {max_retries} attempts. Continuing with graceful degradation.")
            except Exception as e:
                logger.warning(f"OGN client error: {e}. Reconnecting...")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.critical(f"Connection failed after {max_retries} attempts. Continuing with graceful degradation.")

    def shutdown(self):
        """Shutdown the OGN client by closing the underlying AprsClient if connected.

        This method is idempotent and safe to call multiple times.
        """
        try:
            logger.info("Shutting down OGN client...")
            if getattr(self, 'client', None) is not None:
                client = self.client
                if hasattr(client, 'close'):
                    try:
                        client.close()
                    except Exception as e:
                        logger.exception("Error while closing OGN AprsClient: %s", e)
            # Reset reference regardless of whether a client existed
            self.client = None
            logger.info("OGN client shutdown completed")
        except Exception as e:
            logger.exception("OGN client shutdown failed: %s", e)

    def get_overdue_aircraft(self, threshold_minutes: int = 30) -> list[dict]:
        """Return a list of aircraft that have not reported a beacon within the threshold.

        Args:
            threshold_minutes: The overdue threshold in minutes (default 30).

        Returns:
            A list of last-known-position dictionaries for overdue aircraft. Each entry
            contains address, latitude, longitude, altitude, timestamp (UTC),
            and registration when available.
        """
        overdue: list[dict] = []
        if not self._last_beacon_times:
            return overdue

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        for address, last_ts in list(self._last_beacon_times.items()):
            if last_ts is None:
                continue
            delta = now_utc - last_ts
            if delta.total_seconds() > threshold_minutes * 60:
                pos = self._last_position_cache.get(address)
                if pos:
                    overdue.append(pos)
        return overdue

    def get_last_position(self, flarm_id: str) -> dict | None:
        """Return the last known position for a given FLARM ID if available."""
        return self._last_position_cache.get(flarm_id)

    def get_all_last_positions(self) -> dict[str, dict]:
        """Return all tracked last-known positions for SAR queries."""
        return self._last_position_cache
    
    def get_status(self) -> dict:
        """Get server status for Telegram /status command.
        
        Returns:
            Dict with beacon statistics:
            - last_beacon_received: ISO format timestamp of last OGN beacon received
            - total_beacons_received: Total count of beacons processed since startup
            - current_aircraft_count: Number of aircraft in current_messages
            - api_stats: Dict with API sending statistics (from api.py _api_stats)
        """
        from .api import _api_stats
        
        # Get last beacon time from all current messages
        last_beacon_iso = None
        if self.current_messages:
            try:
                last_beacon_dt = max(b.reference_timestamp for b in self.current_messages)
                last_beacon_iso = last_beacon_dt.isoformat()
            except Exception:
                pass
        
        return {
            "last_beacon_received": last_beacon_iso,
            "total_beacons_received": self._total_beacons_received,
            "current_aircraft_count": len(self.current_messages),
            "api_stats": {
                "last_data_sent": _api_stats["last_data_sent"].isoformat() if _api_stats["last_data_sent"] else None,
                "beacons_sent_count": _api_stats["beacons_sent_count"],
            },
        }
    
    def refresh_ddb_devices(self) -> int:
        """Refresh FLARM Device Database from ddb.glidernet.org.
        
        Forces a fresh download of the DDB, updates internal cache,
        and returns the number of devices loaded.
        
        Returns:
            Number of devices in the refreshed DDB.
            Returns 0 if download fails.
        """
        from .ddb import download_ddb
        
        logger.info("Refreshing DDB from ddb.glidernet.org...")
        fresh_devices = download_ddb()
        
        if fresh_devices is None:
            logger.error("DDB refresh failed - download returned no data")
            return 0
        
        # Save to cache
        from .ddb import save_ddb_cache
        save_ddb_cache(fresh_devices)
        
        # Convert to dictionary format used by client
        from .ddb import normalize_flarm_id
        self.ddb_devices = {}
        for device in fresh_devices:
            device_id = normalize_flarm_id(device.get("device_id", ""))
            if device_id:
                self.ddb_devices[device_id] = device
        
        logger.info(f"DDB refreshed successfully: {len(self.ddb_devices)} devices")
        return len(self.ddb_devices)
