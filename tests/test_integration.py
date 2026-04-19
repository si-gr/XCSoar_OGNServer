import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.ogn_server.beacon import Beacon


class TestIntegration:
    def test_client_and_api_integration(self):
        from src.ogn_server.api import create_app
        from src.ogn_server.client import OGNClient
        from src.ogn_server.beacon import Beacon
        import datetime
        
        mock_client = MagicMock(spec=OGNClient)
        
        test_beacon = Beacon(
            address="FLR12345",
            name="FLR12345",
            latitude=47.5,
            longitude=13.0,
            track=180,
            altitude=1500,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=datetime.datetime.now(),
            beacon_type="^"
        )
        
        mock_client.get_messages_in_bounds.return_value = f"1,1\nJohn Doe,47.5,13.0,180,1500,100,2.5,1705312245,^"
        
        app = create_app(mock_client, ["test_token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        
        response = client.get("/?access_token=test_token&bounds=47.0,48.0,12.0,14.0")
        
        assert response.status_code == 200
        assert b"John Doe" in response.data

    def test_health_endpoint_integration(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/health")
        
        assert response.status_code == 200
        assert b"healthy" in response.data

    def test_metrics_endpoint_integration(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/metrics")
        
        assert response.status_code == 200

    def test_invalid_token_rejected_integration(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        app = create_app(mock_client, ["valid_token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/?access_token=invalid_token&bounds=47.0,48.0,12.0,14.0")
        
        assert response.data == b""

    def test_beacon_cleanup_integration(self):
        from src.ogn_server.api import create_app
        from src.ogn_server.client import OGNClient
        import datetime
        
        test_beacons = [
            Beacon(
                address="FLR11111",
                name="FLR11111",
                latitude=47.5,
                longitude=13.0,
                track=180,
                altitude=1500,
                ground_speed=100.0,
                climb_rate=2.5,
                reference_timestamp=datetime.datetime.now(),
                beacon_type="^"
            ),
            Beacon(
                address="FLR22222",
                name="FLR22222",
                latitude=47.6,
                longitude=13.1,
                track=90,
                altitude=1600,
                ground_speed=80.0,
                climb_rate=-1.0,
                reference_timestamp=datetime.datetime.now() - datetime.timedelta(hours=2),
                beacon_type=">"
            )
        ]
        
        mock_client = MagicMock()
        mock_client.get_messages_in_bounds.return_value = "1,1\nFLR11111,47.5,13.0,180,1500,100,2.5,1705312245,^"
        
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        
        response = client.get("/?access_token=token&bounds=47.0,48.0,12.0,14.0")
        
        assert response.status_code == 200

class TestOGNClientClimbHistory:
    def test_update_climb_history_adds_entry(self):
        from src.ogn_server.client import OGNClient
        import datetime
        
        mock_serverdata = ["token", "0.0.0.0", "47.5", "13.FeedbackFreshFeedback feedback fresh touch feedback Fresh fresh feedback feedback fresh
