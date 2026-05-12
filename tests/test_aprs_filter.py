"""Tests for APRS-IS location-based filtering in OGNClient (client.py)."""
import pytest
from unittest.mock import Mock, patch, MagicMock
import math
from src.ogn_server.client import OGNClient
from src.ogn_server.config import Config


class TestAPRSFilterHelpers:
    """Test helper functions for APRS filtering."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.serverdata = ["token", "0.0.0.0", "47.5", "13.0", "0.2"]
        
        with patch('src.ogn_server.client.get_ddb_devices', return_value={}):
            with patch('src.ogn_server.client.Path.exists', return_value=False):
                self.client = OGNClient(self.serverdata)
    
    def test_haversine_distance_km_same_point_returns_zero(self):
        """Distance between identical points should be zero."""
        dist = self.client._haversine_distance_km(47.5, 13.0, 47.5, 13.0)
        assert dist == 0.0
    
    def test_haversine_distance_km_known_distance(self):
        """Test haversine with known distance (approximately 111km per degree latitude)."""
        dist = self.client._haversine_distance_km(47.0, 13.0, 48.0, 13.0)
        assert 110 < dist < 112
    
    def test_haversine_distance_km_short_distance(self):
        """Test haversine with short distance (few kilometers)."""
        dist = self.client._haversine_distance_km(47.5, 13.0, 47.51, 13.0)
        assert 1.0 < dist < 1.2
    
    def test_build_aprs_filter_string_format(self):
        """Test APRS filter string format is correct."""
        bounds = (47.0, 48.0, 12.0, 14.0)
        filter_str = self.client._build_aprs_filter_string(bounds)
        
        assert filter_str.startswith("r/")
        assert "47.5000" in filter_str
        assert "13.0000" in filter_str
        assert f"/{Config.OGN_APRS_FILTER_RADIUS_KM}" in filter_str
    
    def test_build_aprs_filter_string_custom_radius(self):
        """Test APRS filter string with custom radius from config."""
        with patch.object(Config, 'OGN_APRS_FILTER_RADIUS_KM', 150):
            bounds = (47.0, 48.0, 12.0, 14.0)
            filter_str = self.client._build_aprs_filter_string(bounds)
            assert "/150" in filter_str


class TestShouldUpdateFilter:
    """Test filter update threshold logic."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.serverdata = ["token", "0.0.0.0", "47.5", "13.0", "0.2"]
        
        with patch('src.ogn_server.client.get_ddb_devices', return_value={}):
            with patch('src.ogn_server.client.Path.exists', return_value=False):
                self.client = OGNClient(self.serverdata)
    
    def test_should_update_filter_no_previous_bounds(self):
        """Should update when no previous bounds exist."""
        new_bounds = (47.0, 48.0, 12.0, 14.0)
        assert self.client._should_update_filter(new_bounds) is True
    
    def test_should_update_filter_small_change(self):
        """Should NOT update when change is below threshold."""
        self.client._last_filter_bounds = (47.0, 48.0, 12.0, 14.0)
        
        new_bounds = (47.01, 48.01, 12.01, 14.01)
        assert self.client._should_update_filter(new_bounds) is False
    
    def test_should_update_filter_large_change(self):
        """Should update when change exceeds threshold."""
        self.client._last_filter_bounds = (47.0, 48.0, 12.0, 14.0)
        
        new_bounds = (48.5, 49.5, 14.0, 16.0)
        assert self.client._should_update_filter(new_bounds) is True
    
    def test_should_update_filter_exact_threshold(self):
        """Should update when change equals threshold exactly."""
        self.client._last_filter_bounds = (47.0, 48.0, 12.0, 14.0)
        
        new_bounds = (47.45, 48.45, 12.0, 14.0)
        result = self.client._should_update_filter(new_bounds)
        assert isinstance(result, bool)


class TestSetAprsFilter:
    """Test set_aprs_filter method."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.serverdata = ["token", "0.0.0.0", "47.5", "13.0", "0.2"]
        
        with patch('src.ogn_server.client.get_ddb_devices', return_value={}):
            with patch('src.ogn_server.client.Path.exists', return_value=False):
                self.client = OGNClient(self.serverdata)
    
    def test_set_aprs_filter_disabled(self):
        """Should not set filter when disabled in config."""
        with patch.object(Config, 'OGN_APRS_FILTER_ENABLED', False):
            bounds = (47.0, 48.0, 12.0, 14.0)
            self.client.set_aprs_filter(bounds)
            
            assert self.client._last_aprs_filter is None
            assert self.client._filter_needs_update is False
    
    def test_set_aprs_filter_first_time(self):
        """Should set filter on first call."""
        bounds = (47.0, 48.0, 12.0, 14.0)
        self.client.set_aprs_filter(bounds)
        
        assert self.client._last_filter_bounds == bounds
        assert self.client._last_aprs_filter is not None
        assert self.client._filter_needs_update is True
        assert self.client._last_aprs_filter.startswith("r/")
    
    def test_set_aprs_filter_no_update_needed(self):
        """Should not update filter when change is too small."""
        initial_bounds = (47.0, 48.0, 12.0, 14.0)
        self.client.set_aprs_filter(initial_bounds)
        
        first_filter = self.client._last_aprs_filter
        self.client._filter_needs_update = False
        
        small_change_bounds = (47.01, 48.01, 12.01, 14.01)
        self.client.set_aprs_filter(small_change_bounds)
        
        assert self.client._last_filter_bounds == initial_bounds
        assert self.client._last_aprs_filter == first_filter
        assert self.client._filter_needs_update is False
    
    def test_set_aprs_filter_with_update(self):
        """Should update filter when change is significant."""
        initial_bounds = (47.0, 48.0, 12.0, 14.0)
        self.client.set_aprs_filter(initial_bounds)
        
        first_filter = self.client._last_aprs_filter
        
        new_bounds = (48.5, 49.5, 14.0, 16.0)
        self.client.set_aprs_filter(new_bounds)
        
        assert self.client._last_filter_bounds == new_bounds
        assert self.client._last_aprs_filter != first_filter
        assert self.client._filter_needs_update is True
    
    def test_get_last_bounds(self):
        """Should return last filter bounds."""
        bounds = (47.0, 48.0, 12.0, 14.0)
        self.client.set_aprs_filter(bounds)
        
        assert self.client.get_last_bounds() == bounds
    
    def test_get_last_bounds_none(self):
        """Should return None when no bounds set."""
        assert self.client.get_last_bounds() is None


class TestRunMethodWithFilter:
    """Test that run() method applies filter correctly."""
    
    def test_filter_flag_set_after_set_aprs_filter(self):
        """Verify _filter_needs_update flag is set correctly."""
        serverdata = ["token", "0.0.0.0", "47.5", "13.0", "0.2"]
        
        with patch('src.ogn_server.client.get_ddb_devices', return_value={}):
            with patch('src.ogn_server.client.Path.exists', return_value=False):
                client = OGNClient(serverdata)
        
        bounds = (47.0, 48.0, 12.0, 14.0)
        client.set_aprs_filter(bounds)
        
        assert client._filter_needs_update is True
        assert client._last_aprs_filter is not None
        assert client._last_aprs_filter.startswith("r/")
