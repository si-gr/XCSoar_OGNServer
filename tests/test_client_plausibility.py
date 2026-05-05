"""Tests for plausibility checking in OGNClient (client.py)."""
import pytest
from src.ogn_server.client import (
    _haversine_distance,
    _calculate_ground_speed,
    _calculate_vertical_speed,
    _is_beacon_plausible,
    _parse_timestamp,
    PLAUSIBILITY_MAX_ALTITUDE_M,
)
import datetime


class TestClientPlausibilityHelpers:
    """Test helper functions for beacon plausibility validation."""
    
    def test_haversine_same_point_returns_zero(self):
        """Distance between identical points should be zero."""
        dist = _haversine_distance(47.5, 13.0, 47.5, 13.0)
        assert dist == 0.0
    
    def test_haversine_known_distance(self):
        """Test haversine with known distance (approximately 111km per degree latitude)."""
        # 1 degree latitude ≈ 111 km
        dist = _haversine_distance(47.0, 13.0, 48.0, 13.0)
        assert 110000 < dist < 112000  # Allow some tolerance for Earth radius approximation
    
    def test_ground_speed_calculation(self):
        """Test ground speed calculation."""
        # 100m in 10 seconds = 10 m/s
        speed = _calculate_ground_speed(47.0, 13.0, 47.0009, 13.0, 10.0)
        assert 9.5 < speed < 10.5
    
    def test_ground_speed_zero_dt_returns_inf(self):
        """Zero or negative time delta should return infinity."""
        speed = _calculate_ground_speed(47.0, 13.0, 47.001, 13.0, 0.0)
        assert speed == float('inf')
    
    def test_vertical_speed_calculation(self):
        """Test vertical speed calculation."""
        # 50m climb in 10 seconds = 5 m/s
        vs = _calculate_vertical_speed(1000.0, 1050.0, 10.0)
        assert vs == 5.0
    
    def test_vertical_speed_zero_dt_returns_inf(self):
        """Zero or negative time delta should return infinity."""
        vs = _calculate_vertical_speed(1000.0, 1050.0, 0.0)
        assert vs == float('inf')


class TestClientBeaconPlausibility:
    """Test _is_beacon_plausible function for beacon validation."""
    
    def test_first_beacon_always_plausible(self):
        """First beacon (no previous) should always be accepted if altitude valid."""
        beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 1500,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        
        is_plausible, reason = _is_beacon_plausible(None, beacon, "FLR123")
        assert is_plausible is True
        assert reason is None
    
    def test_first_beacon_over_altitude_rejected(self):
        """First beacon with altitude > 10,000m should be rejected."""
        beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 15000,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        
        is_plausible, reason = _is_beacon_plausible(None, beacon, "FLR123")
        assert is_plausible is False
        assert "altitude" in reason
    
    def test_altitude_at_limit_accepted(self):
        """Beacon with altitude exactly at 10,000m should be accepted."""
        beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 10000,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        
        is_plausible, reason = _is_beacon_plausible(None, beacon, "FLR123")
        assert is_plausible is True
        assert reason is None
    
    def test_speed_over_threshold_rejected(self):
        """Beacon implying ground speed > 97.2 m/s should be rejected."""
        prev_beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 1500,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        curr_beacon = {
            "latitude": 48.5,  # ~111km away in 60 seconds = ~1850 m/s (impossible)
            "longitude": 13.0,
            "altitude": 1500,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 1, 0),
        }
        
        is_plausible, reason = _is_beacon_plausible(prev_beacon, curr_beacon, "FLR123")
        assert is_plausible is False
        assert "speed" in reason
    
    def test_climb_rate_over_threshold_rejected(self):
        """Beacon implying climb rate > 8.0 m/s should be rejected."""
        prev_beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 1000,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        curr_beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 1500,  # 500m climb in 10 seconds = 50 m/s (impossible)
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 10),
        }
        
        is_plausible, reason = _is_beacon_plausible(prev_beacon, curr_beacon, "FLR123")
        assert is_plausible is False
        assert "climb" in reason
    
    def test_sink_rate_under_threshold_rejected(self):
        """Beacon implying sink rate < -10.0 m/s should be rejected."""
        prev_beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 2000,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        curr_beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 1000,  # 1000m sink in 10 seconds = -100 m/s (impossible)
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 10),
        }
        
        is_plausible, reason = _is_beacon_plausible(prev_beacon, curr_beacon, "FLR123")
        assert is_plausible is False
        assert "sink" in reason
    
    def test_dt_too_small_rejected(self):
        """Beacon with time delta < 0.5s should be rejected."""
        prev_beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 1500,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        curr_beacon = {
            "latitude": 47.5001,
            "longitude": 13.0001,
            "altitude": 1510,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0, 200000),  # 0.2 seconds
        }
        
        is_plausible, reason = _is_beacon_plausible(prev_beacon, curr_beacon, "FLR123")
        assert is_plausible is False
        assert "dt_too_small" in reason
    
    def test_dt_too_large_rejected(self):
        """Beacon with time delta > 3600s should be rejected."""
        prev_beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 1500,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        curr_beacon = {
            "latitude": 47.5001,
            "longitude": 13.0001,
            "altitude": 1510,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 12, 0, 1),  # 2 hours + 1 second
        }
        
        is_plausible, reason = _is_beacon_plausible(prev_beacon, curr_beacon, "FLR123")
        assert is_plausible is False
        assert "dt_too_large" in reason
    
    def test_valid_beacon_accepted(self):
        """Physically plausible beacon should be accepted."""
        prev_beacon = {
            "latitude": 47.5,
            "longitude": 13.0,
            "altitude": 1500,
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 0, 0),
        }
        curr_beacon = {
            "latitude": 47.51,  # ~1.1km in 60 seconds = ~18 m/s (reasonable glider speed)
            "longitude": 13.01,
            "altitude": 1600,  # 100m climb in 60 seconds = 1.67 m/s (reasonable thermal)
            "reference_timestamp": datetime.datetime(2024, 1, 15, 10, 1, 0),
        }
        
        is_plausible, reason = _is_beacon_plausible(prev_beacon, curr_beacon, "FLR123")
        assert is_plausible is True
        assert reason is None
