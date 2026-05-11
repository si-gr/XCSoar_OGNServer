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
    
    # Handle Docker volume mount edge case: if host file didn't exist at docker-compose up time,
    # Docker creates an empty directory instead of a file
    if cache_path.is_dir():
        try:
            cache_path.rmdir()
            logger.warning(f"Removed directory at {cache_path} (Docker volume mount artifact)")
        except OSError as e:
            logger.error(f"Failed to remove directory at {cache_path}: {e}")
            return False
    
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
            elif response.status_code >= 500:
                wait_time = Config.DDB_RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
                logger.warning(
                    f"DDB server error ({response.status_code}). Waiting {wait_time}s before retry {attempt + 1}/{Config.DDB_MAX_RETRIES}"
                )
                if attempt < Config.DDB_MAX_RETRIES - 1:
                    time.sleep(wait_time)
                else:
                    last_error = f"DDB returned status {response.status_code}"
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


def get_ddb_devices(force_refresh: bool = False) -> dict[str, dict]:
    """
    Get DDB devices as a dictionary keyed by normalized device_id.
    
    Always returns cached data if available, even if stale.
    Attempts to refresh cache in background if TTL expired.
    
    Args:
        force_refresh: If True, always attempt to download fresh data
        
    Returns:
        Dict mapping normalized device_id (e.g., "3ECA1B") to device info dict.
        Returns cached data (even if stale) on download failure.
    """
    raw_devices = load_ddb_cache()
    cache_exists = raw_devices is not None
    
    # Check if cache is stale
    cache_path = Path(Config.DDB_CACHE_FILE)
    cache_age_minutes = 0
    if cache_exists and cache_path.exists():
        try:
            mtime = cache_path.stat().st_mtime
            cache_age_minutes = int((time.time() - mtime) / 60)
        except OSError:
            cache_age_minutes = float('inf')
    
    cache_is_stale = cache_age_minutes > Config.DDB_CACHE_TTL_MINUTES
    
    # Always use existing cache if available (even if stale)
    if cache_exists and not force_refresh:
        devices_dict = {}
        for device in raw_devices:
            device_id = normalize_flarm_id(device.get("device_id", ""))
            if device_id:
                devices_dict[device_id] = device
        
        # Attempt background refresh if stale (non-blocking)
        if cache_is_stale:
            logger.info(f"DDB cache is {cache_age_minutes} minutes old (TTL: {Config.DDB_CACHE_TTL_MINUTES}min). Using cached data, attempting refresh...")
            try:
                fresh_devices = download_ddb()
                if fresh_devices is not None:
                    save_ddb_cache(fresh_devices)
                    logger.info("DDB cache refreshed successfully")
                else:
                    logger.warning("DDB refresh failed, continuing with cached data")
            except Exception as e:
                logger.warning(f"DDB refresh error: {e}. Continuing with cached data")
        
        return devices_dict
    
    # No cache or forced refresh - must download
    if not cache_exists or force_refresh:
        if cache_exists and force_refresh:
            logger.info("Forced DDB refresh requested")
        
        raw_devices = download_ddb()
        if raw_devices is not None:
            save_ddb_cache(raw_devices)
        elif cache_exists:
            # Download failed but we have old cache - use it
            logger.warning("DDB download failed, using existing cached data")
            raw_devices = load_ddb_cache()
    
    if raw_devices is None:
        logger.error("DDB unavailable and no cache exists. Aircraft registrations will not be resolved.")
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
