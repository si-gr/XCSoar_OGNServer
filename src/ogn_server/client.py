import os
import datetime
from pathlib import Path
from typing import Callable, Optional
import pandas as pd
from ogn.client import AprsClient, settings as ogn_settings
from ogn.parser import parse, AprsParseError

from .beacon import Beacon
from .config import Config


class OGNClient:
    def __init__(self, serverdata: list):
        self.serverdata = serverdata
        self.current_messages: list[Beacon] = []
        self.timestamp = 0
        self.names_df = pd.DataFrame()
        self.names_df_time = 0
        self._load_names_df()
    
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
    
    def _get_nickname(self, beacon_name: str) -> Optional[str]:
        all_nicknames = self.names_df[self.names_df["fid"] == beacon_name]
        if len(all_nicknames) > 0:
            nickname = all_nicknames["name"].iloc[0]
            if nickname == '....':
                return None
            return nickname
        return None
    
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
            
            igc_filename = f"{dt.year}{dt.month}{dt.day}{nickname}.igc"
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
    
    def _cleanup_old_beacons(self):
        import time
        if time.time() > self.timestamp + Config.CLEANUP_INTERVAL_SECONDS:
            self.timestamp = time.time()
            i = 0
            while i < len(self.current_messages):
                age = (datetime.datetime.utcnow() - self.current_messages[i].reference_timestamp).total_seconds()
                if age > Config.BEACON_TIMEOUT_SECONDS:
                    self.current_messages.pop(i)
                    i -= 1
                i += 1
    
    def process_beacon(self, raw_message: str):
        self._cleanup_old_beacons()
        self._load_names_df()
        
        try:
            beacon = parse(raw_message)
        except AprsParseError as e:
            print(f'Error, {e.message}')
            return
        except NotImplementedError as e:
            print(f'{e}: {raw_message}')
            return
        except AttributeError as e:
            print(f'{e}: {raw_message}')
            return
        except ValueError as e:
            print(f'ValueError: {e}')
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
        
        if "address" in beacon:
            self._write_igc_file(beacon)
    
    def get_messages_in_bounds(self, bounds: list[str]) -> str:
        try:
            center_lat = (float(bounds[0]) + float(bounds[1])) / 2
            center_lon = (float(bounds[2]) + float(bounds[3])) / 2
        except ValueError:
            return "invalid bound values"
        
        filtered_messages = []
        for msg in self.current_messages:
            if abs(float(msg.latitude) - center_lat) < 0.5:
                if abs(float(msg.longitude) - center_lon) < 0.5:
                    nickname = self._get_nickname(msg.name)
                    if nickname is not None:
                        filtered_messages.append(msg.to_csv_row(nickname))
        
        count = len(filtered_messages)
        header = f"{count},{count}\n"
        return header + "".join(filtered_messages)
    
    def run(self, callback: Optional[Callable] = None, autoreconnect: bool = True):
        ogn_settings.APRS_SERVER_HOST = Config.OGN_SERVER_HOST
        client = AprsClient(
            aprs_user=Config.OGN_APRS_USER,
            aprs_filter=Config.OGN_APRS_FILTER,
            settings=ogn_settings
        )
        client.connect()
        
        if callback is None:
            callback = self.process_beacon
        
        client.run(callback=callback, autoreconnect=autoreconnect)
