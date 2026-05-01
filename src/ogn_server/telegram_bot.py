import logging
import os
import signal
from datetime import datetime, timedelta
from pathlib import Path
import csv
from zoneinfo import ZoneInfo
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

from telegram.helpers import escape_markdown
from .config import Config
from io import BytesIO

 

# IGC File Request Conversation States
SELECTING_AIRCRAFT = 1
SELECTING_DATE = 2
SENDING_FILE = 3
CONVERSATION_TIMEOUT = 300  # 5 minutes

# Location to IGC Conversion Conversation States
LOC2IGC_SELECT_AIRCRAFT = 4
LOC2IGC_SELECT_DATE = 5
LOC2IGC_GENERATING = 6

# Delete Command Conversation States
SELECTING_AIRCRAFT_FOR_DELETE = 7
CONFIRMING_DELETION = 8


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


def scan_names_csv() -> list[tuple[str, str]]:
    """
    Read names.csv and return list of (fid, name) tuples for selection.
    
    Returns:
        List of (fid, name) tuples sorted by name.
        Example: [("FLR123456", "John Doe"), ("FLR789012", "Jane Smith")]
        
    Raises:
        Returns empty list if file doesn't exist or is empty.
    """
    from src.ogn_server.config import Config
    
    names_file = Path(Config.NAMES_FILE)
    if not names_file.exists():
        return []
    
    result: list[tuple[str, str]] = []
    try:
        with open(names_file, "r") as f:
            reader = csv.reader(f)
            for row in reader:
                # Skip header row and empty rows
                if not row or row[0].lower() == "fid":
                    continue
                if len(row) >= 2:
                    fid = row[0].strip()
                    name = row[1].strip()
                    if fid and name:
                        result.append((fid, name))
    except Exception:
        return []
    
    # Sort by name for display
    return sorted(result, key=lambda x: x[1])


def generate_full_igc(
    flarm_id: str,
    date_str: str,
    names_df,
    ddb_devices: dict
) -> bytes:
    """
    Generate full IGC file with H-records and B-records from location data.
    
    Args:
        flarm_id: FLARM device ID (e.g., "FLR123456")
        date_str: Date in YYYYMMDD format
        names_df: DataFrame with fid,name columns
        ddb_devices: Dict from DDB API
    
    Returns:
        bytes: Complete IGC file content ready to send via Telegram
    
    IGC Format Reference:
        - IGC_FILE_FORMAT_VERSION=6
        - H-records: Metadata (pilot, aircraft, date, etc.)
        - B-records: Position fixes (time, lat, lon, altitude)
    """
    # Determine location file to read from
    location_path = None
    for fname in (f"location_{date_str}.txt", "location.txt"):
        p = Path(fname)
        if p.exists():
            location_path = p
            break
    if location_path is None:
        raise FileNotFoundError("Location file not found")

    # Read location data (CSV-like, with 9 fields per line as written by _write_location)
    rows = []
    try:
        with open(location_path, newline="") as f:
            reader = csv.reader(f)
            for r in reader:
                if not r:
                    continue
                # Expect at least 8 fields per row
                if len(r) < 8:
                    continue
                rows.append([cell.strip() for cell in r])
    except FileNotFoundError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to parse location file: {e}")

    # Filter by FLARM ID (full or last 4 chars fallback)
    flarm_upper = flarm_id.upper()
    def _row_matches(r):
        addr = (r[0] or "").strip().upper()
        if addr == flarm_upper:
            return True
        if len(addr) >= 4 and addr[-4:] == flarm_upper[-4:]:
            return True
        return False
    loc_rows = [r for r in rows if _row_matches(r)]
    if not loc_rows:
        raise ValueError("No location data found")

    # Helpers
    berlin_tz = ZoneInfo("Europe/Berlin")
    def parse_ts(val):
        if val is None:
            return None
        s = str(val).strip()
        if s == "":
            return None
        # Unix timestamp seconds
        if s.isdigit():
            try:
                return datetime.utcfromtimestamp(int(s))
            except Exception:
                return None
        # ISO-like formats
        try:
            return datetime.fromisoformat(s)
        except Exception:
            pass
        # Fallback generic parse
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
        return None

    def to_berlin(dt_obj: datetime) -> datetime:
        if dt_obj is None:
            return None
        if dt_obj.tzinfo is None:
            # Assume UTC then convert
            dt_utc = dt_obj.replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt_utc = dt_obj
        return dt_utc.astimezone(berlin_tz)

    # Build H-records
    first_ts = None
    for r in loc_rows:
        ts = r[7] if len(r) > 7 else None
        dtv = parse_ts(ts)
        if dtv is not None:
            first_ts = dtv
            break
    if first_ts is None:
        # As a last resort, use date_str to get a date
        try:
            d = datetime.strptime(date_str, "%Y%m%d")
            first_ts = d
        except Exception:
            first_ts = datetime.utcnow()
    first_dt = to_berlin(first_ts)  # type: ignore
    HFDTE_line = None
    if first_dt is not None:
        dd = first_dt.day
        mm = first_dt.month
        yy = first_dt.year % 100
        HFDTE_line = f"HFDTE{dd:02d}{mm:02d}{yy:02d}"
    else:
        HFDTE_line = "HFDTE000000"  # fallback

    # Pilot
    pilot_name = None
    if isinstance(names_df, (list, tuple)):
        # Graceful fallback if not a DataFrame
        pilot_name = None
    else:
        try:
            pilot_name = str(names_df.loc[names_df["fid"] == flarm_id, "name"].iloc[0])
        except Exception:
            pilot_name = None
    if not pilot_name or pilot_name == '....':
        reg = None
        try:
            reg = __import__('ogn_server.ddb', fromlist=['get_registration']).get_registration(flarm_id, ddb_devices)  # type: ignore
        except Exception:
            reg = None
        if reg not in (None, ""):
            try:
                matches = names_df.loc[names_df["fid"] == reg, "name"]
                if len(matches) > 0:
                    pilot_name = str(matches.iloc[0])
            except Exception:
                pass
    if not pilot_name:
        pilot_name = flarm_id

    # Aircraft model
    aircraft_model = None
    try:
        aircraft_model = __import__('ogn_server.ddb', fromlist=['get_aircraft_model']).get_aircraft_model(flarm_id, ddb_devices)  # type: ignore
    except Exception:
        try:
            from .ddb import get_aircraft_model as _gam
            aircraft_model = _gam(flarm_id, ddb_devices)  # type: ignore
        except Exception:
            aircraft_model = None
    if not aircraft_model:
        aircraft_model = "Unknown"

    # Registration and CN
    registration = None
    try:
        registration = __import__('ogn_server.ddb', fromlist=['get_registration']).get_registration(flarm_id, ddb_devices)  # type: ignore
    except Exception:
        registration = None
    if not registration:
        registration = "Unknown"

    cn = None
    try:
        cn = __import__('ogn_server.ddb', fromlist=['get_cn']).get_cn(flarm_id, ddb_devices)  # type: ignore
    except Exception:
        try:
            from .ddb import get_cn as _get_cn
            cn = _get_cn(flarm_id, ddb_devices)  # type: ignore
        except Exception:
            cn = None
    if not cn:
        cn = "Unknown"

    # Wall-time altitude average for GPS altitude header
    alt_values = []
    # Compute later from B-records; we'll fill after building B-records
    # Placeholder for avg altitude calculation
    # Build B-records
    b_lines: list[str] = []
    avg_alt = 0
    # Prepare inline helper to convert lat/lon to DMS-like string
    for r in loc_rows:
        try:
            lat = float(r[1])
            lon = float(r[2])
            alt = int(float(r[4])) if r[4] != '' else 0
            ts = r[7] if len(r) > 7 else None
            dtv = parse_ts(ts)
            if dtv is None:
                continue
            dt_local = to_berlin(dtv)
        except Exception:
            continue
        hh = dt_local.hour
        mi = dt_local.minute
        ss = dt_local.second
        tstr = f"{hh:02d}{mi:02d}{ss:02d}"
        # Latitude
        lat_dir = 'N' if lat >= 0 else 'S'
        la = abs(lat)
        lat_deg = int(la)
        lat_min = (la - lat_deg) * 60
        lat_sec = (lat_min - int(lat_min)) * 60
        lat_field = f"{lat_deg:02d}{int(lat_min):02d}{int(lat_sec*10):03d}{lat_dir}"
        # Longitude
        lon_dir = 'E' if lon >= 0 else 'W'
        lo = abs(lon)
        lon_deg = int(lo)
        lon_min = (lo - lon_deg) * 60
        lon_sec = (lon_min - int(lon_min)) * 60
        lon_field = f"{lon_deg:03d}{int(lon_min):02d}{int(lon_sec*10):03d}{lon_dir}"
        line = f"B{tstr}{lat_field}{lon_field}A00000{int(alt):05d}"
        b_lines.append(line)
        alt_values.append(alt)

    if loc_rows:
        if alt_values:
            avg_alt = int(round(sum(alt_values) / len(alt_values))) if len(alt_values) > 0 else 0
        else:
            avg_alt = 0
    # Header lines for IGC
    header_lines: list[str] = []
    # A-record MUST be first line per IGC specification
    a_record = f"A{Config.IGC_MANUFACTURER_CODE}{Config.IGC_DEVICE_SERIAL}OGNServer"
    header_lines.append(a_record)
    header_lines.append("IGC_FILE_FORMAT_VERSION=6")
    header_lines.append("HFTZNTIMEZONE:Europe/Berlin")
    header_lines.append(HFDTE_line)
    header_lines.append("HFFLTLX:Unknown")
    header_lines.append(f"HFPLTPILOTINCHARGE:{pilot_name}")
    header_lines.append("HFCM2CREW2:Unknown")
    header_lines.append(f"HFTYPETYPEOFGLIDER:{aircraft_model}")
    header_lines.append(f"HFREGREGISTRATION:{registration}")
    header_lines.append(f"HFRFNCOMPETITIONID:{cn}")
    header_lines.append(f"HFGPSALTGPSAltitude:{avg_alt}")
    # Combine
    igc_content = "\n".join(header_lines + b_lines) + ("\n" if len(b_lines) > 0 else "")
    return igc_content.encode("utf-8")

def scan_location_files() -> dict[str, dict[str, list[str]]]:
    """
    Scans location_*.txt files for available aircraft and dates.
    
    Returns:
        Dictionary mapping nickname -> {flarm_id: [dates]}
        Example: {
            "John Doe": {"FLR123456": ["20260427", "20260428"]},
            "Jane": {"FLRABCDEF": ["20260428"]}
        }
    
    Raises:
        No specific exceptions - returns empty dict if no files.
    """
    # Lazy imports / lookups for name resolution
    try:
        from .client import get_ddb_devices, get_registration  # type: ignore
    except Exception:
        get_ddb_devices = None  # type: ignore
        get_registration = None  # type: ignore

    # Load DDB devices if available
    ddb_devices: dict[str, dict] = {}
    if callable(get_ddb_devices):
        try:
            ddb_devices = get_ddb_devices()
        except Exception:
            ddb_devices = {}

    # Load names.csv for nickname resolution
    names_df = None
    try:
        import pandas as pd  # local import to avoid top-level dependency if unused
        names_path = Path(Config.NAMES_FILE)
        if names_path.exists():
            names_df = pd.read_csv(Config.NAMES_FILE, names=["fid", "name"], header=0)
    except Exception:
        names_df = None

    # Helper to map a FLARM ID to a nickname
    def resolve_nickname(flarm_id: str) -> str:
        reg = None
        if callable(get_registration) and isinstance(ddb_devices, dict):
            try:
                reg = get_registration(flarm_id, ddb_devices)  # type: ignore
            except Exception:
                reg = None
        if reg is not None and names_df is not None:
            try:
                matches = names_df[names_df["fid"] == reg]
                if len(matches) > 0:
                    nickname = matches.iloc[0]["name"]
                    if nickname != '....':
                        return str(nickname)
                    else:
                        return reg
            except Exception:
                pass
            return reg
        if names_df is not None:
            try:
                matches = names_df[names_df["fid"] == flarm_id]
                if len(matches) > 0:
                    nickname = matches.iloc[0]["name"]
                    if nickname != '....':
                        return str(nickname)
            except Exception:
                pass
        return flarm_id

    # Scan location files in the current directory
    results: dict[str, dict[str, set[str]]] = {}
    location_dir = Path(".")
    if not location_dir.exists():
        return {}

    # Calculate valid dates: today only (Europe/Berlin timezone)
    berlin_tz = ZoneInfo("Europe/Berlin")
    today_berlin = datetime.now(berlin_tz).strftime("%Y%m%d")
    valid_dates = {today_berlin}

    for loc_file in location_dir.glob("location*.txt"):
        fname = loc_file.name
        date_str = None
        if fname == Config.LOCATION_FILE:
            date_str = datetime.now(berlin_tz).strftime("%Y%m%d")
        else:
            if fname.startswith("location_") and fname.endswith(".txt"):
                base = fname[len("location_"):-len(".txt")]
                if len(base) == 8 and base.isdigit():
                    date_str = base
        if not date_str:
            continue
        # Filter: only include today and yesterday (Europe/Berlin)
        if date_str not in valid_dates:
            continue

        try:
            with open(loc_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 1:
                        continue
                    flarm_id = parts[0] if parts[0] else ""
                    if not flarm_id:
                        continue
                    nickname = resolve_nickname(flarm_id)
                    results.setdefault(nickname, {}).setdefault(flarm_id, set()).add(date_str)
        except OSError:
            continue

    # Convert to final structure: nickname -> {flarm_id: [dates]}
    final: dict[str, dict[str, list[str]]] = {}
    for nick, fid_map in results.items():
        inner: dict[str, list[str]] = {}
        for fid, dates in fid_map.items():
            inner[fid] = sorted(list(dates), reverse=True)
        final[nick] = inner

    return final

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

def _build_aircraft_deletion_keyboard(aircraft_list: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Build inline keyboard with aircraft buttons (display name) + Cancel button."""
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for fid, name in aircraft_list:
        # Display name, callback data contains fid
        row.append(InlineKeyboardButton(name, callback_data=f"aircraft:{fid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Cancel button in its own final row
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def _build_delete_confirmation_keyboard(fid: str, name: str) -> InlineKeyboardMarkup:
    """Build Yes/No confirmation keyboard for aircraft deletion."""
    rows: list[list[InlineKeyboardButton]] = []
    # Yes and No row
    rows.append([
        InlineKeyboardButton("Yes", callback_data=f"yes:{fid}:{name}"),
        InlineKeyboardButton("No", callback_data="no"),
    ])
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
        BotCommand("start", "Show help message"),
        BotCommand("a", "Add glider nickname"),
        BotCommand("d", "Delete glider nickname"),
        BotCommand("refreshddb", "Refresh DDB"),
        BotCommand("igc", "Request IGC files"),
        BotCommand("loc2igc", "Convert location to IGC"),
        BotCommand("cancel", "Cancel operation"),
    ]
    group_commands = private_commands.copy()
    group_commands.append(BotCommand("loc2igc", "Convert location data to IGC format"))
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
            "*Available Commands:*\n"
            "/start \- Show this help message\\\n"
            "/a \<fid,name\> \- Add a glider nickname\\\n"
            "   Example: `/a FLR123456,John Doe`\\\n"
            "/d \- Delete a glider nickname \(interactive\)\\\n"
            "   Shows list of aircraft, select to delete\\\n"
            "/refreshddb \- Refresh FLARM device database\\\n"
            "   Downloads latest data from glidernet\\\n"
            "/igc \- Request IGC flight files\\\n"
            "   Interactive aircraft and date selection\\\n"
            "/loc2igc \- Convert location\.txt to IGC\\\n"
            "   Generate IGC from recorded locations\\\n"
            "/cancel \- Cancel current operation\\\n"
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
                "added " + escape_markdown(context.args[0], version=2)
                )
            else:
                await update.message.reply_markdown_v2(
"Usage: /a \\<fid,name\\>\\nExample: `/a FLR123456,John Doe`"
                )
        except Exception as e:
            if update and update.message:
                await update.message.reply_markdown_v2(f"Error: {escape_markdown(str(e), version=2)}")
    
    async def delete_command(self, update: Update, context: CallbackContext) -> int:
        """Entry point for /d command. Starts aircraft deletion conversation."""
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
            await update.message.reply_text("Loading registered aircraft...")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Loading registered aircraft...")

        aircraft_list = scan_names_csv()
        if not aircraft_list:
            if update.message is not None:
                await update.message.reply_text("No registered aircraft in names.csv")
            else:
                await context.bot.send_message(chat_id=chat_id, text="No registered aircraft in names.csv")
            return ConversationHandler.END
        
        context.chat_data['aircraft_list'] = aircraft_list
        keyboard = _build_aircraft_deletion_keyboard(aircraft_list)
        if update.message is not None:
            await update.message.reply_text("Select aircraft to delete:", reply_markup=keyboard)
        else:
            await context.bot.send_message(chat_id=chat_id, text="Select aircraft to delete:", reply_markup=keyboard)
        return SELECTING_AIRCRAFT_FOR_DELETE

    async def refresh_ddb(self, update: Update, context: CallbackContext) -> None:
        if update.message is None:
            return
        if update.effective_user is None or update.effective_user.id != int(self.admin_id):
            await update.message.reply_markdown_v2("Unauthorized")
            return
        
        try:
            await update.message.reply_text("Refreshing FLARM device database...")
            if self.ogn_client:
                count = self.ogn_client.refresh_ddb_devices()
                await update.message.reply_text(f"Successfully refreshed {count} devices from DDB.")
            else:
                await update.message.reply_text("OGN client not available for DDB refresh.")
        except Exception as e:
            await update.message.reply_markdown_v2(f"Error refreshing DDB: {escape_markdown(str(e), version=2)}")

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

    async def aircraft_for_deletion_selected(self, update: Update, context: CallbackContext) -> int:
        """Handles aircraft selection from inline keyboard for deletion."""
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "cancel":
            await query.edit_message_text("Cancelled")
            context.chat_data.clear()
            return ConversationHandler.END
        if data == "back":
            return await self.delete_command(update, context)
        if not data.startswith("aircraft:"):
            return SELECTING_AIRCRAFT_FOR_DELETE

        fid = data[len("aircraft:") :]
        # Find name from fid
        aircraft_list = context.chat_data.get('aircraft_list', [])
        name = next((n for f, n in aircraft_list if f == fid), "Unknown")
        
        context.chat_data['selected_fid'] = fid
        context.chat_data['selected_name'] = name
        
        keyboard = _build_delete_confirmation_keyboard(fid, name)
        await query.edit_message_text(f"Delete {name} ({fid})?", reply_markup=keyboard)
        return CONFIRMING_DELETION

    async def deletion_confirmed(self, update: Update, context: CallbackContext) -> int:
        """Handles Yes/No confirmation for aircraft deletion."""
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "cancel":
            await query.edit_message_text("Cancelled")
            context.chat_data.clear()
            return ConversationHandler.END
        if data == "back":
            aircraft_list = context.chat_data.get('aircraft_list', [])
            keyboard = _build_aircraft_deletion_keyboard(aircraft_list)
            await query.edit_message_text("Select aircraft to delete:", reply_markup=keyboard)
            return SELECTING_AIRCRAFT_FOR_DELETE
        if data == "no":
            await query.edit_message_text("Deletion cancelled")
            context.chat_data.clear()
            return ConversationHandler.END
        if not data.startswith("yes:"):
            return CONFIRMING_DELETION
        
        parts = data.split(":", 2)
        if len(parts) != 3:
            return CONFIRMING_DELETION
        _, fid, name = parts
        
        # Perform actual deletion from names.csv
        try:
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
                await query.edit_message_text(f"Deleted {name} ({fid})")
            else:
                await query.edit_message_text(f"Aircraft {name} ({fid}) not found")
        except Exception as e:
            await query.edit_message_text(f"Error: {str(e)}")
        
        context.chat_data.clear()
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
    
    async def loc2igc_command(self, update: Update, context: CallbackContext) -> int:
        """Entry point for /loc2igc command. Starts location-to-IGC conversion conversation."""
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
            await update.message.reply_text("Scanning location files...")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Scanning location files...")

        location_data = scan_location_files() or {}
        if not location_data:
            if update.message is not None:
                await update.message.reply_text("No location data available yet")
            else:
                await context.bot.send_message(chat_id=chat_id, text="No location data available yet")
            return ConversationHandler.END
        context.chat_data['loc_aircraft_data'] = location_data
        aircraft_list = sorted(list(location_data.keys()))
        keyboard = _build_aircraft_keyboard(aircraft_list)
        if update.message is not None:
            await update.message.reply_text("Please select aircraft:", reply_markup=keyboard)
        else:
            await context.bot.send_message(chat_id=chat_id, text="Please select aircraft:", reply_markup=keyboard)
        return LOC2IGC_SELECT_AIRCRAFT

    async def loc2igc_aircraft_selected(self, update: Update, context: CallbackContext) -> int:
        """Handles aircraft selection from location data."""
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "cancel":
            await query.edit_message_text("Cancelled")
            context.chat_data.clear()
            return ConversationHandler.END
        if data == "back":
            return await self.loc2igc_command(update, context)
        if not data.startswith("aircraft:"):
            return LOC2IGC_SELECT_AIRCRAFT

        nickname = data[len("aircraft:") :]
        context.chat_data['selected_nickname'] = nickname
        loc_aircraft = context.chat_data.get('loc_aircraft_data', {})
        flarm_map = loc_aircraft.get(nickname, {})
        if not flarm_map:
            await query.edit_message_text("No data for selected aircraft")
            context.chat_data.clear()
            return ConversationHandler.END

        # Build a combined date keyboard: for each flarm_id present for this nickname
        # Use aircraft label as "nickname:flarm_id" to pass both pieces to the date handler
        rows = []
        for fid, dates in flarm_map.items():
            if not dates:
                continue
            aircraft_label = f"{nickname}:{fid}"
            # Build per-flarm date buttons (two per row)
            r = []
            for d in dates:
                disp = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                r.append(InlineKeyboardButton(disp, callback_data=f"date:{d}:{aircraft_label}"))
                if len(r) == 2:
                    rows.append(r)
                    r = []
            if r:
                while len(r) < 2:
                    r.append(InlineKeyboardButton("", callback_data="noop"))
                rows.append(r)
        # Back and Cancel row
        rows.append([
            InlineKeyboardButton("◀ Back", callback_data="back"),
            InlineKeyboardButton("Cancel", callback_data="cancel"),
        ])
        keyboard = InlineKeyboardMarkup(rows)
        await query.edit_message_text("Please select date:", reply_markup=keyboard)
        return LOC2IGC_SELECT_DATE

    async def loc2igc_date_selected(self, update: Update, context: CallbackContext) -> int:
        """Generates and sends IGC file from a location-based data set."""
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "cancel":
            await query.edit_message_text("Cancelled")
            context.chat_data.clear()
            return ConversationHandler.END
        if data == "back":
            # Return to aircraft selection
            nickname = context.chat_data.get('selected_nickname')
            if not nickname:
                return ConversationHandler.END
            aircraft_list = sorted(list(context.chat_data.get('loc_aircraft_data', {}).keys()))
            keyboard = _build_aircraft_keyboard(aircraft_list)
            await query.edit_message_text("Please select aircraft:", reply_markup=keyboard)
            return LOC2IGC_SELECT_AIRCRAFT
        if not data.startswith("date:"):
            return LOC2IGC_SELECT_DATE
        parts = data.split(":", 2)
        if len(parts) != 3:
            return LOC2IGC_SELECT_DATE
        _, ymd, aircraft_label = parts
        # aircraft_label format: nickname:flarm_id
        if ":" not in aircraft_label:
            await query.edit_message_text("No data found for this aircraft on this date")
            context.chat_data.clear()
            return ConversationHandler.END
        nickname, flarm_id = aircraft_label.split(":", 1)
        if not nickname or not flarm_id:
            await query.edit_message_text("No data found for this aircraft on this date")
            context.chat_data.clear()
            return ConversationHandler.END

        # Generate IGC from location data
        await query.edit_message_text("Generating IGC...")
        try:
            # Lazy import and data loading
            try:
                from .client import get_ddb_devices  # type: ignore
                ddb_devices = get_ddb_devices() if callable(get_ddb_devices) else {}
            except Exception:
                ddb_devices = {}

            # Load names.csv for pilot/name resolution
            names_df = None
            try:
                import pandas as pd  # local import
                name_path = Path(Config.NAMES_FILE)
                if name_path.exists():
                    names_df = pd.read_csv(Config.NAMES_FILE, names=["fid", "name"], header=0)
            except Exception:
                names_df = None

            igc_bytes = generate_full_igc(flarm_id, ymd, names_df, ddb_devices)
            from io import BytesIO as _BytesIO
            bio = _BytesIO(igc_bytes)
            bio.seek(0)
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id is None:
                context.chat_data.clear()
                return ConversationHandler.END
            await context.bot.send_document(chat_id=chat_id, document=bio, filename=f"{ymd}_{nickname}.igc")
            await query.edit_message_text("IGC file sent")
            context.chat_data.clear()
            return ConversationHandler.END
        except FileNotFoundError:
            await query.edit_message_text("Location file not found for this date")
            context.chat_data.clear()
            return ConversationHandler.END
        except ValueError:
            await query.edit_message_text("No data found for this aircraft on this date")
            context.chat_data.clear()
            return ConversationHandler.END
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to generate IGC: {e}")
            await query.edit_message_text("Failed to generate IGC")
            context.chat_data.clear()
            return ConversationHandler.END
    
    
    
    
    
    
    def run(self):
        if self.token is None:
            print("Telegram bot token not found")
            return
        
        # Daily restart scheduler removed
        
        self.application = Application.builder().token(self.token).post_init(post_init).build()
        
        add_handler = CommandHandler('a', self.add)
        # Replace simple /d handler with a ConversationHandler for deletion flow
        del_conv_handler = ConversationHandler(
            entry_points=[CommandHandler('d', self.delete_command)],
            states={
                SELECTING_AIRCRAFT_FOR_DELETE: [CallbackQueryHandler(self.aircraft_for_deletion_selected, pattern=r"^aircraft:.*")],
                CONFIRMING_DELETION: [CallbackQueryHandler(self.deletion_confirmed, pattern=r"^(yes|no|back|cancel).*")],
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel_igc),
                CallbackQueryHandler(self.cancel_igc, pattern="^cancel$"),
            ],
            per_user=True,
            conversation_timeout=CONVERSATION_TIMEOUT,
        )
        refresh_handler = CommandHandler('refreshddb', self.refresh_ddb)
        start_handler = CommandHandler('start', self.start)
        
        self.application.add_handler(add_handler)
        self.application.add_handler(del_conv_handler)
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

        

        # Location to IGC conversion conversation
        loc2igc_conv_handler = ConversationHandler(
            entry_points=[CommandHandler('loc2igc', self.loc2igc_command)],
            states={
                LOC2IGC_SELECT_AIRCRAFT: [CallbackQueryHandler(self.loc2igc_aircraft_selected, pattern=r"^aircraft:.*")],
                LOC2IGC_SELECT_DATE: [CallbackQueryHandler(self.loc2igc_date_selected, pattern=r"^date:.*")],
            },
            fallbacks=[CallbackQueryHandler(self.cancel_igc, pattern=r"^cancel$"), CommandHandler('cancel', self.cancel_igc)],
        )
        self.application.add_handler(loc2igc_conv_handler)
        
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
