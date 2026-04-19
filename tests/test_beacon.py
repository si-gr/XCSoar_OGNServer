import pytest
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.ogn_server.beacon import Beacon


class TestBeacon:
    def test_beacon_creation(self):
        ts = datetime.now()
        beacon = Beacon(
            address="FLR123456",
            name="TEST",
            latitude=47.5,
            longitude=13.0,
            track=180.0,
            altitude=1500.0,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=ts,
            beacon_type="^"
        )
        
        assert beacon.address == "FLR123456"
        assert beacon.name == "TEST"
        assert beacon.latitude == 47.5
        assert beacon.longitude == 13.0
        assert beacon.track == 180.0
        assert beacon.altitude == 1500.0
        assert beacon.ground_speed == 100.0
        assert beacon.climb_rate == 2.5
        assert beacon.reference_timestamp == ts
        assert beacon.beacon_type == "^"
    
    def test_beacon_name_truncation(self):
        ts = datetime.now()
        beacon = Beacon(
            address="FLR123456",
            name="LONGNAME",
            latitude=47.5,
            longitude=13.0,
            track=180.0,
            altitude=1500.0,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=ts,
            beacon_type="^"
        )
        
        assert beacon.name == "LONGNAME"
    
    def test_beacon_equality(self):
        ts = datetime.now()
        beacon1 = Beacon(
            address="FLR123456",
            name="TEST",
            latitude=47.5,
            longitude=13.0,
            track=180.0,
            altitude=1500.0,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=ts,
            beacon_type="^"
        )
        beacon2 = Beacon(
            address="FLR123456",
            name="OTHER",
            latitude=48.0,
            longitude=14.0,
            track=90.0,
            altitude=2000.0,
            ground_speed=150.0,
            climb_rate=3.0,
            reference_timestamp=ts,
            beacon_type=">"
        )
        
        assert beacon1 == beacon2
    
    def test_beacon_inequality(self):
        ts = datetime.now()
        beacon1 = Beacon(
            address="FLR123456",
            name="TEST",
            latitude=47.5,
            longitude=13.0,
            track=180.0,
            altitude=1500.0,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=ts,
            beacon_type="^"
        )
        beacon2 = Beacon(
            address="FLR999999",
            name="OTHER",
            latitude=48.0,
            longitude=14.0,
            track=90.0,
            altitude=2000.0,
            ground_speed=150.0,
            climb_rate=3.0,
            reference_timestamp=ts,
            beacon_type=">"
        )
        
        assert beacon1 != beacon2
    
    def test_to_csv_row_with_nickname(self):
        ts = datetime(2024, 1, 15, 10, 30, 45)
        beacon = Beacon(
            address="FLR123456",
            name="TEST",
            latitude=47.512345,
            longitude=13.012345,
            track=180.5,
            altitude=1500.7,
            ground_speed=100.3,
            climb_rate=2.55,
            reference_timestamp=ts,
            beacon_type="^"
        )
        
        result = beacon.to_csv_row("NICK")
        
        assert "NICK," in result
        assert "47.51234," in result
        assert "13.01234," in result
        assert "180.5," in result
        assert "1501," in result
        assert "100," in result
        assert "2.5," in result
        assert "^\n" in result
    
    def test_to_csv_row_without_nickname(self):
        ts = datetime(2024, 1, 15, 10, 30, 45)
        beacon = Beacon(
            address="FLR123456",
            name="TEST",
            latitude=47.5,
            longitude=13.0,
            track=180.0,
            altitude=1500.0,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=ts,
            beacon_type="^"
        )
        
        result = beacon.to_csv_row()
        
        assert result.startswith("TEST,")
    
    def test_to_csv_row_with_empty_nickname(self):
        ts = datetime(2024, 1, 15, 10, 30, 45)
        beacon = Beacon(
            address="FLR123456",
            name="TEST",
            latitude=47.5,
            longitude=13.0,
            track=180.0,
            altitude=1500.0,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=ts,
            beacon_type="^"
        )
        
        result = beacon.to_csv_row("")
        
        assert result.startswith("TEST,")
    
    def test_to_csv_row_with_avg_climb(self):
        ts = datetime(2024, 1, 15, 10, 30, 45)
        beacon = Beacon(
            address="FLR123456",
            name="TEST",
            latitude=47.5,
            longitude=13.0,
            track=180.0,
            altitude=1500.0,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=ts,
            beacon_type="^"
        )
        
        result = beacon.to_csv_row("NICK", avg_climb=1.5)
        
        assert "NICK," in result
        assert "1.5\n" in result
        fields = result.rstrip('\n').split(',')
        assert len(fields) == 10
    
    def test_to_csv_row_without_avg_climb(self):
        ts = datetime(2024, 1, 15, 10, 30, 45)
        beacon = Beacon(
            address="FLR123 spTimeout456",
            name="TEST",
            latitude=47.5,
            longitude=13.0,
            track=180.0,
            altitude=1500.0,
            ground_speed=100.0,
            climb_rate=2.5,
            reference_timestamp=ts,
            beacon_type="^"
        )
        
        result = beacon.to_csv_row("NICK")
        
        assert "NICK," in result
        fields = result.rstrip('\n').split(',')
        assert len(fields) == 9
