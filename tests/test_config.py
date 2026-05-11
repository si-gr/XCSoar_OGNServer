import pytest
import tempfile
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.ogn_server.config import Config, get_log_level, LOG_LEVELS
import logging


class TestConfig:
    def test_default_values(self):
        assert Config.OGN_SERVER_HOST == "glidern3.glidernet.org"
        assert Config.OGN_APRS_USER == "N0CALL"
        assert Config.OGN_APRS_FILTER == ""
        assert Config.NAMES_FILE == "names.csv"
        assert Config.SERVERDATA_FILE == "serverdata.txt"
        assert Config.PRIVATE_KEY_FILE == "private.key"
        assert Config.ADMIN_CHAT_ID_FILE == "adminChat.id"
        assert Config.LOCATION_FILE == "location.txt"
        assert Config.LOCATION_RETENTION_DAYS == 2
        assert Config.BEACON_TIMEOUT_SECONDS == 30
        assert Config.CLEANUP_INTERVAL_SECONDS == 30
        assert Config.LOCATION_FILTER_DEGREES == 0.01
    
    def test_load_serverdata_returns_none_when_file_missing(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = Config.load_serverdata()
            assert result is None
        finally:
            os.chdir(original_cwd)
    
    def test_load_serverdata_parses_file(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            serverdata_content = "token123\n0.0.0.0\n47.5\n13.0"
            Path("serverdata.txt").write_text(serverdata_content)
            
            result = Config.load_serverdata()
            
            assert result is not None
            assert len(result) == 4
            assert result[0] == "token123"
            assert result[1] == "0.0.0.0"
            assert result[2] == "47.5"
            assert result[3] == "13.0"
        finally:
            os.chdir(original_cwd)
    
    def test_load_admin_chat_id_returns_default_when_file_missing(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = Config.load_admin_chat_id()
            assert result == "0"
        finally:
            os.chdir(original_cwd)
    
    def test_load_admin_chat_id_reads_file(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            Path("adminChat.id").write_bytes(b"123456")
            
            result = Config.load_admin_chat_id()
            
            assert result == "123456"
        finally:
            os.chdir(original_cwd)
    
    def test_load_private_key_returns_none_when_file_missing(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = Config.load_private_key()
            assert result is None
        finally:
            os.chdir(original_cwd)
    
    def test_load_private_key_reads_file(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            Path("private.key").write_text("token123456:ABCDEF\nsecondline")
            
            result = Config.load_private_key()
            
            assert result == "token123456:ABCDEF"
        finally:
            os.chdir(original_cwd)


class TestGetLogLevel:
    """Test cases for get_log_level() function."""
    
    def test_log_levels_mapping_exists(self):
        """Verify LOG_LEVELS mapping contains all valid levels."""
        assert "CRITICAL" in LOG_LEVELS
        assert "ERROR" in LOG_LEVELS
        assert "WARNING" in LOG_LEVELS
        assert "INFO" in LOG_LEVELS
        assert "DEBUG" in LOG_LEVELS
    
    def test_log_levels_mapping_values(self):
        """Verify LOG_LEVELS maps to correct logging constants."""
        assert LOG_LEVELS["CRITICAL"] == logging.CRITICAL
        assert LOG_LEVELS["ERROR"] == logging.ERROR
        assert LOG_LEVELS["WARNING"] == logging.WARNING
        assert LOG_LEVELS["INFO"] == logging.INFO
        assert LOG_LEVELS["DEBUG"] == logging.DEBUG
    
    def test_get_log_level_default(self, monkeypatch):
        """Test default log level when env var is not set."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert get_log_level() == logging.INFO
    
    def test_get_log_level_info(self, monkeypatch):
        """Test LOG_LEVEL=INFO."""
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        assert get_log_level() == logging.INFO
    
    def test_get_log_level_debug(self, monkeypatch):
        """Test LOG_LEVEL=DEBUG."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        assert get_log_level() == logging.DEBUG
    
    def test_get_log_level_warning(self, monkeypatch):
        """Test LOG_LEVEL=WARNING."""
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        assert get_log_level() == logging.WARNING
    
    def test_get_log_level_error(self, monkeypatch):
        """Test LOG_LEVEL=ERROR."""
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        assert get_log_level() == logging.ERROR
    
    def test_get_log_level_critical(self, monkeypatch):
        """Test LOG_LEVEL=CRITICAL."""
        monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
        assert get_log_level() == logging.CRITICAL
    
    def test_get_log_level_case_insensitive(self, monkeypatch):
        """Test that log level is case-insensitive."""
        monkeypatch.setenv("LOG_LEVEL", "debug")
        assert get_log_level() == logging.DEBUG
        
        monkeypatch.setenv("LOG_LEVEL", "Info")
        assert get_log_level() == logging.INFO
        
        monkeypatch.setenv("LOG_LEVEL", "CrItIcAl")
        assert get_log_level() == logging.CRITICAL
    
    def test_get_log_level_invalid_value_defaults_to_info(self, monkeypatch, capsys):
        """Test that invalid log level defaults to INFO with warning."""
        monkeypatch.setenv("LOG_LEVEL", "INVALID")
        result = get_log_level()
        assert result == logging.INFO
        
        captured = capsys.readouterr()
        assert "Warning: Invalid LOG_LEVEL 'INVALID'" in captured.out
    
    def test_get_log_level_empty_string_defaults_to_info(self, monkeypatch):
        """Test that empty string defaults to INFO."""
        monkeypatch.setenv("LOG_LEVEL", "")
        assert get_log_level() == logging.INFO
