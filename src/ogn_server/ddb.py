"""
DDB (FLARM Device Database) module for downloading and caching FLARM device information.

This module provides functions to:
- Download the FLARM Device Database from ddb.glidernet.org
- Cache the database locally for faster subsequent restarts
- Look up aircraft registration by FLARM ID
- Handle rate limiting with exponential backoff
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

from .config import Config


logger = logging.getLogger(__name__)


def normalize_flarm_id(flarm_id: str) -> str:
    """
    Normalize a FLARM ID for DDB lookup.
    
    FLARM IDs can be in various formats:
    - "FLR3ECA1B" (full ID with FLR prefix)
    - "3ECA1B" (6-char hex without prefix)
    - "flr3eca1b" (lowercase)
    
    DDB uses 6-char uppercase hex (e.g., "3ECA1B").
    
    Args:
        flarm_id: Raw FLARM ID from beacon data
        
    Returns:
        Normalized 6-char uppercase hex string
    """
    normalized = flarm_id.upper()
    if normalized.startswith("FLR"):
        normalized = normalized[3:]
    
    if len(normalized) < 6:
        normalized = normalized.zfill(6)
    
    return normalized


def load_ddb_cache() -> list[dict] | None:
    """
    Load DDB cache from ddb.json file.
    
    Returns:
        List of device dicts if cache exists, None otherwise
    """
    cache_path = Path(Config.DDB_CACHE_FILE)
    if not cache_path.exists():
        return None
    
    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "devices" in data:
            return data["devices"]
        else:
            logger.warning("Invalid DDB cache format")
            return None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load DDB cache: {e}")
        return None


def save_ddb_cache(devices: list[dict]) -> bool:
    """
    Save DDB data to ddb.json cache file.
    
    Args:
        devices: List of device dicts from DDB API
        
    Returns:
        True if saved successfully, False on failure
    """
    cache_path = Path(Config.DDB_CACHE_FILE)
    
    try:
        with open(cache_path, "w") as f:
            json.dump(devices, f, indent=2)
        logger.info(f"DDB cache saved to {cache_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to save DDB cache: {e}")
        return False


def download_ddb() -> list[dict] | None:
    """
    Download DDB JSON from server with 429 retry logic.
    
    Implements exponential backoff for rate limiting:
    - Initial delay: 2 seconds
    - Backoff factor: 2x per retry
    - Max retries: 5 attempts
    
    Args:
        None
        
    Returns:
        List of device dicts on success, None on failure
    """
    url = Config.DDB_URL
    
    last_error = None
    for attempt in range(Config.DDB_MAX_RETRIES):
        try:
            response = requests.get(
                url,
                timeout=Config.DDB_TIMEOUT_SECONDS,
                headers={"Accept": "application/json"}
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "devices" in data:
                    return data["devices"]
                else:
                    logger.warning(f"Unexpected DDB response format: {type(data)}")
                    return None
                
            elif response.status_code == 429:
                wait_time = Config.DDB_RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
                logger.warning(
                    f"DDB rate limited (429). Waiting {wait_time}s before retry {attempt + 1}/{Config.DDB_MAX_RETRIES}"
                )
                time.sleep(wait_time)
            else:
                last_error = f"DDB returned status {response.status_code}"
                logger.warning(last_error)
                break
                
        except requests.Timeout:
            last_error = f"DDB request timed out after {Config.DDB_TIMEOUT_SECONDS}s"
            logger.warning(last_error)
        except requests.RequestException as e:
            last_error = f"DDB request failed: {e}"
            logger.warning(last_error)
            break
    
    logger.error(f"DDB download failed after {Config.DDB_MAX_RETRIES} attempts: {last_error or 'unknown error'}")
    return None


def get_ddb_devices() -> dict[str, dict]:
    """
    Get DDB devices as a dictionary keyed by normalized device_id.
    
    Returns:
        Dict mapping normalized device_id (e.g., "3ECA1B") to device info dict
    """
    raw_devices = load_ddb_cache()
    if raw_devices is None:
        raw_devices = download_ddb()
        if raw_devices is not None:
            save_ddb_cache(raw_devices)
    
    if raw_devices is None:
        return {}
    
    devices_dict = {}
    for device in raw_devices:
        device_id = normalize_flarm_id(device.get("device_id", ""))
        if device_id:
            devices_dict[device_id] = device
    
    return devices_dict


def get_registration(flarm_id: str, ddb_devices: dict[str, dict]) -> str | None:
    """
    Look up aircraft registration for a FLARM ID.
    
    Priority:
    1. DDB registration (from FLARM Device Database)
    
    Args:
        flarm_id: Full FLARM ID (e.g., "FLR3ECA1B") or partial ID
        ddb_devices: Dict of DDB devices from get_ddb_devices()
        
    Returns:
        Aircraft registration string if found, None otherwise
    """
    normalized_id = normalize_flarm_id(flarm_id)
    
    if normalized_id not in ddb_devices:
        return None
    
    device = ddb_devices[normalized_id]
    registration = device.get("registration", "")
    
    if not registration or not registration.strip():
        return None
    
    return registration.strip()


def get_aircraft_model(flarm_id: str, ddb_devices: dict[str, dict]) -> str | None:
    """
    Look up aircraft model for a FLARM ID.
    
    Args:
        flarm_id: Full FLARM ID (e.g., "FLR3ECA1B") or partial ID
        ddb_devices: Dict of DDB devices from get_ddb_devices()
        
    Returns:
        Aircraft model string if found, None otherwise
    """
    normalized_id = normalize_flarm_id(flarm_id)
    
    if normalized_id not in ddb_devices:
        return None
    
    device = ddb_devices[normalized_id]
    model = device.get("aircraft_model", "")
    
    if not model or not model.strip():
        return None
    
    return model.strip()


def get_cn(flarm_id: str, ddb_devices: dict[str, dict]) -> str | None:
    """
    Look up competition number (cn) for a FLARM ID.
    
    Args:
        flarm_id: Full FLARM ID (e.g., "FLR3ECA1B") or partial ID
        ddb_devices: Dict of DDB devices from get_ddb_devices()
        
    Returns:
        Competition number string if found, None otherwise
    """
    normalized_id = normalize_flarm_id(flarm_id)
    
    if normalized_id not in ddb_devices:
        return None
    
    device = ddb_devices[normalized_id]
    cn = device.get("cn", "")
    
    if not cn or not cn.strip():
        return None
    
    return cn.strip()
