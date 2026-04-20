import pytest
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.ogn_server.ddb import (
    normalize_flarm_id,
    load_ddb_cache,
    save_ddb_cache,
    download_ddb,
    get_ddb_devices,
    get_registration,
    get_aircraft_model,
    get_cn,
)


class TestNormalizeFlarmId:
    def test_full_flarm_id_with_flr_prefix(self):
        result = normalize_flarm_id("FLR3ECA1B")
        assert result == "3ECA1B"
    
    def test_6_char_hex_without_prefix(self):
        result = normalize_flarm_id("3ECA1B")
        assert result == "3ECA1B"
    
    def test_lowercase_input(self):
        result = normalize_flarm_id("flr3eca1b")
        assert result == "3ECA1B"
    
    def test_short_id_pads_with_zeros(self):
        result = normalize_flarm_id("123")
        assert result == "000123"
    
    def test_empty_string_returns_six_zeros(self):
        result = normalize_flarm_id("")
        assert result == "000000"


class TestLoadDdbCache:
    def test_returns_none_when_cache_file_missing(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = load_ddb_cache()
            assert result is None
        finally:
            os.chdir(original_cwd)
    
    def test_reads_list_format_from_json(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            import json
            test_data = [{"device_id": "3ECA1B", "registration": "D-1234"}]
            Path("ddb.json").write_text(json.dumps(test_data))
            
            result = load_ddb_cache()
            assert result is not None
            assert len(result) == 1
            assert result[0]["device_id"] == "3ECA1B"
        finally:
            os.chdir(original_cwd)
    
    def test_reads_dict_with_devices_key(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            import json
            test_data = {"devices": [{"device_id": "3ECA1B", "registration": "D-1234"}]}
            Path("ddb.json").write_text(json.dumps(test_data))
            
            result = load_ddb_cache()
            assert result is not None
            assert len(result) == 1
            assert result[0]["device_id"] == "3ECA1B"
        finally:
            os.chdir(original_cwd)
    
    def test_returns_none_for_invalid_format(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            invalid_data = {"wrong": "format"}
            Path("ddb.json").write_text(str(invalid_data))
            
            result = load_ddb_cache()
            assert result is None
        finally:
            os.chdir(original_cwd)


class TestSaveDdbCache:
    def test_saves_to_ddb_json(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            test_data = [{"device_id": "3ECA1B", "registration": "D-1234"}]
            
            result = save_ddb_cache(test_data)
            assert result is True
            
            cache_path = Path("ddb.json")
            assert cache_path.exists()
            
            import json
            with open(cache_path, "r") as f:
                loaded = json.load(f)
            assert loaded == test_data
        finally:
            os.chdir(original_cwd)


class TestDownloadDdb:
    @patch('src.ogn_server.ddb.requests.get')
    def test_success_with_list_response(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"device_id": "3ECA1B", "registration": "D-1234"}
        ]
        mock_get.return_value = mock_response
        
        result = download_ddb()
        assert result is not None
        assert len(result) == 1
        assert result[0]["device_id"] == "3ECA1B"
    
    @patch('src.ogn_server.ddb.requests.get')
    def test_success_with_dict_response(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "devices": [{"device_id": "3ECA1B", "registration": "D-1234"}]
        }
        mock_get.return_value = mock_response
        
        result = download_ddb()
        assert result is not None
        assert len(result) == 1
    
    @patch('src.ogn_server.ddb.requests.get')
    def test_429_retry_logic(self, mock_get):
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = [{"device_id": "3ECA1B"}]
        
        mock_get.side_effect = [mock_response_429, mock_response_429, mock_response_success]
        
        result = download_ddb()
        assert result is not None
        assert len(result) == 1
    
    @patch('src.ogn_server.ddb.requests.get')
    def test_max_retries_exhausted(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response
        
        result = download_ddb()
        assert result is None
    
    @patch('src.ogn_server.ddb.requests.get')
    def test_timeout_handling(self, mock_get):
        import requests
        mock_get.side_effect = requests.Timeout("Timeout")
        
        result = download_ddb()
        assert result is None


class TestGetDdbDevices:
    @patch('src.ogn_server.ddb.load_ddb_cache')
    def test_returns_dict_keyed_by_normalized_device_id(self, mock_load_cache):
        mock_load_cache.return_value = [
            {"device_id": "3ECA1B", "registration": "D-1234"},
            {"device_id": "000001", "registration": "N5678"}
        ]
        
        result = get_ddb_devices()
        assert isinstance(result, dict)
        assert "3ECA1B" in result
        assert "000001" in result
        assert result["3ECA1B"]["registration"] == "D-1234"
    
    @patch('src.ogn_server.ddb.load_ddb_cache')
    @patch('src.ogn_server.ddb.download_ddb')
    def test_downloads_if_cache_missing(self, mock_download, mock_load_cache):
        mock_load_cache.return_value = None
        mock_download.return_value = [{"device_id": "3ECA1B"}]
        
        result = get_ddb_devices()
        assert isinstance(result, dict)
        assert "3ECA1B" in result
        mock_download.assert_called_once()
    
    @patch('src.ogn_server.ddb.load_ddb_cache')
    @patch('src.ogn_server.ddb.download_ddb')
    @patch('src.ogn_server.ddb.save_ddb_cache')
    def test_saves_cache_after_download(self, mock_save, mock_download, mock_load_cache):
        mock_load_cache.return_value = None
        mock_download.return_value = [{"device_id": "3ECA1B"}]
        
        get_ddb_devices()
        mock_save.assert_called_once()


class TestGetRegistration:
    def test_returns_registration_when_found(self):
        ddb_devices = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": "D-1234"}
        }
        
        result = get_registration("FLR3ECA1B", ddb_devices)
        assert result == "D-1234"
    
    def test_returns_none_when_not_found(self):
        ddb_devices = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": "D-1234"}
        }
        
        result = get_registration("FLR999999", ddb_devices)
        assert result is None
    
    def test_returns_none_when_registration_empty(self):
        ddb_devices = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": ""}
        }
        
        result = get_registration("FLR3ECA1B", ddb_devices)
        assert result is None
    
    def test_handles_partial_flarm_id(self):
        ddb_devices = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": "D-1234"}
        }
        
        result = get_registration("3ECA1B", ddb_devices)
        assert result == "D-1234"


class TestGetAircraftModel:
    def test_returns_model_when_found(self):
        ddb_devices = {
            "3ECA1B": {"device_id": "3ECA1B", "aircraft_model": "SZD-41 Jantar Std"}
        }
        
        result = get_aircraft_model("FLR3ECA1B", ddb_devices)
        assert result == "SZD-41 Jantar Std"
    
    def test_returns_none_when_not_found(self):
        ddb_devices = {
            "3ECA1B": {"device_id": "3ECA1B"}
        }
        
        result = get_aircraft_model("FLR3ECA1B", ddb_devices)
        assert result is None


class TestGetCn:
    def test_returns_cn_when_found(self):
        ddb_devices = {
            "3ECA1B": {"device_id": "3ECA1B", "cn": "23"}
        }
        
        result = get_cn("FLR3ECA1B", ddb_devices)
        assert result == "23"
    
    def test_returns_none_when_cn_empty(self):
        ddb_devices = {
            "3ECA1B": {"device_id": "3ECA1B", "cn": ""}
        }
        
        result = get_cn("FLR3ECA1B", ddb_devices)
        assert result is None
