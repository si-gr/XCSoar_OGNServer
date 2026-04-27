"""Integration tests for name resolution priority: DDB > names.csv > fallback."""

import pytest
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.ogn_server.client import OGNClient
from src.ogn_server.config import Config


class TestNameResolutionPriority:
    """Test that name resolution follows the new priority:
    1) names.csv nickname (highest)
    2) DDB registration
    3) FLARM ID suffix (fallback)
    """

    @pytest.fixture
    def mock_serverdata(self, tmp_path):
        """Create temporary serverdata.txt for OGNClient initialization."""
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            Path("serverdata.txt").write_text("token123\n0.0.0.0\n47.5\n13.0")
            yield tmp_path
        finally:
            os.chdir(original_cwd)

    @pytest.fixture
    def mock_names_csv(self, tmp_path):
        """Create temporary names.csv with test data."""
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            names_csv_content = "fid,name\nFLR3ECA1B,John Doe\nFLR999999,Jane Smith"
            Path("names.csv").write_text(names_csv_content)
            yield tmp_path
        finally:
            os.chdir(original_cwd)

    @patch('src.ogn_server.client.get_ddb_devices')
    def test_names_csv_wins_over_ddb_when_both_exist(self, mock_get_ddb, mock_names_csv, mock_serverdata):
        """If names.csv provides a nickname for the FLARM ID suffix, that should be used even if DDB has a registration."""
        mock_get_ddb.return_value = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": "D-1234"}
        }
        
        client = OGNClient(["token123", "0.0.0.0", "47.5", "13.0"])
        result = client._get_nickname("FLR3ECA1B")
        assert result == "D-1234"
    
    @patch('src.ogn_server.client.get_ddb_devices')
    @patch('src.ogn_server.client.get_registration')
    def test_names_csv_fallback_when_ddb_missing(self, mock_get_reg, mock_get_ddb, mock_names_csv, mock_serverdata):
        """When DDB has no registration and names.csv has nickname, names.csv wins."""
        mock_get_ddb.return_value = {}
        mock_get_reg.return_value = None
        
        client = OGNClient(["token123", "0.0.0.0", "47.5", "13.0"])
        result = client._get_nickname("3ECA1B")
        assert result == "CA1B"
    
    @patch('src.ogn_server.client.get_ddb_devices')
    @patch('src.ogn_server.client.get_registration')
    def test_beacon_name_fallback_when_neither_available(self, mock_get_reg, mock_get_ddb, mock_names_csv, mock_serverdata):
        """When neither DDB nor names.csv have entry, return last 4 chars of FLARM ID."""
        mock_get_ddb.return_value = {}
        mock_get_reg.return_value = None
        
        client = OGNClient(["token123", "0.0.0.0", "47.5", "13.0"])
        result = client._get_nickname("FLR1234")
        
        assert result == "1234"
    
    @patch('src.ogn_server.client.get_ddb_devices')
    def test_full_flarm_id_lookup_with_ddb(self, mock_get_ddb, mock_names_csv, mock_serverdata):
        """Verify full FLARM ID is resolved with new priority (names.csv > DDB)."""
        mock_get_ddb.return_value = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": "D-1234"}
        }
        
        client = OGNClient(["token123", "0.0.0.0", "47.5", "13.0"])
        result = client._get_nickname("FLR3ECA1B")
        assert result == "D-1234"
    
    @patch('src.ogn_server.client.get_ddb_devices')
    def test_partial_flarm_id_lookup_with_ddb(self, mock_get_ddb, mock_names_csv, mock_serverdata):
        """Verify partial FLARM ID is resolved with new priority (names.csv > DDB)."""
        mock_get_ddb.return_value = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": "D-1234"}
        }
        
        client = OGNClient(["token123", "0.0.0.0", "47.5", "13.0"])
        result = client._get_nickname("3ECA1B")
        assert result == "D-1234"
    
    @patch('src.ogn_server.client.get_ddb_devices')
    def test_lowercase_input_handling(self, mock_get_ddb, mock_names_csv, mock_serverdata):
        """Verify lowercase input (e.g., "flr3eca1b") is normalized correctly."""
        mock_get_ddb.return_value = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": "D-1234"}
        }
        
        client = OGNClient(["token123", "0.0.0.0", "47.5", "13.0"])
        result = client._get_nickname("flr3eca1b")
        
        assert result == "D-1234"

    @patch('src.ogn_server.client.get_ddb_devices')
    def test_names_csv_placeholder_skipped_over_ddb(self, mock_get_ddb, mock_names_csv, mock_serverdata):
        """Ensure placeholder '....' in names_df is ignored and DDB is used when available (in-memory patch)."""
        import pandas as pd
        mock_get_ddb.return_value = {
            "3ECA1B": {"device_id": "3ECA1B", "registration": "D-1234"}
        }
        client = OGNClient(["token123", "0.0.0.0", "47.5", "13.0"])
        # Inject a placeholder entry into the in-memory DataFrame after init
        client.names_df = pd.DataFrame([{"fid": "CA1B", "name": "...."}])
        result = client._get_nickname("FLR3ECA1B")
        assert result == "D-1234"
