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
from zoneinfo import ZoneInfo

# Geofence utilities
from .geofence import load_geofences, is_off_field

from .beacon import Beacon
from .config import Config
from .ddb import get_ddb_devices, get_registration
from .formatters import JSONFormatter, logger

# Initialize module-level JSON-formatted logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


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
        self._climb_history: dict[str, list[tuple[datetime.datetime, float, float, float]]] = {}
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
        
        # Suppress OGN library's closeout warning (normal disconnection)
        ogn_client_logger = logging.getLogger('ogn.client.client')
        ogn_client_logger.addFilter(OGNCloseoutFilter())
        
        self._migrate_location_file()

    def refresh_ddb_devices(self) -> int:
        """Refresh the FLARM device database. Returns number of devices loaded."""
        self.ddb_devices = get_ddb_devices()
        logger.info(f"DDB refreshed: {len(self.ddb_devices)} devices")
        return len(self.ddb_devices)
    
    def _load_names_df(self):
        names_path = Path(Config.NAMES_FILE)
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
        with open(Config.LOCATION_FILE, "a") as loc_file:
            loc_file.write(
                f'{beacon["address"]},'
                f'{beacon["latitude"]},'
                f'{beacon["longitude"]},'
                f'{beacon["track"]},'
                f'{beacon["altitude"]},'
                f'{beacon["ground_speed"]},'
                f'{beacon["climb_rate"]},'
                f'{beacon["reference_timestamp"]},'
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
    
    def _update_climb_history(self, address: str, timestamp: datetime.datetime, climb_rate: float, ground_speed: float, track: float):
        # Strip timezone info to make timestamps naive (consistent with _cleanup_old_beacons pattern)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)
        
        current_time = datetime.datetime.utcnow()
        cutoff_time = current_time - datetime.timedelta(seconds=30)
        
        if address not in self._climb_history:
            self._climb_history[address] = []
        
        self._climb_history[address].append((timestamp, climb_rate, ground_speed, track))
        self._climb_history[address] = [
            entry for entry in self._climb_history[address] 
            if entry[0] >= cutoff_time
        ]
    
    def _get_avg_climb_rate(self, address: str) -> float | None:
        if address not in self._climb_history or not self._climb_history[address]:
            return None
        
        total_climb = sum(entry[1] for entry in self._climb_history[address])
        count = len(self._climb_history[address])
        return total_climb / count
    
    def _is_circling(self, address: str) -> bool:
        """Determine if a glider is actively cirling in a thermal.
        
        Criteria (all must be met over 30 seconds of history):
        1. Average climb > 0.5 m/s
        """
        if address not in self._climb_history or len(self._climb_history[address]) < 30:
            return False
        
        history = self._climb_history[address]
        
        # Calculate average climb rate
        avg_climb = sum(entry[1] for entry in history) / len(history)
        if avg_climb <= 0.5:
            return False

        if history[-1][3] is not None and history[-2][3] is not None:
            track_change = abs(history[-1][3] - history[-2][3])
            if track_change > 180:
                track_change = 360 - track_change
            if track_change < 10:
                return False
        
        if history[-1][1] is not None and history[-1][1] < 0.2:
            return False
                
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
                    del self._climb_history[address]
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

                self._update_climb_history(beacon["address"], beacon["reference_timestamp"], beacon["climb_rate"], beacon["ground_speed"], beacon["track"])

                # SAR: update last-known-position cache for this aircraft
                address = beacon["address"]
                # Compute Berlin time for the timestamp
                ts = beacon["reference_timestamp"]
                berlin_tz = ZoneInfo("Europe/Berlin")
                if ts.tzinfo is None:
                    ts_berlin = ts.replace(tzinfo=berlin_tz)
                else:
                    ts_berlin = ts.astimezone(berlin_tz)
                timestamp_str = ts_berlin.isoformat()
                registration = get_registration(address, self.ddb_devices)
                self._last_position_cache[address] = {
                    "address": address,
                    "latitude": beacon["latitude"],
                    "longitude": beacon["longitude"],
                    "altitude": beacon["altitude"],
                    "timestamp": timestamp_str,
                    "registration": registration,
                }
                self._last_beacon_times[address] = ts_berlin
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
        berlin_tz = ZoneInfo("Europe/Berlin")
        now_berlin = datetime.datetime.now(berlin_tz)
        for address, info in list(self._offline_aircraft.items()):
            last_seen = info.get("last_seen")
            last_seen_dt: datetime.datetime | None = None
            if isinstance(last_seen, datetime.datetime):
                if last_seen.tzinfo is None:
                    last_seen_dt = last_seen.replace(tzinfo=berlin_tz)
                else:
                    last_seen_dt = last_seen.astimezone(berlin_tz)
            else:
                try:
                    ts = int(last_seen)
                    last_seen_dt = datetime.datetime.fromtimestamp(ts, tz=berlin_tz)
                except Exception:
                    last_seen_dt = None
            if last_seen_dt is None:
                continue
            delta = now_berlin - last_seen_dt
            if delta.total_seconds() > threshold_minutes * 60:
                offline.append(info)
        return offline

    def run(self, callback: Optional[Callable] = None, autoreconnect: bool = True):
        import time
        logger.info("Starting OGN client...")
        max_retries = 5
        retry_delay = 10
        # Ensure there is a fresh client reference point for potential shutdowns

        for attempt in range(max_retries):
            try:
                ogn_settings.APRS_SERVER_HOST = Config.OGN_SERVER_HOST
                client = AprsClient(
                    aprs_user=Config.OGN_APRS_USER,
                    aprs_filter=Config.OGN_APRS_FILTER,
                    settings=ogn_settings
                )
                # Expose the client for external shutdown via self.shutdown()
                self.client = client
                client.connect()
                logger.info("OGN client connected to OGN server")
                
                if callback is None:
                    callback = self.process_beacon
                
                client.run(callback=callback, autoreconnect=autoreconnect)
            except Exception as e:
                logger.warning(f"OGN client error: {e}. Reconnecting...")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(f"Max retries reached. Last error: {e}")
                    raise

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

        The comparison is performed in the Europe/Berlin timezone to align with SAR operations.

        Args:
            threshold_minutes: The overdue threshold in minutes (default 30).

        Returns:
            A list of last-known-position dictionaries for overdue aircraft. Each entry
            contains address, latitude, longitude, altitude, timestamp (Berlin time),
            and registration when available.
        """
        overdue: list[dict] = []
        if not self._last_beacon_times:
            return overdue

        berlin_tz = ZoneInfo("Europe/Berlin")
        now_berlin = datetime.datetime.now(berlin_tz)
        for address, last_ts in list(self._last_beacon_times.items()):
            if last_ts is None:
                continue
            delta = now_berlin - last_ts
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
