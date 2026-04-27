import logging
import os
import signal
from datetime import datetime
from pathlib import Path
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
)
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
)

from .config import Config


# IGC File Request Conversation States
SELECTING_AIRCRAFT = 1
SELECTING_DATE = 2
SENDING_FILE = 3
CONVERSATION_TIMEOUT = 300  # 5 minutes


def format_size(num_bytes: int) -> str:
    """Format a byte count into a human readable size string."""
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes/1024:.1f}KB"
    return f"{num_bytes/1024/1024:.1f}MB"


def scan_igc_files() -> dict[str, list[str]]:
    """
    Scans IGC_FOLDER for .igc files.
    
    Returns:
        Dictionary mapping aircraft nickname to list of dates (YYYYMMDD).
        Example: {
            "Test Pilot": ["20260419", "20260420"],
            "John Doe": ["20260418"]
        }
    
    Raises:
        No specific exceptions - returns empty dict if no files.
    """
    igc_root = Path(Config.IGC_FOLDER)
    result: dict[str, set[str]] = {}

    if not igc_root.exists():
        return {}

    for f in igc_root.glob("*.igc"):
        name = f.name
        if not name.lower().endswith(".igc"):
            continue
        base = name[:-4]  # drop .igc
        if len(base) < 8:
            continue
        date_part = base[:8]
        if not date_part.isdigit():
            continue
        nickname = base[8:].strip()
        if nickname == "":
            continue
        result.setdefault(nickname, set()).add(date_part)

    # convert sets to sorted lists (newest first)
    finalized: dict[str, list[str]] = {}
    for nick, dates in result.items():
        finalized[nick] = sorted(list(dates), reverse=True)
    return finalized


def _build_aircraft_keyboard(aircraft_list: list[str]) -> InlineKeyboardMarkup:
    """Build inline keyboard with aircraft buttons in rows of 2. Includes Cancel button."""
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for ac in aircraft_list:
        row.append(InlineKeyboardButton(ac, callback_data=f"aircraft:{ac}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        # append the last row with a single button
        buttons.append(row)
    # Cancel button in its own final row
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)


def _build_date_keyboard(dates_list: list[str], aircraft: str) -> InlineKeyboardMarkup:
    """Build inline keyboard with date buttons (YYYY-MM-DD) in rows of 2. Includes Back and Cancel."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for d in dates_list:
        disp = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        row.append(InlineKeyboardButton(disp, callback_data=f"date:{d}:{aircraft}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        while len(row) < 2:
            row.append(InlineKeyboardButton("", callback_data="noop"))
        rows.append(row)
    # Back and Cancel row
    rows.append([
        InlineKeyboardButton("◀ Back", callback_data="back"),
        InlineKeyboardButton("Cancel", callback_data="cancel"),
    ])
    return InlineKeyboardMarkup(rows)


async def post_init(application: Application) -> None:
    """Set up bot commands menu after startup."""
    bot = application.bot
    private_commands = [
        BotCommand("start", "Show available commands"),
        BotCommand("a", "Add a glider to name mapping"),
        BotCommand("d", "Remove a glider from name mapping"),
        BotCommand("refreshddb", "Refresh the FLARM device database"),
        BotCommand("igc", "Download IGC flight files"),
        BotCommand("cancel", "Cancel an ongoing operation"),
    ]
    group_commands = private_commands.copy()
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())

class TelegramBot:
    def __init__(self, ogn_client=None):
        self.filename = Config.NAMES_FILE
        self.admin_id = Config.load_admin_chat_id()
        self.token = Config.load_private_key()
        self.application = None
        self.ogn_client = ogn_client
    
    async def start(self, update: Update, context: CallbackContext) -> None:
        """Handle /start command - list all available commands."""
        if update.message is None:
            return
        commands_text = (
            "\\*Available Commands:\\*\\n\\n"
            "/start \\- Show available commands\\n"
"/a \\<fid,name\\> \\- Add a glider to name mapping\\n"
            "   Example: `/a FLR123456,John Doe`\\n\\n"
"/d \\<fid\\> \\- Remove a glider from name mapping\\n"
            "   Example: `/d FLR123456`\\n\\n"
            "/refreshddb \\- Refresh the FLARM device database\\n"
            "   Downloads latest data from glidernet.org\\n\\n"
            "/igc \\- Download IGC flight files\\n"
            "   Interactive selection of aircraft and date\\n\\n"
            "/cancel \\- Cancel an ongoing operation\\n\\n"
            "\\_Commands marked \\(admin\\) require admin privileges\\_"
        )
        await update.message.reply_markdown_v2(commands_text)
    
    async def add(self, update: Update, context: CallbackContext):
        try:
            if update.message is None:
                return
            if update.effective_user is None or update.effective_user.id != int(self.admin_id):
                await update.message.reply_markdown_v2("Unauthorized")
                return
            if context.args is None or len(context.args) != 1:
                await update.message.reply_markdown_v2(
"Usage: /a \\<fid,name\\>\\nExample: `/a FLR123456,John Doe`"
                )
                return
            if len(context.args[0]) > 0 and "," in context.args[0]:
                with open(self.filename, "a") as out:
                    out.write(context.args[0] + "\n")
                await update.message.reply_markdown_v2(
                    "added " + context.args[0].replace(".", "\\.")
                )
            else:
                await update.message.reply_markdown_v2(
"Usage: /a \\<fid,name\\>\\nExample: `/a FLR123456,John Doe`"
                )
        except Exception as e:
            if update and update.message:
                await update.message.reply_markdown_v2(f"Error: {str(e)}")
    
    async def delete(self, update: Update, context: CallbackContext):
        try:
            if update.message is None:
                return
            # Admin check
            if update.effective_user is None or update.effective_user.id != int(self.admin_id):
                await update.message.reply_markdown_v2("Unauthorized")
                return
            if context.args is None or len(context.args) != 1:
                await update.message.reply_markdown_v2(
"Usage: /d \\<fid\\>\\nExample: `/d FLR123456`"
                )
                return
            fid = context.args[0]
            
            if len(fid) > 0:
                with open(self.filename, "r") as f:
                    all_names = f.readlines()
                
                with open(self.filename, "w") as f:
                    deleted = False
                    for n in all_names:
                        if fid not in n:
                            f.write(n)
                        else:
                            deleted = True
            
                if deleted:
                    await update.message.reply_markdown_v2(
                        "deleted " + fid.replace(".", "\\.")
                    )
                else:
                    await update.message.reply_markdown_v2(
                        "not found " + fid.replace(".", "\\.")
                    )
        except Exception as e:
            if update and update.message:
                await update.message.reply_markdown_v2(f"Error: {str(e)}")

    async def refresh_ddb(self, update: Update, context: CallbackContext):
        try:
            if update.message is None:
                return
            # Admin check
            if update.effective_user is None or update.effective_user.id != int(self.admin_id):
                await update.message.reply_markdown_v2("Unauthorized")
                return

            if self.ogn_client is None:
                await update.message.reply_markdown_v2("DDB refresh unavailable: no client reference")
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
        except Exception as e:
            if update and update.message:
                await update.message.reply_markdown_v2(f"Error: {str(e)}")

    async def igc_command(self, update: Update, context: CallbackContext) -> int:
        """Entry point for /igc command. Starts IGC file request conversation."""
        if update.message is None:
            return ConversationHandler.END
        # Admin check
        if update.effective_user is None or update.effective_user.id != int(self.admin_id):
            await update.message.reply_markdown_v2("Unauthorized")
            return ConversationHandler.END
        chat_id = update.message.chat_id if update.message is not None else (update.effective_chat.id if update.effective_chat else None)
        if chat_id is None:
            return ConversationHandler.END

        try:
            await update.message.reply_text("Loading aircraft...")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Loading aircraft...")

        aircraft_data = scan_igc_files() or {}
        if not aircraft_data:
            if update.message is not None:
                await update.message.reply_text("No IGC files available yet")
            else:
                await context.bot.send_message(chat_id=chat_id, text="No IGC files available yet")
            return ConversationHandler.END
        
        context.chat_data['aircraft_data'] = aircraft_data
        aircraft_list = sorted(list(aircraft_data.keys()))
        keyboard = _build_aircraft_keyboard(aircraft_list)
        if update.message is not None:
            await update.message.reply_text("Please select aircraft:", reply_markup=keyboard)
        else:
            await context.bot.send_message(chat_id=chat_id, text="Please select aircraft:", reply_markup=keyboard)
        return SELECTING_AIRCRAFT

    async def aircraft_selected(self, update: Update, context: CallbackContext) -> int:
        """Handles aircraft selection from inline keyboard."""
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "cancel":
            await query.edit_message_text("Cancelled")
            context.chat_data.clear()
            return ConversationHandler.END
        if data == "back":
            return await self.igc_command(update, context)
        if not data.startswith("aircraft:"):
            return SELECTING_AIRCRAFT

        aircraft = data[len("aircraft:") :]
        context.chat_data['selected_aircraft'] = aircraft
        aircraft_data = context.chat_data.get('aircraft_data', {})
        dates = aircraft_data.get(aircraft, [])
        if not dates:
            await query.edit_message_text("No IGC files for selected aircraft")
            context.chat_data.clear()
            return ConversationHandler.END
        keyboard = _build_date_keyboard(dates, aircraft)
        await query.edit_message_text("Please select a date:", reply_markup=keyboard)
        return SELECTING_DATE

    async def date_selected(self, update: Update, context: CallbackContext) -> int:
        """Handles date selection and sends IGC files as documents."""
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "cancel":
            await query.edit_message_text("Cancelled")
            context.chat_data.clear()
            return ConversationHandler.END
        if data == "back":
            aircraft = context.chat_data.get('selected_aircraft')
            if not aircraft:
                return ConversationHandler.END
            aircraft_list = sorted(list(context.chat_data.get('aircraft_data', {}).keys()))
            keyboard = _build_aircraft_keyboard(aircraft_list)
            await query.edit_message_text("Please select aircraft:", reply_markup=keyboard)
            return SELECTING_AIRCRAFT
        if not data.startswith("date:"):
            return SELECTING_DATE
        parts = data.split(":", 2)
        if len(parts) != 3:
            return SELECTING_DATE
        _, ymd, aircraft = parts
        igc_root = Path(Config.IGC_FOLDER)
        pattern = f"{ymd}{aircraft}*.igc"
        files = sorted(list(igc_root.glob(pattern)))
        if not files:
            await query.edit_message_text("File not found")
            return ConversationHandler.END
        total_size = sum(p.stat().st_size for p in files)
        await query.edit_message_text(f"Sending {len(files)} file(s) ({format_size(total_size)})...")
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None:
            return ConversationHandler.END
        for fpath in files:
            try:
                with open(fpath, 'rb') as fh:
                    await context.bot.send_document(chat_id=chat_id, document=fh, filename=fpath.name)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to send IGC file {fpath}: {e}")
        return ConversationHandler.END

    async def cancel_igc(self, update: Update, context: CallbackContext) -> int:
        """Cancels the current IGC conversation."""
        if update.callback_query is not None:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
            try:
                await update.callback_query.edit_message_text("Cancelled")
            except Exception:
                pass
        elif update.message is not None:
            await update.message.reply_text("Cancelled")
        context.chat_data.clear()
        return ConversationHandler.END
    
    
    
    
    
    
    
    def run(self):
        if self.token is None:
            print("Telegram bot token not found")
            return
        
        # Daily restart scheduler removed
        
        self.application = Application.builder().token(self.token).post_init(post_init).build()
        
        add_handler = CommandHandler('a', self.add)
        del_handler = CommandHandler('d', self.delete)
        refresh_handler = CommandHandler('refreshddb', self.refresh_ddb)
        start_handler = CommandHandler('start', self.start)
        
        self.application.add_handler(add_handler)
        self.application.add_handler(del_handler)
        self.application.add_handler(refresh_handler)
        self.application.add_handler(start_handler)
        
        # IGC file request conversation
        igc_conv_handler = ConversationHandler(
            entry_points=[CommandHandler('igc', self.igc_command)],
            states={
                SELECTING_AIRCRAFT: [CallbackQueryHandler(self.aircraft_selected, pattern=r"^aircraft:.*")],
                SELECTING_DATE: [CallbackQueryHandler(self.date_selected, pattern=r"^date:.*")],
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel_igc),
                CallbackQueryHandler(self.cancel_igc, pattern="^cancel$"),
            ],
            per_user=True,
            conversation_timeout=CONVERSATION_TIMEOUT,
        )

        self.application.add_handler(igc_conv_handler)
        
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
