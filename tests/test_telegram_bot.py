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
        mock_context.args = ["FLR11111,New Pilot"]
        
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                
                import asyncio
                asyncio.run(bot.add(mock_update, mock_context))
                
        content = mock_config.read_text()
        assert "FLR11111,New Pilot" not in content

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_delete_command_success(self, mock_context, mock_update, mock_config):
        from src.ogn_server.telegram_bot import TelegramBot
        
        mock_update.effective_user.id = 12345
        mock_update.message = MagicMock()
        mock_update.message.reply_markdown_v2 = AsyncMock()
        mock_context.args = ["FLR12345"]
        
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                
                import asyncio
                asyncio.run(bot.delete(mock_update, mock_context))
                
        content = mock_config.read_text()
        assert "FLR12345" not in content
        assert "FLR67890" in content

    @patch('telegram.Update')
    @patch('telegram.ext.CallbackContext')
    def test_delete_command_not_found(self, mock_context, mock_update, mock_config):
        from src.ogn_server.telegram_bot import TelegramBot
        
        mock_update.effective_user.id = 12345
        mock_update.message = MagicMock()
        mock_update.message.reply_markdown_v2 = AsyncMock()
        mock_context.args = ["FLR99999"]
        
        with patch('src.ogn_server.telegram_bot.Config.load_admin_chat_id', return_value='12345'):
            with patch('src.ogn_server.telegram_bot.Config.load_private_key', return_value='test_token'):
                bot = TelegramBot()
                
                import asyncio
                asyncio.run(bot.delete(mock_update, mock_context))
                
        content = mock_config.read_text()
        assert "FLR12345" in content
        assert "FLR67890" in content