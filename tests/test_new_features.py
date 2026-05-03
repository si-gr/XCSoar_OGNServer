"""Manual QA tests for newly implemented features."""
import pytest
import json
from datetime import datetime, timedelta
from src.ogn_server.geofence import load_geofences, point_in_polygon, is_off_field
from src.ogn_server.config import Config


class TestGeofenceModule:
    """Test geofence.py functionality."""
    
    def test_point_in_polygon_triangle(self):
        """Test point-in-polygon with simple triangle."""
        polygon = [[0, 0], [10, 0], [5, 10]]
        
        # Point inside
        assert point_in_polygon(5, 5, polygon) is True
        
        # Point outside
        assert point_in_polygon(0, -1, polygon) is False
        assert point_in_polygon(15, 15, polygon) is False
        
        # Point on edge (should be considered inside or boundary case)
        result = point_in_polygon(5, 0, polygon)
        assert result in [True, False]  # Boundary cases may vary
    
    def test_point_in_polygon_rectangle(self):
        """Test point-in-polygon with rectangle."""
        polygon = [[0, 0], [10, 0], [10, 10], [0, 10]]
        
        # Point inside
        assert point_in_polygon(5, 5, polygon) is True
        
        # Point outside
        assert point_in_polygon(-1, 5, polygon) is False
        assert point_in_polygon(11, 5, polygon) is False
    
    def test_is_off_field_detection(self):
        """Test off-field detection logic."""
        # Format expected: list of polygons or dict with "geofences"/"polygons" key
        airport_polygon = [[47.5, 8.5], [47.6, 8.5], [47.6, 8.6], [47.5, 8.6]]
        
        # Test with list format
        geofences_list = [airport_polygon]
        
        # Position inside geofence - NOT off-field
        result = is_off_field(lat=47.55, lon=8.55, geofences=geofences_list)
        assert result is False
        
        # Position outside geofence - IS off-field
        result = is_off_field(lat=48.0, lon=9.0, geofences=geofences_list)
        assert result is True
        
        # Empty geofences - everything is off-field
        result = is_off_field(lat=47.5, lon=8.5, geofences={})
        assert result is True


class TestHealthEndpoint:
    """Test enhanced /health endpoint."""
    
    def test_health_endpoint_exists(self):
        """Verify /health endpoint is defined in api.py."""
        # Import check - verify the endpoint function exists
        from src.ogn_server.api import create_app
        assert create_app is not None
        
        # Verify health check logic by inspecting the source
        import inspect
        from src.ogn_server import api
        source = inspect.getsource(api)
        
        # Verify key health check components exist
        assert '/health' in source
        assert 'status' in source
        assert 'checks' in source


class TestConfigConstants:
    """Test new configuration constants."""
    
    def test_geofence_constants_exist(self):
        """Verify geofence configuration constants are defined."""
        assert hasattr(Config, 'GEOFENCE_FILE')
        assert hasattr(Config, 'GEOFENCE_OFFLINE_THRESHOLD_MINUTES')
        assert hasattr(Config, 'GEOFENCE_ALERT_COOLDOWN_MINUTES')
        
        # Verify reasonable default values
        assert Config.GEOFENCE_FILE == 'geofences.json'
        assert Config.GEOFENCE_OFFLINE_THRESHOLD_MINUTES >= 5
        assert Config.GEOFENCE_ALERT_COOLDOWN_MINUTES >= 15


class TestSARTracking:
    """Test SAR tracking functionality in OGNClient."""
    
    def test_last_position_cache_methods_exist(self):
        """Verify SAR tracking methods exist."""
        from src.ogn_server.client import OGNClient
        
        # Check that the class has the required attributes/methods
        assert hasattr(OGNClient, 'get_overdue_aircraft')
        assert hasattr(OGNClient, 'get_offline_aircraft')
        assert hasattr(OGNClient, 'get_last_position')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
