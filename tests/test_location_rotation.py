import os
import sys
import time
import datetime
from pathlib import Path
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.ogn_server.config import Config
from src.ogn_server.client import OGNClient


class TestLocationMigration:
    """Test _migrate_location_file() method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_serverdata = ["token", "host", "47.5", "13.0"]
        
        # Clean up any existing location files before each test
        self._cleanup_test_files()
    
    def teardown_method(self):
        """Clean up after each test."""
        self._cleanup_test_files()
    
    def _cleanup_test_files(self):
        """Remove all location files created during tests."""
        for f in Path(".").glob("location_*.txt"):
            f.unlink(missing_ok=True)
        if Path("location.txt").exists():
            Path("location.txt").unlink(missing_ok=True)
    
    def test_migrate_skips_when_file_does_not_exist(self):
        """Should do nothing when location.txt doesn't exist."""
        assert not Path("location.txt").exists()
        
        client = OGNClient(self.test_serverdata)
        client._migrate_location_file()
        
        # Should still not exist
        assert not Path("location.txt").exists()
    
    def test_migrate_skips_empty_file(self):
        """Empty location.txt should not be migrated."""
        Path("location.txt").touch()
        assert Path("location.txt").stat().st_size == 0
        
        client = OGNClient(self.test_serverdata)
        client._migrate_location_file()
        
        # File should still exist (not renamed, not deleted)
        assert Path("location.txt").exists()
    
    def test_migrate_deletes_file_older_than_retention(self):
        """location.txt older than LOCATION_RETENTION_DAYS should be deleted."""
        # Create file with content
        Path("location.txt").write_text("test,data\n")
        
        # Set modification time to 3 days ago (beyond 2-day retention)
        old_mtime = time.time() - (Config.LOCATION_RETENTION_DAYS * 86400 * 1.5)
        os.utime("location.txt", (old_mtime, old_mtime))
        
        client = OGNClient(self.test_serverdata)
        client._migrate_location_file()
        
        # File should be deleted
        assert not Path("location.txt").exists()
        # No rotated files should exist
        dated_files = list(Path(".").glob("location_*.txt"))
        assert len(dated_files) == 0
    
    def test_migrate_renames_recent_file_to_dated_name(self):
        """location.txt with recent mtime should be renamed to location_YYYYMMDD.txt."""
        # Create file with content
        Path("location.txt").write_text("test,data\n")
        
        # Set modification time to yesterday (within retention)
        recent_mtime = time.time() - 86400  # 1 day ago
        os.utime("location.txt", (recent_mtime, recent_mtime))
        
        expected_date = time.strftime("%Y%m%d", time.localtime(recent_mtime))
        expected_rotated_name = f"location_{expected_date}.txt"
        
        client = OGNClient(self.test_serverdata)
        client._migrate_location_file()
        
        # Original file should be gone
        assert not Path("location.txt").exists()
        # Rotated file should exist
        assert Path(expected_rotated_name).exists()
        # Content should be preserved
        assert Path(expected_rotated_name).read_text() == "test,data\n"
    
    def test_migrate_skips_if_rotated_name_already_exists(self):
        """Should not overwrite existing rotated file."""
        # Create original location.txt
        Path("location.txt").write_text("new data\n")
        
        # Create already-existing rotated file
        old_date = time.strftime("%Y%m%d")
        rotated_name = f"location_{old_date}.txt"
        Path(rotated_name).write_text("old data\n")
        
        client = OGNClient(self.test_serverdata)
        client._migrate_location_file()
        
        # Original should still exist (not renamed)
        assert Path("location.txt").exists()
        # Rotated file unchanged
        assert Path(rotated_name).read_text() == "old data\n"


class TestLocationCleanup:
    """Test _cleanup_old_location_files() method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_serverdata = ["token", "host", "47.5", "13.0"]
        
        # Clean up any existing location files before each test
        self._cleanup_test_files()
    
    def teardown_method(self):
        """Clean up after each test."""
        self._cleanup_test_files()
    
    def _cleanup_test_files(self):
        """Remove all location files created during tests."""
        for f in Path(".").glob("location_*.txt"):
            f.unlink(missing_ok=True)
        if Path("location.txt").exists():
            Path("location.txt").unlink(missing_ok=True)
    
    def test_cleanup_removes_old_location_files(self):
        """Files older than retention should be deleted."""
        # Create old file
        old_file = Path("location_20260101.txt")
        old_file.write_text("old data\n")
        
        # Set modification time to 5 days ago (beyond 2-day retention)
        old_mtime = time.time() - (Config.LOCATION_RETENTION_DAYS * 86400 * 2.5)
        os.utime(old_file, (old_mtime, old_mtime))
        
        # Create recent rotated file (will act as "current" after migration)
        today = time.strftime("%Y%m%d")
        current_dated = Path(f"location_{today}.txt")
        current_dated.write_text("current\n")
        
        client = OGNClient(self.test_serverdata)
        client._cleanup_old_location_files()
        
        # Old file should be deleted
        assert not old_file.exists()
        # Dated file should remain
        assert current_dated.exists()
    
    def test_cleanup_keeps_recent_location_files(self):
        """Files newer than retention should be kept."""
        # Create recent file (today)
        today = time.strftime("%Y%m%d")
        recent_file = Path(f"location_{today}.txt")
        recent_file.write_text("recent data\n")
        
        # Create current location.txt
        Path("location.txt").write_text("current\n")
        
        client = OGNClient(self.test_serverdata)
        client._cleanup_old_location_files()
        
        # Recent file should remain
        assert recent_file.exists()
        # Current file should remain
        assert Path("location.txt").exists()
    
    def test_cleanup_preserves_current_location_txt(self):
        """Current location.txt is migrated on startup, then cleanup runs."""
        # After migration in __init__, location.txt becomes location_YYYYMMDD.txt
        # This test verifies cleanup doesn't delete the dated file
        
        today = time.strftime("%Y%m%d")
        dated_file = Path(f"location_{today}.txt")
        dated_file.write_text("current data\n")
        
        client = OGNClient(self.test_serverdata)
        client._cleanup_old_location_files()
        
        # Dated file must NOT be deleted (within retention)
        assert dated_file.exists()
        assert dated_file.read_text() == "current data\n"
    
    def test_cleanup_handles_multiple_files(self):
        """Should correctly handle mix of old and new files."""
        # Create old file
        old_file = Path("location_20260101.txt")
        old_file.write_text("old\n")
        old_mtime = time.time() - (Config.LOCATION_RETENTION_DAYS * 86400 * 3)
        os.utime(old_file, (old_mtime, old_mtime))
        
        # Create recent file (yesterday)
        yesterday = time.strftime("%Y%m%d", time.localtime(time.time() - 86400))
        recent_file = Path(f"location_{yesterday}.txt")
        recent_file.write_text("recent\n")
        recent_mtime = time.time() - 86400
        os.utime(recent_file, (recent_mtime, recent_mtime))
        
        # Create another old file
        very_old_file = Path("location_20250101.txt")
        very_old_file.write_text("very old\n")
        very_old_mtime = time.time() - (365 * 86400)
        os.utime(very_old_file, (very_old_mtime, very_old_mtime))
        
        client = OGNClient(self.test_serverdata)
        client._cleanup_old_location_files()
        
        # Old files should be deleted
        assert not old_file.exists()
        assert not very_old_file.exists()
        # Recent file should remain
        assert recent_file.exists()


class TestLocationIntegration:
    """Integration tests for location file rotation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_serverdata = ["token", "host", "47.5", "13.0"]
        self._cleanup_test_files()
    
    def teardown_method(self):
        """Clean up after each test."""
        self._cleanup_test_files()
    
    def _cleanup_test_files(self):
        """Remove all location files created during tests."""
        for f in Path(".").glob("location_*.txt"):
            f.unlink(missing_ok=True)
        if Path("location.txt").exists():
            Path("location.txt").unlink(missing_ok=True)
    
    def test_migration_then_write_creates_new_file(self):
        """After migration, writing should create fresh location.txt."""
        # Setup: old file that will be deleted
        Path("location.txt").write_text("old data\n")
        old_mtime = time.time() - (Config.LOCATION_RETENTION_DAYS * 86400 * 2)
        os.utime("location.txt", (old_mtime, old_mtime))
        
        # Create client (migration runs in __init__)
        client = OGNClient(self.test_serverdata)
        
        # Old file should be deleted
        assert not Path("location.txt").exists()
        
        # Write new beacon data (simulate)
        test_beacon = {
            "address": "FLR123456",
            "latitude": 47.5,
            "longitude": 13.0,
            "track": 180,
            "altitude": 1500,
            "ground_speed": 100,
            "climb_rate": 2.5,
            "reference_timestamp": int(time.time()),
            "symbolcode": "^"
        }
        client._write_location(test_beacon)
        
        # New file should exist
        assert Path("location.txt").exists()
        assert Path("location.txt").stat().st_size > 0


class TestDateRotation:
    """Test _rotate_location_file_if_needed() method - oldest entry based rotation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_serverdata = ["token", "host", "47.5", "13.0"]
        self._cleanup_test_files()
    
    def teardown_method(self):
        """Clean up after each test."""
        self._cleanup_test_files()
    
    def _cleanup_test_files(self):
        """Remove all location files created during tests."""
        for f in Path(".").glob("location_*.txt"):
            f.unlink(missing_ok=True)
        if Path("location.txt").exists():
            Path("location.txt").unlink(missing_ok=True)
    
    def test_rotate_skips_when_no_file_exists(self):
        """Should do nothing when location.txt doesn't exist."""
        client = OGNClient(self.test_serverdata)
        client._rotate_location_file_if_needed()
        
        assert not Path("location.txt").exists()
        dated_files = list(Path(".").glob("location_*.txt"))
        assert len(dated_files) == 0
    
    def test_rotate_skips_empty_file(self):
        """Empty location.txt should not be rotated."""
        Path("location.txt").touch()
        
        client = OGNClient(self.test_serverdata)
        client._rotate_location_file_if_needed()
        
        assert Path("location.txt").exists()
        assert Path("location.txt").stat().st_size == 0
    
    def test_rotate_skips_only_today_entries(self):
        """Should not rotate if all entries are from today."""
        client = OGNClient(self.test_serverdata)
        
        today_ts = int(time.time())
        content = f"FLR123456,47.5,13.0,180,1500,100,2.5,{today_ts},^\n"
        Path("location.txt").write_text(content)
        
        client._rotate_location_file_if_needed()
        
        assert Path("location.txt").exists()
        assert Path("location.txt").read_text().strip() == content.strip()
    
    def test_rotate_with_yesterday_entries(self):
        """Should rotate when oldest entry is from yesterday."""
        client = OGNClient(self.test_serverdata)
        
        yesterday_ts = int(time.time()) - 86400
        content = f"FLR123456,47.5,13.0,180,1500,100,2.5,{yesterday_ts},^\n"
        Path("location.txt").write_text(content)
        
        client._rotate_location_file_if_needed()
        
        dated_files = list(Path(".").glob("location_*.txt"))
        assert len(dated_files) >= 1
    
    def test_rotate_filters_by_date_content(self):
        """Entries should be filtered by timestamp date."""
        client = OGNClient(self.test_serverdata)
        
        today_ts = int(time.time())
        yesterday_ts = int(time.time()) - 86400
        
        content = (
            f"FLR111111,47.5,13.0,180,1500,100,2.5,{today_ts},^\n"
            f"FLR222222,47.6,13.1,90,1600,120,1.5,{yesterday_ts},>\n"
        )
        Path("location.txt").write_text(content)
        
        client._rotate_location_file_if_needed()
        
        assert Path("location.txt").exists()
        loc_content = Path("location.txt").read_text()
        assert "FLR111111" in loc_content
        assert "FLR222222" not in loc_content
    
    def test_rotate_handles_collision_by_appending(self):
        """If dated archive exists, should append rather than overwrite."""
        client = OGNClient(self.test_serverdata)
        
        yesterday_ts = int(time.time()) - 86400
        yesterday_date = datetime.date.fromtimestamp(yesterday_ts).strftime("%Y%m%d")
        rotated_name = f"location_{yesterday_date}.txt"
        
        Path(rotated_name).write_text("existing,data\n")
        Path("location.txt").write_text(f"FLR123,47.5,13.0,180,1500,100,2.5,{yesterday_ts},^\n")
        
        client._rotate_location_file_if_needed()
        
        assert Path(rotated_name).exists()
        content = Path(rotated_name).read_text()
        assert "existing,data" in content
        assert "FLR123" in content


class TestDateRotationIntegration:
    """Integration tests for date-based rotation with full workflow."""

    def setup_method(self):
        """Set up test fixtures."""
        self.test_serverdata = ["token", "host", "47.5", "13.0"]
        self._cleanup_test_files()
    
    def teardown_method(self):
        """Clean up after each test."""
        self._cleanup_test_files()
    
    def _cleanup_test_files(self):
        """Remove all location files created during tests."""
        for f in Path(".").glob("location_*.txt"):
            f.unlink(missing_ok=True)
        if Path("location.txt").exists():
            Path("location.txt").unlink(missing_ok=True)
    
    def test_write_triggers_rotation_on_old_entries(self):
        """Writing beacon should trigger rotation if existing entries are old."""
        yesterday_ts = int(time.time()) - 86400
        content = f"FLR123456,47.5,13.0,180,1500,100,2.5,{yesterday_ts},^\n"
        Path("location.txt").write_text(content)
        
        client = OGNClient(self.test_serverdata)
        
        today_ts = int(time.time())
        test_beacon = {
            "address": "FLR789012",
            "latitude": 47.6,
            "longitude": 13.1,
            "track": 90,
            "altitude": 1600,
            "ground_speed": 120,
            "climb_rate": 1.5,
            "reference_timestamp": today_ts,
            "symbolcode": ">"
        }
        client._write_location(test_beacon)
        
        dated_files = list(Path(".").glob("location_*.txt"))
        assert len(dated_files) >= 1
        
        assert Path("location.txt").exists()
        loc_content = Path("location.txt").read_text()
        assert "FLR789012" in loc_content
    
    def test_rotation_check_interval_respected(self):
        """Rotation check should only happen every 15 minutes."""
        from src.ogn_server.config import Config
        
        client = OGNClient(self.test_serverdata)
        
        yesterday_ts = int(time.time()) - 86400
        content = f"FLR123456,47.5,13.0,180,1500,100,2.5,{yesterday_ts},^\n"
        Path("location.txt").write_text(content)
        
        initial_check_time = client._last_rotation_check_time
        
        client._rotate_location_file_if_needed()
        first_check_time = client._last_rotation_check_time
        
        assert first_check_time > initial_check_time
        
        client._rotate_location_file_if_needed()
        second_check_time = client._last_rotation_check_time
        
        assert second_check_time == first_check_time
        
        client._last_rotation_check_time = time.time() - (Config.LOCATION_ROTATION_CHECK_INTERVAL_SECONDS + 10)
        client._rotate_location_file_if_needed()
        third_check_time = client._last_rotation_check_time
        
        assert third_check_time > second_check_time
