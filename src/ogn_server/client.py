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
        # Track the AprsClient instance to allow graceful shutdowns
        self.client = None
    
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
        if address in self.names_df["fid"].values:
            nickname = self.names_df[self.names_df["fid"].str.contains(address)].iloc[0]["name"]
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
            igc_filename = igc_dir / f"{dt.year}{dt.month:02d}{dt.day:02d}{nickname}.igc"
            with open(igc_filename, "a") as igc_file:
                igc_file.write(igc_line)
    
    def _write_location(self, beacon: dict):
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
            return (
                beacon["latitude"] < target_lat + Config.LOCATION_FILTER_DEGREES and
                beacon["latitude"] > target_lat - 0.03 and
                beacon["longitude"] < target_lon + Config.LOCATION_FILTER_DEGREES and
                beacon["longitude"] > target_lon - Config.LOCATION_FILTER_DEGREES
            )
        except (ValueError, IndexError):
            return False
    
    def _update_climb_history(self, address: str, timestamp: datetime.datetime, climb_rate: float, ground_speed: float, track: float):
        # Strip timezone info to make timestamps naive (consistent with _cleanup_old_beacons pattern)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)
        
        current_time = datetime.datetime.utcnow()
        cutoff_time = current_time - datetime.timedelta(seconds=60)
        
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
        
        Criteria (all must be met over 60 seconds of history):
        1. Average climb > 0.5 m/s
        """
        if address not in self._climb_history or len(self._climb_history[address]) < 60:
            return False
        
        history = self._climb_history[address]
        
        # Calculate average climb rate
        avg_climb = sum(entry[1] for entry in history) / len(history)
        if avg_climb <= 0.5:
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
