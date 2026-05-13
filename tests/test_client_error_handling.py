"""Tests for OGN client error handling (DNS failures, connection errors, graceful degradation)."""
import pytest
import socket
from unittest.mock import Mock, patch, MagicMock, call
from src.ogn_server.client import OGNClient
from src.ogn_server.config import Config


class TestDNSErrorHandling:
    """Test DNS resolution failure handling."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.serverdata = ["token", "0.0.0.0", "47.5", "13.0", "0.2"]
    
    @patch('src.ogn_server.client.AprsClient')
    @patch('src.ogn_server.client.get_ddb_devices', return_value={})
    @patch('src.ogn_server.client.Path.exists', return_value=False)
    def test_dns_resolution_failure_logs_error(self, mock_path_exists, mock_get_ddb, mock_aprs_client, caplog):
        """DNS resolution failures should be logged at ERROR level with clear message."""
        import logging
        caplog.set_level(logging.ERROR)
        
        # Mock AprsClient.connect() to raise socket.gaierror
        mock_client_instance = Mock()
        mock_client_instance.connect.side_effect = socket.gaierror("Name or service not known")
        mock_aprs_client.return_value = mock_client_instance
        
        client = OGNClient(self.serverdata)
        
        # Run with max_retries=1 to avoid long test execution
        with patch.object(Config, 'OGN_CONNECT_MAX_RETRIES', 1):
            client.run(callback=Mock(), autoreconnect=False)
        
        # Verify ERROR log contains DNS failure message
        assert any("DNS resolution failed" in record.message for record in caplog.records if record.levelno == logging.ERROR)
    
    @patch('src.ogn_server.client.AprsClient')
    @patch('src.ogn_server.client.get_ddb_devices', return_value={})
    @patch('src.ogn_server.client.Path.exists', return_value=False)
    def test_dns_uses_longer_retry_delay(self, mock_path_exists, mock_get_ddb, mock_aprs_client):
        """DNS failures should use longer retry delay (60s) than connection errors (10s)."""
        import time
        
        # Mock AprsClient.connect() to raise socket.gaierror on fallback, then succeed
        mock_client_instance = Mock()
        mock_client_instance.connect.side_effect = [
            socket.gaierror("Primary host DNS fails"),
            socket.gaierror("Fallback host DNS fails"),
            None  # Third attempt succeeds
        ]
        mock_aprs_client.return_value = mock_client_instance
        
        client = OGNClient(self.serverdata)
        
        # Track sleep calls
        with patch.object(time, 'sleep') as mock_sleep:
            with patch.object(Config, 'OGN_CONNECT_MAX_RETRIES', 3):
                with patch.object(mock_client_instance, 'run'):
                    client.run(callback=Mock(), autoreconnect=False)
            
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_once_with(Config.OGN_DNS_RETRY_DELAY)


class TestGracefulDegradation:
    """Test that client doesn't crash after max retries."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.serverdata = ["token", "0.0.0.0", "47.5", "13.0", "0.2"]
    
    @patch('src.ogn_server.client.AprsClient')
    @patch('src.ogn_server.client.get_ddb_devices', return_value={})
    @patch('src.ogn_server.client.Path.exists', return_value=False)
    def test_graceful_degradation_after_max_retries(self, mock_path_exists, mock_get_ddb, mock_aprs_client, caplog):
        """Client should NOT raise exception after max retries - should continue gracefully."""
        import logging
        caplog.set_level(logging.CRITICAL)
        
        # Mock AprsClient.connect() to always fail
        mock_client_instance = Mock()
        mock_client_instance.connect.side_effect = ConnectionError("Connection refused")
        mock_aprs_client.return_value = mock_client_instance
        
        client = OGNClient(self.serverdata)
        
        # Should NOT raise exception even after max retries
        with patch.object(Config, 'OGN_CONNECT_MAX_RETRIES', 2):
            with patch.object(mock_client_instance, 'run'):
                # This should complete without raising
                client.run(callback=Mock(), autoreconnect=False)
        
        # Verify CRITICAL log message about graceful degradation
        assert any("Continuing with graceful degradation" in record.message for record in caplog.records if record.levelno == logging.CRITICAL)


class TestFallbackHostname:
    """Test fallback hostname functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.serverdata = ["token", "0.0.0.0", "47.5", "13.0", "0.2"]
    
    @patch('src.ogn_server.client.AprsClient')
    @patch('src.ogn_server.client.get_ddb_devices', return_value={})
    @patch('src.ogn_server.client.Path.exists', return_value=False)
    def test_fallback_hostname_attempted_on_dns_failure(self, mock_path_exists, mock_get_ddb, mock_aprs_client, caplog):
        """When primary host DNS fails, should attempt fallback hostname."""
        import logging
        caplog.set_level(logging.INFO)
        
        # Mock AprsClient.connect() to fail on primary, succeed on fallback
        mock_client_instance = Mock()
        mock_client_instance.connect.side_effect = [
            socket.gaierror("Primary host DNS fails"),
            None  # Fallback host succeeds
        ]
        mock_aprs_client.return_value = mock_client_instance
        
        client = OGNClient(self.serverdata)
        
        with patch.object(Config, 'OGN_CONNECT_MAX_RETRIES', 2):
            with patch.object(mock_client_instance, 'run'):
                client.run(callback=Mock(), autoreconnect=False)
        
        # Verify INFO log about fallback attempt
        assert any("Attempting fallback hostname" in record.message for record in caplog.records if record.levelno == logging.INFO)
        
        # Verify connect was called twice (primary + fallback)
        assert mock_client_instance.connect.call_count == 2
    
    @patch('src.ogn_server.client.AprsClient')
    @patch('src.ogn_server.client.get_ddb_devices', return_value={})
    @patch('src.ogn_server.client.Path.exists', return_value=False)
    def test_fallback_not_used_for_connection_errors(self, mock_path_exists, mock_get_ddb, mock_aprs_client):
        """Fallback hostname should only be used for DNS errors, not connection errors."""
        # Mock AprsClient.connect() to fail with ConnectionError (not DNS)
        mock_client_instance = Mock()
        mock_client_instance.connect.side_effect = ConnectionError("Connection refused")
        mock_aprs_client.return_value = mock_client_instance
        
        client = OGNClient(self.serverdata)
        
        with patch.object(Config, 'OGN_CONNECT_MAX_RETRIES', 1):
            with patch.object(mock_client_instance, 'run'):
                client.run(callback=Mock(), autoreconnect=False)
        
        # Verify AprsClient was instantiated only once (no fallback for connection errors)
        assert mock_aprs_client.call_count == 1


class TestConfigurableRetryParams:
    """Test that retry parameters are configurable via Config."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.serverdata = ["token", "0.0.0.0", "47.5", "13.0", "0.2"]
    
    @patch('src.ogn_server.client.AprsClient')
    @patch('src.ogn_server.client.get_ddb_devices', return_value={})
    @patch('src.ogn_server.client.Path.exists', return_value=False)
    def test_uses_config_retry_values(self, mock_path_exists, mock_get_ddb, mock_aprs_client):
        """Client should use Config.OGN_CONNECT_MAX_RETRIES instead of hardcoded values."""
        import time
        
        # Mock AprsClient.connect() to fail
        mock_client_instance = Mock()
        mock_client_instance.connect.side_effect = ConnectionError("Connection refused")
        mock_aprs_client.return_value = mock_client_instance
        
        client = OGNClient(self.serverdata)
        
        custom_max_retries = 3
        custom_retry_delay = 5
        
        with patch.object(Config, 'OGN_CONNECT_MAX_RETRIES', custom_max_retries):
            with patch.object(Config, 'OGN_CONNECT_RETRY_DELAY', custom_retry_delay):
                with patch.object(time, 'sleep') as mock_sleep:
                    with patch.object(mock_client_instance, 'run'):
                        client.run(callback=Mock(), autoreconnect=False)
        
        # Verify sleep was called custom_max_retries - 1 times (after each failure except last)
        assert mock_sleep.call_count == custom_max_retries - 1
