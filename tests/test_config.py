import pytest
import tempfile
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.ogn_server.config import Config


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
