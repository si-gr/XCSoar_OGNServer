import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestAPI:
    @pytest.fixture
    def mock_client(self):
        with patch('src.ogn_server.api.ogn_client') as mock:
            yield mock
    
    def test_create_app(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        mock_client.get_messages_in_bounds.return_value = "5,5\ndata"
        
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/?access_token=token&bounds=47.0,48.0,12.0,14.0")
        
        assert response.status_code == 200
    
    def test_api_requires_access_token(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/")
        
        assert response.data == b""
    
    def test_api_rejects_invalid_token(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/?access_token=wrongtoken")
        
        assert response.data == b""
    
    def test_api_requires_bounds_parameter(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/?access_token=token")
        
        assert response.data == b""
    
    def test_api_validates_bounds_count(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/?access_token=token&bounds=47.0,48.0")
        
        assert response.data == b""
    
    def test_api_returns_filtered_messages(self):
        from src.ogn_server.api import create_app
        
        mock_client = MagicMock()
        expected_data = "3,3\nFLR123,47.5,13.0,180,1500,100,2.5,1705312245,^"
        mock_client.get_messages_in_bounds.return_value = expected_data
        
        app = create_app(mock_client, ["token", "0.0.0.0", "47.5", "13.0"])
        
        client = app.test_client()
        response = client.get("/?access_token=token&bounds=47.0,48.0,12.0,14.0")
        
        assert response.status_code == 200
        mock_client.get_messages_in_bounds.assert_called_once_with(["47.0", "48.0", "12.0", "14.0"])
