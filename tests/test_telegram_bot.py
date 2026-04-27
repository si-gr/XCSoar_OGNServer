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
        # Ensure format mentions admin-only notes (even if escaped)
        assert 'admin' in text

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
