import logging
import os
import signal
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.background import BackgroundScheduler

from .config import Config


class TelegramBot:
    def __init__(self):
        self.filename = Config.NAMES_FILE
        self.admin_id = Config.load_admin_chat_id()
        self.token = Config.load_private_key()
        self.restart_pending = False
        self.confirmation_token = None
        self.scheduler = None
        self.application = None
    
    async def add(self, update: Update, context: CallbackContext):
        if update.effective_user.id != int(self.admin_id):
            return
        if context.args is None or len(context.args) != 1:
            return
        if update.message is None:
            return
        
        if len(context.args[0]) > 0 and "," in context.args[0]:
            with open(self.filename, "a") as out:
                out.write(context.args[0] + "\n")
            await update.message.reply_markdown_v2(
                "added " + context.args[0].replace(".", "\\.")
            )
    
    async def delete(self, update: Update, context: CallbackContext):
        if update.effective_user.id != int(self.admin_id):
            return
        if context.args is None or len(context.args) != 1:
            return
        if update.message is None:
            return
        
        if len(context.args[0]) > 0:
            with open(self.filename, "r") as f:
                all_names = f.readlines()
            
            with open(self.filename, "w") as f:
                deleted = False
                for n in all_names:
                    if context.args[0] not in n:
                        f.write(n)
                    else:
                        deleted = True
            
            if deleted:
                await update.message.reply_markdown_v2(
                    "deleted " + context.args[0].replace(".", "\\.")
                )
            else:
                await update.message.reply_markdown_v2(
                    "not found " + context.args[0].replace(".", "\\.")
                )
    
    async def restart_request(self, update: Update, context: CallbackContext):
        if update.effective_user.id != int(self.admin_id):
            return
        if update.message is None:
            return
        
        # Rate limiting: check if restart was done recently
        if self.restart_pending and self.confirmation_token is not None and (datetime.now() - self.confirmation_token) < timedelta(seconds=60):
            await update.message.reply_text("Rate limited. Wait 60 seconds between restart requests.")
            return
        
        # Set confirmation state
        self.restart_pending = True
        self.confirmation_token = datetime.now()
        
        await update.message.reply_text(
            "⚠️ Restart requested. Reply /confirm_restart within 60 seconds to confirm."
        )
    
    async def restart_confirm(self, update: Update, context: CallbackContext):
        if update.effective_user.id != int(self.admin_id):
            return
        if update.message is None:
            return
        
        if not self.restart_pending:
            await update.message.reply_text("No pending restart request.")
            return
        
        self.restart_pending = False
        
        await update.message.reply_text("Restarting server now...")
        
        # Trigger graceful shutdown via SIGTERM
        os.kill(os.getpid(), signal.SIGTERM)
    
    async def cancel_restart(self, update: Update, context: CallbackContext):
        if update.effective_user.id != int(self.admin_id):
            return
        if update.message is None:
            return
        
        if self.restart_pending:
            self.restart_pending = False
            await update.message.reply_text("Restart cancelled.")
        else:
            await update.message.reply_text("No pending restart to cancel.")
    
    def setup_daily_restart(self):
        """Set up daily automatic restart at configured hour (default 3 AM UTC)."""
        self.scheduler = BackgroundScheduler()
        
        self.scheduler.add_job(
            self._trigger_daily_restart,
            'cron',
            hour=Config.DAILY_RESTART_HOUR,
            minute=0,
            timezone='UTC'
        )
        self.scheduler.start()
    
    def _trigger_daily_restart(self):
        print(f"Daily restart triggered at {datetime.now()} UTC")
        os.kill(os.getpid(), signal.SIGTERM)
    
    def run(self):
        if self.token is None:
            print("Telegram bot token not found")
            return
        
        # Set up daily automatic restart scheduler
        self.setup_daily_restart()
        
        self.application = Application.builder().token(self.token).build()
        
        add_handler = CommandHandler('a', self.add)
        del_handler = CommandHandler('d', self.delete)
        restart_request_handler = CommandHandler('restart', self.restart_request)
        restart_confirm_handler = CommandHandler('confirm_restart', self.restart_confirm)
        cancel_restart_handler = CommandHandler('cancel_restart', self.cancel_restart)
        
        self.application.add_handler(add_handler)
        self.application.add_handler(del_handler)
        self.application.add_handler(restart_request_handler)
        self.application.add_handler(restart_confirm_handler)
        self.application.add_handler(cancel_restart_handler)
        
        self.application.run_polling()
    
    def shutdown(self):
        """Gracefully stop the Telegram bot and its scheduler."""
        logger = logging.getLogger(__name__)
        logger.info("Shutting down Telegram bot...")
        
        if self.scheduler:
            try:
                self.scheduler.shutdown()
                logger.info("Telegram bot scheduler stopped")
            except Exception as e:
                logger.error("Failed to stop scheduler: %s", e)
        
        if self.application:
            try:
                self.application.stop()
                logger.info("Telegram bot polling stopped")
            except Exception as e:
                logger.error("Failed to stop Telegram bot: %s", e)
        
        logger.info("Telegram bot shutdown completed")


async def run_bot_async():
    bot = TelegramBot()
    bot.run()
