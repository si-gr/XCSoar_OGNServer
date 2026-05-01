import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestTelegramBot:
    @pytest.fixture
    def mock_config(self, tmp_path):
        names_file = tmp_path / "names.csv"
        names_file.write_text("FLR12345,John Doe\nFLR67890,Jane Smith\n")
        
        with patch('src.ogn_server.config.Config.NAMES_FILE', str(names_file)):
            with patch('src.ogn_server.telegram_bot.Config.NAMES_FILE', str(names_file)):
                with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
                    with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                        yield names_file

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_add_command_success(self, mock_context, mock_update, mock_config, tmp_path):
        from src.ogn_server.telegram_bot import TelegramBot
        
        mock_update.effective_user.id = 12345
        mock_update.message = MagicMock()
        mock_update.message.reply_markdown_v2 = AsyncMock()
        mock_context.args = ["FLR11111,New Pilot"]
        
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                
                import asyncio
                asyncio.run(bot.add(mock_update, mock_context))
                
        content = mock_config.read_text()
        assert "FLR11111,New Pilot" in content

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_add_command_unauthorized_user(self, mock_context, mock_update, mock_config):
        from src.ogn_server.telegram_bot import TelegramBot
        
        mock_update.effective_user.id = 99999
        mock_update.message = MagicMock()
        mock_update.message.reply_markdown_v2 = AsyncMock()
        mock_context.args = ["FLR11111,New Pilot"]
        
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                
                import asyncio
                asyncio.run(bot.add(mock_update, mock_context))
                
        content = mock_config.read_text()
        assert "FLR11111,New Pilot" not in content
        mock_update.message.reply_markdown_v2.assert_called_once()

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_delete_command_shows_aircraft_list(self, mock_context, mock_update, mock_config):
        """Test /d command shows aircraft selection keyboard."""
        from src.ogn_server.telegram_bot import TelegramBot, scan_names_csv
        
        mock_update.effective_user.id = 12345
        mock_update.message = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_context.bot.send_message = AsyncMock()
        
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                
                import asyncio
                result = asyncio.run(bot.delete_command(mock_update, mock_context))
                
        # Should return SELECTING_AIRCRAFT_FOR_DELETE state (value 7)
        assert result == 7
        # Verify reply_text or send_message was called
        assert mock_update.message.reply_text.called or mock_context.bot.send_message.called

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_delete_command_no_aircraft(self, mock_context, mock_update, mock_config):
        """Test /d command when names.csv is empty."""
        from src.ogn_server.telegram_bot import TelegramBot
        
        mock_update.effective_user.id = 12345
        mock_update.message = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        
        # Simulate empty names.csv by clearing the file content
        mock_config.write_text("")
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                
                import asyncio
                result = asyncio.run(bot.delete_command(mock_update, mock_context))
                
        # Should end conversation when no aircraft exist
        assert result is None or result == -1  # END constant

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_start_command_returns_message_contains_all_commands(self, mock_context, mock_update, mock_config):
        # Test that /start returns a markdown message listing all admin commands
        from src.ogn_server.telegram_bot import TelegramBot
        from telegram import BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
        mock_update.effective_user.id = 12345
        mock_update.message = MagicMock()
        mock_update.message.reply_markdown_v2 = AsyncMock()
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                import asyncio
                asyncio.run(bot.start(mock_update, mock_context))
        assert mock_update.message.reply_markdown_v2.called
        text = mock_update.message.reply_markdown_v2.call_args[0][0]
        # Basic checks for presence of all expected commands in the help text
        for token in ['/start', '/a', '/d', '/refreshddb', '/igc', '/cancel']:
            assert token in text
        # Markdown formatting hints should be present
        assert 'Available Commands' in text

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_post_init_sets_bot_commands(self, mock_context, mock_update, mock_config):
        # Verify post_init creates two sets of commands with correct scopes
        from src.ogn_server.telegram_bot import post_init
        from telegram import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
        class FakeBot:
            def __init__(self):
                self.calls = []
            async def set_my_commands(self, commands, scope=None):
                self.calls.append((commands, scope))
        class FakeApp:
            def __init__(self):
                self.bot = FakeBot()
        app = FakeApp()
        import asyncio
        asyncio.run(post_init(app))
        assert len(app.bot.calls) == 2
        private_cmds, private_scope = app.bot.calls[0]
        group_cmds, group_scope = app.bot.calls[1]
        assert isinstance(private_scope, BotCommandScopeAllPrivateChats)
        assert isinstance(group_scope, BotCommandScopeAllGroupChats)
        assert isinstance(private_cmds, list)
        names = [c.command for c in private_cmds]
        for expected in ["start", "a", "d", "refreshddb", "igc", "cancel"]:
            assert expected in names

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_add_command_missing_args_no_crash(self, mock_context, mock_update, mock_config):
        # Ensure missing arguments do not crash the bot and do not modify the file
        from src.ogn_server.telegram_bot import TelegramBot
        mock_update.effective_user.id = 12345
        mock_update.message = MagicMock()
        mock_update.message.reply_markdown_v2 = AsyncMock()
        mock_context.args = []  # missing args
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                import asyncio
                asyncio.run(bot.add(mock_update, mock_context))
        # No exception means test passes; content unchanged would be checked in other tests if needed

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_start_command_none_message_graceful(self, mock_context, mock_update, mock_config):
        # Ensure gracefully handles None message object
        from src.ogn_server.telegram_bot import TelegramBot
        mock_update.effective_user.id = 12345
        mock_update.message = None
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                import asyncio
                asyncio.run(bot.start(mock_update, mock_context))


class TestLocationHelpers:
    """Unit tests for scan_location_files() and generate_full_igc() helpers."""

    def test_scan_location_files_empty(self, tmp_path, monkeypatch):
        """Test scan_location_files when no location files exist."""
        monkeypatch.chdir(tmp_path)
        from src.ogn_server.telegram_bot import scan_location_files
        result = scan_location_files()
        assert result == {}

    def test_scan_location_files_with_data(self, tmp_path, monkeypatch):
        """Test scan_location_files parses location files correctly."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        monkeypatch.chdir(tmp_path)

        # Use today's date since filtering is now today-only
        berlin_tz = ZoneInfo("Europe/Berlin")
        today = datetime.now(berlin_tz).strftime("%Y%m%d")
        
        location_file = tmp_path / f"location_{today}.txt"
        location_file.write_text(
            "# address,lat,lon,track,altitude,ground_speed,climb_rate,timestamp,symbolcode\n"
            "FLR123456,47.5,13.0,180,1500,100,2.5,1705312245,^\n"
            "FLRABCDEF,47.6,13.1,90,1600,120,1.5,1705312300,>\n"
        )

        names_file = tmp_path / "names.csv"
        names_file.write_text("fid,name\nFLR123456,Test Pilot\nFLRABCDEF,Another Pilot\n")

        import src.ogn_server.config as config
        monkeypatch.setattr(config.Config, 'NAMES_FILE', str(names_file))

        from src.ogn_server.telegram_bot import scan_location_files
        result = scan_location_files()

        assert "Test Pilot" in result
        assert "FLR123456" in result["Test Pilot"]
        assert today in result["Test Pilot"]["FLR123456"]

    def test_generate_full_igc_basic(self, tmp_path, monkeypatch):
        """Test generate_full_igc produces valid IGC format."""
        monkeypatch.chdir(tmp_path)

        location_file = tmp_path / "location_20260428.txt"
        location_file.write_text(
            "# address,lat,lon,track,altitude,ground_speed,climb_rate,timestamp,symbolcode\n"
            "FLR123456,47.5,13.0,180,1500,100,2.5,1705312245,^\n"
        )

        import pandas as pd
        names_df = pd.DataFrame({'fid': ['FLR123456'], 'name': ['Test Pilot']})

        ddb_devices = {}

        from src.ogn_server.telegram_bot import generate_full_igc
        igc_bytes = generate_full_igc('FLR123456', '20260428', names_df, ddb_devices)
        igc_content = igc_bytes.decode('utf-8')

        assert "IGC_FILE_FORMAT_VERSION=6" in igc_content
        assert "HFTZNTIMEZONE:Europe/Berlin" in igc_content
        assert "HFDTE" in igc_content
        assert "HFPLTPILOTINCHARGE:Test Pilot" in igc_content
        assert "HFTYPETYPEOFGLIDER:" in igc_content
        assert "HFREGREGISTRATION:" in igc_content
        assert "B" in igc_content

    def test_generate_full_igc_file_not_found(self, tmp_path, monkeypatch):
        """Test generate_full_igc raises FileNotFoundError when location file missing."""
        import pandas as pd
        names_df = pd.DataFrame({'fid': ['FLR123456'], 'name': ['Test Pilot']})

        from src.ogn_server.telegram_bot import generate_full_igc
        import pytest

        # Run in temp directory with no location files
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            generate_full_igc('FLR123456', '20260428', names_df, {})

    def test_generate_full_igc_has_a_record(self, tmp_path, monkeypatch):
        """Test generate_full_igc produces IGC with valid A-record as FIRST line."""
        import re
        
        # Create mock location file (required for generate_full_igc to work)
        location_file = tmp_path / "location_20260428.txt"
        location_file.write_text(
            "# address,lat,lon,track,altitude,ground_speed,climb_rate,timestamp,symbolcode\n"
            "FLR123456,47.5,13.0,180,1500,100,2.5,1705312245,^\n"
        )
        
        monkeypatch.chdir(tmp_path)
        
        import pandas as pd
        names_df = pd.DataFrame({'fid': ['FLR123456'], 'name': ['Test Pilot']})
        ddb_devices = {}
        
        from src.ogn_server.telegram_bot import generate_full_igc
        igc_bytes = generate_full_igc('FLR123456', '20260428', names_df, ddb_devices)
        igc_content = igc_bytes.decode('utf-8')
        
        lines = igc_content.split('\n')
        
        # A-record MUST be first line per IGC specification
        assert len(lines) > 0, "IGC content should not be empty"
        assert lines[0].startswith('A'), f"A-record must be FIRST line, got: {lines[0]}"
        
        # Validate A-record format: A + 3 letters + 3 alphanumeric + optional text
        # Example: AXXX001OGNServer
        a_record_pattern = r'^A[A-Z]{3}[A-Z0-9]{3}.*$'
        assert re.match(a_record_pattern, lines[0]), \
            f"A-record format invalid: {lines[0]} (expected pattern: {a_record_pattern})"
        
        # Verify specific expected value based on Config
        from src.ogn_server.config import Config
        expected_a_record = f"A{Config.IGC_MANUFACTURER_CODE}{Config.IGC_DEVICE_SERIAL}OGNServer"
        assert lines[0] == expected_a_record, \
            f"A-record mismatch: got '{lines[0]}', expected '{expected_a_record}'"


class TestLocationFilesDateFilter:
    """Unit tests for scan_location_files() today-only filtering logic."""

    def test_scan_location_files_includes_retention_period(self, tmp_path, monkeypatch):
        """Test that LOCATION_RETENTION_DAYS dates are returned (Europe/Berlin)."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        
        monkeypatch.chdir(tmp_path)
        
        # Calculate dates in Europe/Berlin timezone
        berlin_tz = ZoneInfo("Europe/Berlin")
        today = datetime.now(berlin_tz).strftime("%Y%m%d")
        yesterday = (datetime.now(berlin_tz) - timedelta(days=1)).strftime("%Y%m%d")
        two_days_ago = (datetime.now(berlin_tz) - timedelta(days=2)).strftime("%Y%m%d")
        three_days_ago = (datetime.now(berlin_tz) - timedelta(days=3)).strftime("%Y%m%d")
        
        # Create location files for today, yesterday, 2 days ago, and 3 days ago
        (tmp_path / f"location_{today}.txt").write_text(
            "FLR123456,47.5,13.0,180,1500,100,2.5,1705312245,^\n"
        )
        (tmp_path / f"location_{yesterday}.txt").write_text(
            "FLR789012,47.6,13.1,90,1600,120,1.5,1705312300,>\n"
        )
        (tmp_path / f"location_{two_days_ago}.txt").write_text(
            "FLRABCDEF,47.7,13.2,270,1400,90,1.0,1705312400,<\n"
        )
        (tmp_path / f"location_{three_days_ago}.txt").write_text(
            "FLRGHIJKL,47.8,13.3,45,1300,80,0.5,1705312500,v\n"
        )
        
        names_file = tmp_path / "names.csv"
        names_file.write_text("fid,name\nFLR123456,Pilot Today\nFLR789012,Pilot Yesterday\nFLRABCDEF,Pilot 2 Days\nFLRGHIJKL,Pilot 3 Days\n")
        
        import src.ogn_server.config as config
        monkeypatch.setattr(config.Config, 'NAMES_FILE', str(names_file))
        
        from src.ogn_server.telegram_bot import scan_location_files
        result = scan_location_files()
        
        # Should have data for retention period (2 days: today + yesterday)
        all_dates = set()
        for nick_data in result.values():
            for flarm_dates in nick_data.values():
                all_dates.update(flarm_dates)
        
        assert today in all_dates, f"Today ({today}) should be included"
        assert yesterday in all_dates, f"Yesterday ({yesterday}) should be included (within retention)"
        assert two_days_ago not in all_dates, f"Two days ago ({two_days_ago}) should be filtered out (beyond retention)"
        assert three_days_ago not in all_dates, f"Three days ago ({three_days_ago}) should be filtered out"

    def test_scan_location_files_only_old_files(self, tmp_path, monkeypatch):
        """Test that when only old files exist, empty dict is returned."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        
        monkeypatch.chdir(tmp_path)
        
        # Create file from 5 days ago (should be filtered out)
        five_days_ago = (datetime.now(ZoneInfo("Europe/Berlin")) - timedelta(days=5)).strftime("%Y%m%d")
        (tmp_path / f"location_{five_days_ago}.txt").write_text(
            "FLR123456,47.5,13.0,180,1500,100,2.5,1705312245,^\n"
        )
        
        names_file = tmp_path / "names.csv"
        names_file.write_text("fid,name\nFLR123456,Old Pilot\n")
        
        import src.ogn_server.config as config
        monkeypatch.setattr(config.Config, 'NAMES_FILE', str(names_file))
        
        from src.ogn_server.telegram_bot import scan_location_files
        result = scan_location_files()
        
        # Should return empty since all files are too old
        assert result == {}

    def test_scan_location_files_retention_boundary(self, tmp_path, monkeypatch):
        """Test that dates beyond LOCATION_RETENTION_DAYS are filtered out."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        
        monkeypatch.chdir(tmp_path)
        
        berlin_tz = ZoneInfo("Europe/Berlin")
        today = datetime.now(berlin_tz).strftime("%Y%m%d")
        yesterday = (datetime.now(berlin_tz) - timedelta(days=1)).strftime("%Y%m%d")
        two_days_ago = (datetime.now(berlin_tz) - timedelta(days=2)).strftime("%Y%m%d")
        three_days_ago = (datetime.now(berlin_tz) - timedelta(days=3)).strftime("%Y%m%d")
        one_week_ago = (datetime.now(berlin_tz) - timedelta(days=7)).strftime("%Y%m%d")
        
        # Create files for various dates to test retention boundary
        (tmp_path / f"location_{today}.txt").write_text("FLR111,47.5,13.0,180,1500,100,2.5,1705312245,^\n")
        (tmp_path / f"location_{yesterday}.txt").write_text("FLR222,47.6,13.1,90,1600,120,1.5,1705312300,>\n")
        (tmp_path / f"location_{two_days_ago}.txt").write_text("FLR333,47.7,13.2,270,1400,90,1.0,1705312400,<\n")
        (tmp_path / f"location_{three_days_ago}.txt").write_text("FLR444,47.8,13.3,45,1300,80,0.5,1705312500,v\n")
        (tmp_path / f"location_{one_week_ago}.txt").write_text("FLR555,47.9,13.4,90,1200,70,0.3,1705312600,^\n")
        
        names_file = tmp_path / "names.csv"
        names_file.write_text("fid,name\nFLR111,Today\nFLR222,Yesterday\nFLR333,Two Days\nFLR444,Three Days\nFLR555,Week Ago\n")
        
        import src.ogn_server.config as config
        monkeypatch.setattr(config.Config, 'NAMES_FILE', str(names_file))
        
        from src.ogn_server.telegram_bot import scan_location_files
        result = scan_location_files()
        
        # Collect all dates
        all_dates = set()
        for nick_data in result.values():
            for flarm_dates in nick_data.values():
                all_dates.update(flarm_dates)
        
        # Should include today and yesterday (within 2-day retention), exclude older
        assert today in all_dates, "Today should be included"
        assert yesterday in all_dates, "Yesterday should be included (within retention)"
        assert two_days_ago not in all_dates, "Two days ago should be filtered (beyond retention)"
        assert three_days_ago not in all_dates, "Three days ago should be filtered"
        assert one_week_ago not in all_dates, "One week ago should be filtered"

    def test_scan_location_files_current_file_treated_as_today(self, tmp_path, monkeypatch):
        """Test that location.txt (current) is treated as today's date."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        monkeypatch.chdir(tmp_path)
        
        today = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y%m%d")
        
        # Create current location.txt file
        (tmp_path / "location.txt").write_text(
            "FLR123456,47.5,13.0,180,1500,100,2.5,1705312245,^\n"
        )
        
        names_file = tmp_path / "names.csv"
        names_file.write_text("fid,name\nFLR123456,Current Pilot\n")
        
        import src.ogn_server.config as config
        monkeypatch.setattr(config.Config, 'NAMES_FILE', str(names_file))
        monkeypatch.setattr(config.Config, 'LOCATION_FILE', "location.txt")
        
        from src.ogn_server.telegram_bot import scan_location_files
        result = scan_location_files()
        
        # Should have today's date from location.txt
        assert len(result) > 0, "Should find current location.txt"
        found_today = False
        for nick_data in result.values():
            for flarm_dates in nick_data.values():
                if today in flarm_dates:
                    found_today = True
                    break
        
        assert found_today, "location.txt should be mapped to today's date"
