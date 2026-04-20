import logging
import os
import signal
from datetime import datetime
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import Config


class TelegramBot:
    def __init__(self, ogn_client=None):
        self.filename = Config.NAMES_FILE
        self.admin_id = Config.load_admin_chat_id()
        self.token = Config.load_private_key()
        self.application = None
        self.ogn_client = ogn_client
    
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

    async def refresh_ddb(self, update: Update, context: CallbackContext):
        if update.effective_user.id != int(self.admin_id):
            return
        if update.message is None:
            return

        if self.ogn_client is None:
            await update.message.reply_text("DDB refresh unavailable: no client reference")
            return

        try:
            count = self.ogn_client.refresh_ddb_devices()
            await update.message.reply_markdown_v2(
                rf"DDB refreshed: *{count}* devices loaded"
            )
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"DDB refresh failed: {e}")
            await update.message.reply_markdown_v2(
                rf"DDB refresh failed\: *{str(e)}*"
            )
    
    
    
    
    
    
    
    def run(self):
        if self.token is None:
            print("Telegram bot token not found")
            return
        
    # Daily restart scheduler removed
        
        self.application = Application.builder().token(self.token).build()
        
        add_handler = CommandHandler('a', self.add)
        del_handler = CommandHandler('d', self.delete)
        refresh_handler = CommandHandler('refreshddb', self.refresh_ddb)
        
        
        self.application.add_handler(add_handler)
        self.application.add_handler(del_handler)
        self.application.add_handler(refresh_handler)
        
        self.application.run_polling()
    
    def shutdown(self):
        logger = logging.getLogger(__name__)
        logger.info("Shutting down Telegram bot...")
        
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
