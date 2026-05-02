"""
Tests for /loc2igc command date filtering functionality.

These tests verify that:
1. IGC files contain ONLY timestamps from the selected date
2. Dates without data are not shown in selection
3. Proper errors are raised when no matching data exists
"""
import pytest
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


@pytest.fixture
def mock_config(monkeypatch, tmp_path):
    """Mock Config to use temp directory for location files"""
    import os
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.config import Config
        
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        
        monkeypatch.setattr(Config, 'LOCATION_FILE', str(tmp_path / "location.txt"))
        monkeypatch.setattr(Config, 'NAMES_FILE', str(tmp_path / "names.csv"))
        monkeypatch.setattr(Config, 'LOCATION_RETENTION_DAYS', 30)
        
        (tmp_path / "names.csv").write_text("fid,name\nFLR123456,Test Pilot\n")
        
        yield tmp_path
        
        os.chdir(original_cwd)
    except Exception:
        yield tmp_path


def create_location_file(tmp_path: Path, date_str: str, content: str) -> Path:
    """Create a location_{date}.txt file with test data"""
    filename = f"location_{date_str}.txt"
    path = tmp_path / filename
    path.write_text(content)
    return path


def create_location_with_timestamps(tmp_path: Path, date_str: str, flarm_id: str, 
                                    timestamps: list[int]) -> Path:
    """
    Create location file with specific timestamps.
    
    Args:
        tmp_path: Temp directory
        date_str: Date in YYYYMMDD format
        flarm_id: FLARM device ID
        timestamps: List of Unix timestamps (should all be on the same date)
    
    Returns:
        Path to created file
    """
    lines = []
    for ts in timestamps:
        # Format: address,latitude,longitude,track,altitude,ground_speed,climb_rate,timestamp,symbolcode
        lat = 47.5 + (ts % 100) / 10000
        lon = 13.0 + (ts % 100) / 10000
        line = f"{flarm_id},{lat:.5f},{lon:.5f},180,1500,100,2.5,{ts},^"
        lines.append(line)
    
    content = "\n".join(lines)
    return create_location_file(tmp_path, date_str, content)


class TestGenerateIgcDateFiltering:
    """Test that generate_full_igc() filters by selected date"""
    
    def test_igc_contains_only_selected_date(self, mock_config):
        """IGC file should contain ONLY timestamps from the selected date"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.telegram_bot import generate_full_igc
        
        # Create location file with data from TWO different dates
        date1 = "20260427"
        date2 = "20260428"
        
        # Timestamps for April 27, 2026 (Europe/Berlin)
        berlin_tz = ZoneInfo("Europe/Berlin")
        ts_apr27_10am = int(datetime(2026, 4, 27, 10, 0, 0, tzinfo=berlin_tz).timestamp())
        ts_apr27_11am = int(datetime(2026, 4, 27, 11, 0, 0, tzinfo=berlin_tz).timestamp())
        
        # Timestamps for April 28, 2026 (Europe/Berlin)
        ts_apr28_10am = int(datetime(2026, 4, 28, 10, 0, 0, tzinfo=berlin_tz).timestamp())
        ts_apr28_11am = int(datetime(2026, 4, 28, 11, 0, 0, tzinfo=berlin_tz).timestamp())
        
        # Create file with mixed dates
        content = f"""FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr27_10am},^
FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr27_11am},^
FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr28_10am},^
FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr28_11am},^
"""
        create_location_file(mock_config, date1, content)
        
        # Request IGC for April 27 only
        igc_bytes = generate_full_igc("FLR123456", date1, None, {})
        igc_content = igc_bytes.decode('utf-8')
        
        # Count B-records (lines starting with 'B')
        b_records = [line for line in igc_content.split('\n') if line.startswith('B')]
        
        # Should have exactly 2 B-records (the April 27 ones)
        assert len(b_records) == 2, f"Expected 2 B-records, got {len(b_records)}"
        
        # Verify timestamps in B-records are from April 27 (10:00 and 11:00 Berlin time)
        # B-record format: Bhhmmss...
        times = [record[1:7] for record in b_records]  # Extract hhmmss
        assert "100000" in times, "Should include 10:00 timestamp"
        assert "110000" in times, "Should include 11:00 timestamp"
        assert "100000" not in times or times.count("100000") == 1, "Should not have duplicate timestamps"
    
    def test_igc_raises_when_no_matching_timestamps(self, mock_config):
        """Should raise ValueError when no timestamps match the selected date"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.telegram_bot import generate_full_igc
        
        date_requested = "20260428"
        
        berlin_tz = ZoneInfo("Europe/Berlin")
        ts_apr27 = int(datetime(2026, 4, 27, 10, 0, 0, tzinfo=berlin_tz).timestamp())
        
        content = f"FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr27},^\n"
        create_location_file(mock_config, date_requested, content)
        
        with pytest.raises(ValueError, match="No location data found for"):
            generate_full_igc("FLR123456", date_requested, None, {})
    
    def test_igc_handles_mixed_dates_in_single_file(self, mock_config):
        """Should filter correctly when location file has mixed dates"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.telegram_bot import generate_full_igc
        
        # Create location file simulating location.txt with multiple days of data
        date_requested = "20260428"
        
        berlin_tz = ZoneInfo("Europe/Berlin")
        ts_apr27 = int(datetime(2026, 4, 27, 15, 0, 0, tzinfo=berlin_tz).timestamp())
        ts_apr28_1 = int(datetime(2026, 4, 28, 9, 0, 0, tzinfo=berlin_tz).timestamp())
        ts_apr28_2 = int(datetime(2026, 4, 28, 10, 0, 0, tzinfo=berlin_tz).timestamp())
        ts_apr29 = int(datetime(2026, 4, 29, 8, 0, 0, tzinfo=berlin_tz).timestamp())
        
        content = f"""FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr27},^
FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr28_1},^
FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr28_2},^
FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_apr29},^
"""
        # Write to location.txt (fallback file)
        (mock_config / "location.txt").write_text(content)
        
        # Request IGC for April 28
        igc_bytes = generate_full_igc("FLR123456", date_requested, None, {})
        igc_content = igc_bytes.decode('utf-8')
        
        b_records = [line for line in igc_content.split('\n') if line.startswith('B')]
        
        # Should have exactly 2 B-records (the April 28 ones)
        assert len(b_records) == 2, f"Expected 2 B-records, got {len(b_records)}"
        
        # Verify times are 09:00 and 10:00
        times = [record[1:7] for record in b_records]
        assert "090000" in times, "Should include 09:00 timestamp"
        assert "100000" in times, "Should include 10:00 timestamp"


class TestScanLocationFilesDateValidation:
    """Test that scan_location_files() only shows dates with actual data"""
    
    def test_only_shows_dates_with_aircraft_data(self, mock_config):
        """Should only show dates where the specific aircraft has data"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.telegram_bot import scan_location_files
        
        berlin_tz = ZoneInfo("Europe/Berlin")
        
        # Create location files for different dates
        date1 = "20260427"
        date2 = "20260428"
        date3 = "20260429"
        
        # Aircraft 1 has data on all three dates
        create_location_with_timestamps(mock_config, date1, "FLR123456", 
                                        [int(datetime(2026, 4, 27, 10, 0, tzinfo=berlin_tz).timestamp())])
        create_location_with_timestamps(mock_config, date2, "FLR123456",
                                        [int(datetime(2026, 4, 28, 10, 0, tzinfo=berlin_tz).timestamp())])
        create_location_with_timestamps(mock_config, date3, "FLR123456",
                                        [int(datetime(2026, 4, 29, 10, 0, tzinfo=berlin_tz).timestamp())])
        
        # Aircraft 2 only has data on date2
        create_location_with_timestamps(mock_config, date2, "FLR789012",
                                        [int(datetime(2026, 4, 28, 11, 0, tzinfo=berlin_tz).timestamp())])
        
        result = scan_location_files()
        
        # Aircraft 1 should have all three dates
        assert "Test Pilot" in result  # FLR123456 resolves to "Test Pilot" via names.csv
        pilot_dates = result["Test Pilot"]
        assert "FLR123456" in pilot_dates
        assert set(pilot_dates["FLR123456"]) == {date1, date2, date3}
        
        # Aircraft 2 should only have date2
        assert "FLR789012" in result
        flr789_dates = result["FLR789012"]
        assert "FLR789012" in flr789_dates
        assert set(flr789_dates["FLR789012"]) == {date2}
    
    def test_excludes_dates_outside_retention_period(self, mock_config):
        """Should exclude dates outside LOCATION_RETENTION_DAYS"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.telegram_bot import scan_location_files
        from ogn_server.config import Config
        from datetime import timedelta
        
        berlin_tz = ZoneInfo("Europe/Berlin")
        now = datetime.now(berlin_tz)
        
        # Create file with old date (outside retention period)
        old_date = (now - timedelta(days=Config.LOCATION_RETENTION_DAYS + 1)).strftime("%Y%m%d")
        old_ts = int((now - timedelta(days=Config.LOCATION_RETENTION_DAYS + 1)).timestamp())
        
        create_location_with_timestamps(mock_config, old_date, "FLR123456", [old_ts])
        
        result = scan_location_files()
        
        # Old date should not appear
        if "Test Pilot" in result:
            assert old_date not in result["Test Pilot"].get("FLR123456", [])


class TestEdgeCases:
    """Test edge cases in date filtering"""
    
    def test_handles_malformed_timestamps(self, mock_config):
        """Should skip rows with malformed timestamps"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.telegram_bot import generate_full_igc
        
        date_str = "20260428"
        berlin_tz = ZoneInfo("Europe/Berlin")
        valid_ts = int(datetime(2026, 4, 28, 10, 0, 0, tzinfo=berlin_tz).timestamp())
        
        # Mix of valid and invalid timestamps
        content = f"""FLR123456,47.50000,13.00000,180,1500,100,2.5,{valid_ts},^
FLR123456,47.50000,13.00000,180,1500,100,2.5,invalid_timestamp,^
FLR123456,47.50000,13.00000,180,1500,100,2.5,,^
FLR123456,47.50000,13.00000,180,1500,100,2.5,not_a_number,^
"""
        create_location_file(mock_config, date_str, content)
        
        # Should succeed with only the valid timestamp
        igc_bytes = generate_full_igc("FLR123456", date_str, None, {})
        igc_content = igc_bytes.decode('utf-8')
        
        b_records = [line for line in igc_content.split('\n') if line.startswith('B')]
        assert len(b_records) == 1, "Should only include row with valid timestamp"
    
    def test_handles_empty_location_file(self, mock_config):
        """Should raise error when location file is empty"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.telegram_bot import generate_full_igc
        
        date_str = "20260428"
        create_location_file(mock_config, date_str, "")
        
        with pytest.raises(ValueError, match="No location data found"):
            generate_full_igc("FLR123456", date_str, None, {})
    
    def test_handles_timezone_conversion_correctly(self, mock_config):
        """Should handle UTC to Europe/Berlin timezone conversion correctly"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ogn_server.telegram_bot import generate_full_igc
        
        date_str = "20260428"
        
        # Timestamp for April 28, 2026 22:00 UTC = April 29, 2026 00:00 Berlin (DST)
        # This should NOT appear in April 28 IGC
        utc_tz = timezone.utc
        ts_utc_end_of_day = int(datetime(2026, 4, 28, 22, 0, 0, tzinfo=utc_tz).timestamp())
        
        # Timestamp for April 28, 2026 20:00 UTC = April 28, 2026 22:00 Berlin
        # This SHOULD appear in April 28 IGC
        ts_utc_evening = int(datetime(2026, 4, 28, 20, 0, 0, tzinfo=utc_tz).timestamp())
        
        content = f"""FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_utc_end_of_day},^
FLR123456,47.50000,13.00000,180,1500,100,2.5,{ts_utc_evening},^
"""
        create_location_file(mock_config, date_str, content)
        
        igc_bytes = generate_full_igc("FLR123456", date_str, None, {})
        igc_content = igc_bytes.decode('utf-8')
        
        b_records = [line for line in igc_content.split('\n') if line.startswith('B')]
        
        # Only the evening UTC timestamp should appear (converts to Berlin April 28)
        # The late UTC timestamp converts to Berlin April 29, so should be filtered out
        assert len(b_records) == 1, f"Expected 1 B-record (timezone filtered), got {len(b_records)}"
