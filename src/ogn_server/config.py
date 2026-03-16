import os
from pathlib import Path


class Config:
    OGN_SERVER_HOST = "glidern3.glidernet.org"
    OGN_APRS_USER = "N0CALL"
    OGN_APRS_FILTER = ""
    
    NAMES_FILE = "names.csv"
    SERVERDATA_FILE = "serverdata.txt"
    PRIVATE_KEY_FILE = "private.key"
    ADMIN_CHAT_ID_FILE = "adminChat.id"
    LOCATION_FILE = "location.txt"
    
    BEACON_TIMEOUT_SECONDS = 30
    CLEANUP_INTERVAL_SECONDS = 30
    LOCATION_FILTER_DEGREES = 0.01
    
    API_ACCESS_TOKEN_INDEX = 0
    API_HOST_INDEX = 1
    API_TARGET_LAT_INDEX = 2
    API_TARGET_LON_INDEX = 3
    
    @classmethod
    def load_serverdata(cls):
        serverdata_path = Path(cls.SERVERDATA_FILE)
        if serverdata_path.exists():
            with open(cls.SERVERDATA_FILE, "r") as f:
                return [line.strip() for line in f.readlines()]
        return None
    
    @classmethod
    def load_admin_chat_id(cls):
        admin_chat_id_path = Path(cls.ADMIN_CHAT_ID_FILE)
        if admin_chat_id_path.exists():
            return admin_chat_id_path.read_bytes()
        return b"0"
    
    @classmethod
    def load_private_key(cls):
        priv_key_path = Path(cls.PRIVATE_KEY_FILE)
        if priv_key_path.exists():
            with open(cls.PRIVATE_KEY_FILE, "r") as f:
                return f.readlines()[0].strip()
        return None
