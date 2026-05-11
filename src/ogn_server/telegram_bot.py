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
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import json


def _parse_timestamp(val) -> datetime | None:
    """Parse timestamp string to datetime object (UTC)."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None
    if s.isdigit():
        try:
            return datetime.utcfromtimestamp(int(s))
        except Exception:
            return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _to_berlin_time(dt: datetime) -> datetime:
    """Convert UTC datetime to Berlin timezone for IGC date labeling."""
    berlin_tz = ZoneInfo("Europe/Berlin")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(berlin_tz)

 

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

# Quick Add Conversation States
QUICKADD_SELECT = 12
QUICKADD_CONFIRM = 13

# Overdue SAR tracking conversation states
OVERDUE_SHOW_LIST = 10
OVERDUE_EXPORT = 11


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
    
    has_matching_date = False
    for r in loc_rows:
        ts = r[7] if len(r) > 7 else None
        dtv = _parse_timestamp(ts)
        if dtv is not None:
            dt_local = _to_berlin_time(dtv)
            if dt_local.strftime("%Y%m%d") == date_str:
                has_matching_date = True
                break
    
    if not has_matching_date:
        raise ValueError(f"No location data found for {date_str}")

    # Build H-records
    first_ts = None
    for r in loc_rows:
        ts = r[7] if len(r) > 7 else None
        dtv = _parse_timestamp(ts)
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
    first_dt = _to_berlin_time(first_ts)  # type: ignore
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
            dtv = _parse_timestamp(ts)
            if dtv is None:
                continue
            dt_local = _to_berlin_time(dtv)
            
            record_date = dt_local.strftime("%Y%m%d")
            if record_date != date_str:
                continue
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

    # Calculate valid dates: last N days based on LOCATION_RETENTION_DAYS (Europe/Berlin timezone)
    from datetime import timedelta
    berlin_tz = ZoneInfo("Europe/Berlin")
    now = datetime.now(berlin_tz)
    valid_dates = set()
    for i in range(Config.LOCATION_RETENTION_DAYS):
        date = now - timedelta(days=i)
        valid_dates.add(date.strftime("%Y%m%d"))

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
        BotCommand("overdue", "List overdue aircraft for SAR"),
        BotCommand("quickadd", "Quick-add from live beacons"),
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
        self.names_df = None
        # Geofence alert scheduler and in-memory cooldown state
        self.scheduler = None
        self._alerted_offline = {}
        # Store bot reference for chunked message sending
        self.bot = None
    
    async def _send_chunked_messages(self, chat_id: int, text: str, reply_markup=None) -> None:
        """Send long messages in chunks to avoid Telegram's 4096 char limit.
        
        Args:
            chat_id: Target chat ID
            text: Message text to send (may exceed 4096 chars)
            reply_markup: Optional inline keyboard to attach to last message only
        """
        if not text:
            return
        
        MAX_MESSAGE_LENGTH = 4096
        messages = []
        
        while len(text) > MAX_MESSAGE_LENGTH:
            # Find a good split point near the limit
            chunk = text[:MAX_MESSAGE_LENGTH]
            # Try to split at newline for cleaner messages
            last_newline = chunk.rfind('\n')
            if last_newline > MAX_MESSAGE_LENGTH // 2:
                chunk = chunk[:last_newline]
                text = text[last_newline + 1:]
            else:
                text = text[MAX_MESSAGE_LENGTH:]
            messages.append(chunk)
        
        messages.append(text)  # Add remaining text
        
        # Send messages, last one gets the keyboard
        for i, msg in enumerate(messages):
            if i == len(messages) - 1:
                await self.bot.send_message(chat_id, msg, reply_markup=reply_markup)
            else:
                await self.bot.send_message(chat_id, msg)
    
    async def start(self, update: Update, context: CallbackContext) -> None:
        """Handle /start command - list all available commands."""
        if update.message is None:
            return
        commands_text = (
            "*Available Commands:*" + "\n"
            "/start \\- Show this help message" + "\n"
            "/a \\<fid,name\\> \\- Add a glider nickname" + "\n"
            "   Example: `/a FLR123456,John Doe`" + "\n"
            "/d \\- Delete a glider nickname \\(interactive\\)" + "\n"
            "   Shows list of aircraft, select to delete" + "\n"
            "/refreshddb \\- Refresh FLARM device database" + "\n"
            "   Downloads latest data from glidernet" + "\n"
            "/igc \\- Request IGC flight files" + "\n"
            "   Interactive aircraft and date selection" + "\n"
            "/loc2igc \\- Convert location\\.txt to IGC" + "\n"
            "   Generate IGC from recorded locations" + "\n"
            "/overdue \\- List overdue aircraft for SAR" + "\n"
            "   Export as JSON or CSV for rescue teams" + "\n"
            "/quickadd \\- Quick\\-add gliders from live beacons" + "\n"
            "   Select from currently flying aircraft" + "\n"
            "/cancel \\- Cancel current operation" + "\n"
        )
        await update.message.reply_markdown_v2(commands_text)


    async def overdue_command(self, update: Update, context: CallbackContext) -> int:
        """Show list of overdue aircraft for SAR."""
        import json
        logger = logging.getLogger(__name__)
        # JSON structured log
        try:
            user_id = update.effective_user.id if update.effective_user else None
            logger.info(json.dumps({
                "event": "overdue_command",
                "user_id": user_id,
                "chat_id": (update.effective_chat.id if update.effective_chat else None),
                "timestamp": datetime.utcnow().isoformat(),
            }))
        except Exception:
            pass
        if update.message is None:
            return ConversationHandler.END
        # Admin check
        if update.effective_user is None or update.effective_user.id != int(self.admin_id):
            await update.message.reply_markdown_v2("Unauthorized")
            return ConversationHandler.END
        # Retrieve overdue aircraft from OGN client
        overdue = []
        if self.ogn_client and hasattr(self.ogn_client, 'get_overdue_aircraft'):
            overdue = self.ogn_client.get_overdue_aircraft(threshold_minutes=30)
        
        # Filter: only show aircraft registered in names.csv
        self._load_names_df()
        if self.names_df is not None and 'fid' in self.names_df.columns:
            try:
                registered_fids = set(self.names_df['fid'].values)
                overdue = [ac for ac in overdue if ac.get('address', '') in registered_fids]
            except Exception:
                pass
        
        if not overdue:
            await update.message.reply_text("✅ No overdue aircraft. All tracked aircraft are transmitting normally.")
            return ConversationHandler.END

        # Format message
        msg = "📋 Overdue Aircraft Report\n\n"
        for i, ac in enumerate(overdue, 1):
            reg = ac.get('registration', 'Unknown')
            last_pos = ac.get('last_position', {}) or {}
            lat = last_pos.get('lat', 0)
            lon = last_pos.get('lon', 0)
            last_seen = ac.get('last_seen', 'Unknown')
            minutes = ac.get('minutes_overdue', 0)
            address = ac.get('address', '')
            msg += f"{i}. {reg} ({address})\n"
            msg += f"   Last seen: {lat:.4f}, {lon:.4f} @ {last_seen}\n"
            msg += f"   Time since: {minutes} minutes\n\n"

        msg += "[Export JSON] [Export CSV]"

        # Show inline keyboard with export options
        keyboard = [
            [InlineKeyboardButton("Export JSON", callback_data="overdue_export_json"),
             InlineKeyboardButton("Export CSV", callback_data="overdue_export_csv")],
            [InlineKeyboardButton("Cancel", callback_data="cancel")]
        ]
        
        # Use chunked messages to avoid 4096 char limit
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is not None:
            await self._send_chunked_messages(chat_id, msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return OVERDUE_EXPORT

    async def overdue_export(self, update: Update, context: CallbackContext) -> int:
        """Export overdue aircraft list."""
        logger = logging.getLogger(__name__)
        # CallbackQuery based export
        if update.callback_query is None:
            return ConversationHandler.END
        query = update.callback_query
        await query.answer()

        # Admin check
        if (update.effective_user is None) or (update.effective_user.id != int(self.admin_id)):
            await query.edit_message_text("Unauthorized")
            return ConversationHandler.END

        data = (query.data or "").lower()
        if data == "cancel":
            await query.edit_message_text("Cancelled")
            return ConversationHandler.END

        export_format = data.split('_')[-1]  # json or csv
        overdue = []
        if self.ogn_client and hasattr(self.ogn_client, 'get_overdue_aircraft'):
            overdue = self.ogn_client.get_overdue_aircraft(threshold_minutes=30)

        berlin_tz = ZoneInfo("Europe/Berlin")
        from datetime import datetime as _dt
        if export_format == 'json':
            import json
            export_data = {
                "generated_at": _dt.now(berlin_tz).isoformat(),
                "threshold_minutes": 30,
                "aircraft": overdue,
            }
            json_str = json.dumps(export_data, indent=2)
            await query.edit_message_text(text="📄 Exporting overdue aircraft list as JSON...")
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=json_str.encode('utf-8'),
                filename=f"overdue_aircraft_{_dt.now(berlin_tz).strftime('%Y%m%d_%H%M%S')}.json",
            )
        else:  # csv
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['FLARM_ID', 'Registration', 'Latitude', 'Longitude', 'Altitude', 'Last_Seen', 'Minutes_Overdue'])
            for ac in overdue:
                pos = ac.get('last_position', {})
                writer.writerow([
                    ac.get('address', ''),
                    ac.get('registration', ''),
                    pos.get('lat', ''),
                    pos.get('lon', ''),
                    pos.get('alt', ''),
                    ac.get('last_seen', ''),
                    ac.get('minutes_overdue', 0),
                ])
            await query.edit_message_text(text="📄 Exporting overdue aircraft list as CSV...")
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=output.getvalue().encode('utf-8'),
                filename=f"overdue_aircraft_{_dt.now(berlin_tz).strftime('%Y%m%d_%H%M%S')}.csv",
            )
        return ConversationHandler.END

    async def check_geofence_alerts(self) -> None:
        """Check for off-field aircraft that are stationary and alert admin."""
        offline = []
        # Sub-task 1: Ensure names_df loaded before geofence checks
        if self.names_df is None:
            self._load_names_df()
        try:
            if self.ogn_client and hasattr(self.ogn_client, 'get_offline_aircraft'):
                offline = self.ogn_client.get_offline_aircraft(threshold_minutes=Config.GEOFENCE_OFFLINE_THRESHOLD_MINUTES)
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Geofence check failed while fetching offline aircraft: {e}")
            offline = []
        if not hasattr(self, '_alerted_offline'):
            self._alerted_offline = {}
        now = datetime.utcnow() if hasattr(datetime, 'utcnow') else datetime.now()
        cooldown = getattr(Config, 'GEOFENCE_ALERT_COOLDOWN_MINUTES', 30)
        for ac in offline or []:
            address = ac.get('address')
            if not address:
                continue
            # Sub-task 2: Filter: only alert for registered aircraft (in names.csv)
            try:
                if self.names_df is None or 'fid' not in self.names_df.columns:
                    continue
                if address not in self.names_df['fid'].values:
                    continue
            except Exception:
                continue
            last_ts = self._alerted_offline.get(address)
            if last_ts and (now - last_ts).total_seconds() < cooldown * 60:
                continue
            pos = ac.get('last_position', {}) or {}
            lat = pos.get('lat', 0)
            lon = pos.get('lon', 0)
            last_seen = ac.get('last_seen', 'Unknown')
            msg = "⚠️ GEOFENCE ALERT\n\n"
            msg += "Aircraft off-field and stationary\n\n"
            msg += f"FLARM ID: {address}\n"
            msg += f"Last position: {lat:.4f}, {lon:.4f}\n"
            msg += f"Last seen: {last_seen}\n"
            msg += "Status: OFF-FIELD\n\n"
            msg += "Please verify aircraft safety."
            try:
                admin_id = int(Config.load_admin_chat_id())
            except Exception:
                admin_id = None
            if not admin_id:
                logger = logging.getLogger(__name__)
                logger.error("Geofence: admin chat id not configured")
                continue
            bot = None
            if hasattr(self, 'application') and self.application is not None:
                bot = getattr(self.application, 'bot', None)
            if bot is None:
                bot = getattr(self, 'bot', None)
            if bot is None:
                logger = logging.getLogger(__name__)
                logger.error("Geofence: Telegram bot instance not available to send alert")
                continue
            try:
                await bot.send_message(chat_id=admin_id, text=msg, parse_mode='HTML')
                self._alerted_offline[address] = now
                log_line = {
                    "type": "geofence_alert_sent",
                    "address": address,
                    "position": pos,
                    "admin_id": admin_id,
                    "timestamp": now.isoformat()
                }
                logger.info(json.dumps(log_line))
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to send geofence alert: {e}")
        # end for

    async def check_missing_aircraft_alerts(self) -> None:
        """Check for registered aircraft that have disappeared from OGN tracker."""
        # Ensure names_df is loaded
        if self.names_df is None:
            self._load_names_df()
        if self.names_df is None or 'fid' not in self.names_df.columns:
            return
        missing = []
        try:
            if self.ogn_client and hasattr(self.ogn_client, 'get_missing_aircraft'):
                missing = self.ogn_client.get_missing_aircraft(threshold_minutes=Config.MISSING_AIRCRAFT_THRESHOLD_MINUTES)
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Missing aircraft check failed: {e}")
            missing = []
        if not hasattr(self, '_alerted_missing'):
            self._alerted_missing = {}
        now = datetime.utcnow() if hasattr(datetime, 'utcnow') else datetime.now()
        cooldown = getattr(Config, 'MISSING_AIRCRAFT_ALERT_COOLDOWN_MINUTES', 30)
        for ac in missing or []:
            address = ac.get('address')
            if not address:
                continue
            # Filter: only registered aircraft
            try:
                if self.names_df is None or 'fid' not in self.names_df.columns:
                    continue
                if address not in self.names_df['fid'].values:
                    continue
            except Exception:
                continue
            last_ts = self._alerted_missing.get(address)
            if last_ts and (now - last_ts).total_seconds() < cooldown * 60:
                continue
            last_seen = ac.get('last_seen', 'Unknown')
            seconds_ago = ac.get('seconds_ago', 0)
            msg = "🛟 MISSING AIRCRAFT ALERT\n\n"
            msg += "Aircraft has stopped transmitting\n\n"
            msg += f"FLARM ID: {address}\n"
            msg += f"Last seen: {last_seen}\n"
            msg += f"Time since last beacon: {int(seconds_ago / 60)} minutes\n"
            msg += "Status: SIGNAL LOST\n\n"
            msg += "Please verify aircraft safety."
            try:
                admin_id = int(Config.load_admin_chat_id())
            except Exception:
                admin_id = None
            if not admin_id:
                logger = logging.getLogger(__name__)
                logger.error("Missing aircraft: admin chat id not configured")
                continue
            bot = None
            if hasattr(self, 'application') and self.application is not None:
                bot = getattr(self.application, 'bot', None)
            if bot is None:
                bot = getattr(self, 'bot', None)
            if bot is None:
                logger = logging.getLogger(__name__)
                logger.error("Missing aircraft: Telegram bot instance not available")
                continue
            try:
                await bot.send_message(chat_id=admin_id, text=msg, parse_mode='HTML')
                self._alerted_missing[address] = now
                log_line = {
                    "type": "missing_aircraft_alert_sent",
                    "address": address,
                    "last_seen": last_seen,
                    "admin_id": admin_id,
                    "timestamp": now.isoformat()
                }
                logger.info(json.dumps(log_line))
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to send missing aircraft alert: {e}")
    async def add(self, update: Update, context: CallbackContext) -> None:
        """Handle /a command - add a glider nickname."""
        if update.message is None:
            return
        if update.effective_user is None or update.effective_user.id != int(self.admin_id):
            await update.message.reply_markdown_v2("Unauthorized")
            return
        if context.args is None or len(context.args) != 1:
            await update.message.reply_markdown_v2(
                "Usage: /a \\<fid,name\\>" + "\n" + "Example: \\`/a FLR123456,John Doe\\`"
            )
            return
        if len(context.args[0]) > 0 and "," in context.args[0]:
            with open(self.filename, "a", encoding="utf-8") as out:
                out.write(context.args[0] + "\n")
            await update.message.reply_markdown_v2(
                "added " + escape_markdown(context.args[0], version=2)
            )
        else:
            await update.message.reply_markdown_v2(
                "Usage: /a \\<fid,name\\>" + "\n" + "Example: \\`/a FLR123456,John Doe\\`"
            )
    
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
    
    async def status(self, update: Update, context: CallbackContext) -> None:
        """Handle /status command - show server statistics."""
        if update.message is None:
            return
        if update.effective_user is None or update.effective_user.id != int(self.admin_id):
            await update.message.reply_markdown_v2("Unauthorized")
            return
        
        if not self.ogn_client:
            await update.message.reply_text("OGN client not available.")
            return
        
        try:
            status_data = self.ogn_client.get_status()
            
            last_beacon = status_data["last_beacon_received"]
            if last_beacon:
                try:
                    dt = datetime.fromisoformat(last_beacon)
                    last_beacon_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except Exception:
                    last_beacon_str = last_beacon
            else:
                last_beacon_str = "No beacons received"
            
            api_stats = status_data["api_stats"]
            last_sent = api_stats["last_data_sent"]
            if last_sent:
                try:
                    dt = datetime.fromisoformat(last_sent)
                    last_sent_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except Exception:
                    last_sent_str = last_sent
            else:
                last_sent_str = "No data sent yet"
            
            response = (
                "📊 *Server Status*\n\n"
                f"🛰️ Last OGN Beacon: `{last_beacon_str}`\n"
                f"✈️ Current Aircraft: `{status_data['current_aircraft_count']}`\n"
                f"📈 Total Beacons Received: `{status_data['total_beacons_received']}`\n\n"
                f"🌐 Last Data Sent: `{last_sent_str}`\n"
                f"📤 Beacons Sent to Clients: `{api_stats['beacons_sent_count']}`"
            )
            
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.exception("Status command failed")
            await update.message.reply_markdown_v2(f"Error getting status: {escape_markdown(str(e), version=2)}")

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

    # --- Quick Add (bulk add) conversation ---
    async def quickadd_command(self, update: Update, context: CallbackContext) -> int:
        """Scan live beacons and allow quick addition of aircraft."""
        if update.message is None:
            return ConversationHandler.END
        # Admin check
        user_id = update.effective_user.id if update.effective_user else None
        admin_id = int(self.admin_id) if self.admin_id is not None else None
        if user_id != admin_id:
            await update.message.reply_text("Unauthorized. Only admins can add aircraft.")
            return ConversationHandler.END

        # Get all current positions from SAR tracking
        all_positions = {}
        if self.ogn_client and hasattr(self.ogn_client, 'get_all_last_positions'):
            try:
                all_positions = self.ogn_client.get_all_last_positions()
            except Exception:
                all_positions = {}

        if not all_positions:
            await update.message.reply_text("No aircraft currently tracked. Make sure OGN client is connected.")
            return ConversationHandler.END

        # Load existing names.csv if available
        self._load_names_df()

        # Build list of aircraft with DDB info
        aircraft_list = []
        for flarm_id, pos in (all_positions or {}).items():
            reg = None
            lat = pos.get('latitude', pos.get('lat', 0))
            lon = pos.get('longitude', pos.get('lon', 0))
            if isinstance(pos, dict):
                reg = pos.get('registration', None)
            if not reg:
                reg = "Unknown"
            # Skip already registered
            if self.names_df is not None and 'fid' in self.names_df.columns:
                try:
                    if flarm_id in self.names_df['fid'].values:
                        continue
                except Exception:
                    pass
            aircraft_list.append({
                'flarm_id': flarm_id,
                'registration': reg,
                'lat': float(lat) if lat is not None else 0.0,
                'lon': float(lon) if lon is not None else 0.0,
            })

        if not aircraft_list:
            await update.message.reply_text("All currently tracked aircraft are already in names.csv")
            return ConversationHandler.END

        # Store in context for next step
        context.user_data['quickadd_aircraft'] = aircraft_list
        context.user_data['quickadd_selected'] = set()

        # Build inline keyboard with checkboxes
        keyboard = []
        for i, ac in enumerate(aircraft_list):
            btn_text = f"{ac['registration']} ({ac['flarm_id']}) - {ac['lat']:.2f},{ac['lon']:.2f}"
            keyboard.append([InlineKeyboardButton(f"☐ {btn_text}", callback_data=f"quickadd_toggle:{i}")])
        keyboard.append([
            InlineKeyboardButton("✅ Add Selected", callback_data="quickadd_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel")
        ])

        msg = f"🛩️ Quick Add - Currently Flying Aircraft\n\n"
        msg += f"Found {len(aircraft_list)} unregistered aircraft.\n\n"
        msg += f"Tap to select/deselect, then press 'Add Selected':"

        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return QUICKADD_SELECT

    async def quickadd_toggle(self, update: Update, context: CallbackContext) -> int:
        """Toggle aircraft selection in quickadd."""
        query = update.callback_query
        await query.answer()
        if not query.data or not query.data.startswith("quickadd_toggle:"):
            return QUICKADD_SELECT
        idx = int(query.data.split(':')[1])
        aircraft_list = context.user_data.get('quickadd_aircraft', [])
        selected = context.user_data.get('quickadd_selected', set())

        if idx in selected:
            selected.remove(idx)
        else:
            selected.add(idx)

        context.user_data['quickadd_selected'] = selected

        # Rebuild keyboard with updated selections
        keyboard = []
        for i, ac in enumerate(aircraft_list):
            checkbox = "✅" if i in selected else "☐"
            btn_text = f"{ac['registration']} ({ac['flarm_id']}) - {ac['lat']:.2f},{ac['lon']:.2f}"
            keyboard.append([InlineKeyboardButton(f"{checkbox} {btn_text}", callback_data=f"quickadd_toggle:{i}")])
        keyboard.append([
            InlineKeyboardButton(f"✅ Add Selected ({len(selected)})", callback_data="quickadd_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel")
        ])

        await query.edit_message_text(
            text=f"🛩️ Quick Add - Currently Flying Aircraft\n\nSelected {len(selected)} of {len(aircraft_list)}:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return QUICKADD_SELECT

    async def quickadd_confirm(self, update: Update, context: CallbackContext) -> int:
        """Confirm and add selected aircraft to names.csv."""
        query = update.callback_query
        await query.answer()

        aircraft_list = context.user_data.get('quickadd_aircraft', [])
        selected_indices = context.user_data.get('quickadd_selected', set())

        if not selected_indices:
            await query.edit_message_text("No aircraft selected. Operation cancelled.")
            return ConversationHandler.END

        added = []
        for idx in sorted(list(selected_indices)):
            ac = aircraft_list[idx]
            self._add_to_names_csv(ac['flarm_id'], ac['registration'])
            added.append(f"{ac['registration']} ({ac['flarm_id']})")

        msg = f"✅ Successfully added {len(added)} aircraft:\n\n" + "\n".join(added)
        
        # Truncate if message exceeds Telegram's 4096 char limit
        MAX_MESSAGE_LENGTH = 4096
        if len(msg) > MAX_MESSAGE_LENGTH:
            note = f"\n\n(Note: {len(added)} total aircraft added)"
            # Reserve space for the note
            available_space = MAX_MESSAGE_LENGTH - len(note) - 3  # 3 for "..."
            msg = msg[:available_space] + "..." + note
        
        await query.edit_message_text(msg)
        # Clear context
        context.user_data.pop('quickadd_aircraft', None)
        context.user_data.pop('quickadd_selected', None)
        return ConversationHandler.END

    def _load_names_df(self) -> None:
        """Load names.csv into self.names_df if pandas is available."""
        self.names_df = None
        try:
            import pandas as pd  # type: ignore
            names_path = Path(Config.NAMES_FILE)
            if names_path.exists():
                self.names_df = pd.read_csv(Config.NAMES_FILE, names=["fid", "name"], header=0)
            else:
                # Initialize empty dataframe with expected columns
                self.names_df = pd.DataFrame(columns=["fid", "name"])
        except Exception:
            self.names_df = None

    def _add_to_names_csv(self, flarm_id: str, name: str) -> None:
        """Add a single entry to names.csv, avoiding duplicates."""
        try:
            # Lazy load
            self._load_names_df()
            if self.names_df is not None and 'fid' in self.names_df.columns:
                try:
                    if flarm_id in self.names_df['fid'].values:
                        return
                except Exception:
                    pass
                import pandas as pd  # local import
                new_row = pd.DataFrame({'fid': [flarm_id], 'name': [name]})
                self.names_df = pd.concat([self.names_df, new_row], ignore_index=True)
                self.names_df.to_csv(Config.NAMES_FILE, index=False)
                return
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to update names.csv: {e}")
        # Fallback: append plain line
        try:
            with open(Config.NAMES_FILE, 'a', encoding='utf-8') as f:
                f.write(f"{flarm_id},{name}\n")
        except Exception:
            pass

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
        # Store bot reference for _send_chunked_messages helper
        self.bot = self.application.bot
        # Initialize geofence alert scheduler (background task)
        try:
            self.scheduler = AsyncIOScheduler()
            self.scheduler.add_job(
                self.check_geofence_alerts,
                'interval',
                seconds=Config.GEOFENCE_CHECK_INTERVAL_SECONDS,
                id='geofence_alert_check'
            )
            self.scheduler.start()
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to initialize geofence alert scheduler: {e}")
        
        # Initialize missing aircraft alert scheduler
        try:
            self.scheduler.add_job(
                self.check_missing_aircraft_alerts,
                'interval',
                seconds=Config.GEOFENCE_CHECK_INTERVAL_SECONDS,
                id='missing_aircraft_alert_check'
            )
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to initialize missing aircraft alert scheduler: {e}")
        
        add_handler = CommandHandler('a', self.add)
        overdue_handler = CommandHandler('overdue', self.overdue_command)
        quickadd_handler = CommandHandler('quickadd', self.quickadd_command)
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
        status_handler = CommandHandler('status', self.status)
        
        self.application.add_handler(add_handler)
        self.application.add_handler(del_conv_handler)
        self.application.add_handler(refresh_handler)
        self.application.add_handler(start_handler)
        self.application.add_handler(status_handler)
        self.application.add_handler(overdue_handler)
        # Quick Add conversation
        quickadd_conv_handler = ConversationHandler(
            entry_points=[quickadd_handler],
            states={
                QUICKADD_SELECT: [CallbackQueryHandler(self.quickadd_toggle, pattern=r"^quickadd_toggle:.*")],
                QUICKADD_CONFIRM: [CallbackQueryHandler(self.quickadd_confirm, pattern=r"^quickadd_confirm$")],
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel_igc),
                CallbackQueryHandler(self.cancel_igc, pattern="^cancel$"),
            ],
            per_user=True,
            conversation_timeout=CONVERSATION_TIMEOUT,
        )
        self.application.add_handler(quickadd_conv_handler)
        
        # IGC file request conversation
        igc_conv_handler = ConversationHandler(
            entry_points=[CommandHandler('igc', self.igc_command)],
            states={
                SELECTING_AIRCRAFT: [CallbackQueryHandler(self.aircraft_selected, pattern=r"^aircraft:.*")],
                SELECTING_DATE: [
                    CallbackQueryHandler(self.date_selected, pattern=r"^date:.*"),
                    CallbackQueryHandler(self.date_selected, pattern=r"^back$"),
                ],
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel_igc),
                CallbackQueryHandler(self.cancel_igc, pattern="^cancel$"),
            ],
            per_user=True,
            conversation_timeout=CONVERSATION_TIMEOUT,
        )

        self.application.add_handler(igc_conv_handler)

        # Overdue SAR conversation (list + export)
        overdue_conv_handler = ConversationHandler(
            entry_points=[overdue_handler],
            states={
                OVERDUE_SHOW_LIST: [CallbackQueryHandler(self.overdue_export, pattern=r"^overdue_export_.*|^cancel$")],
                OVERDUE_EXPORT: [CallbackQueryHandler(self.overdue_export, pattern=r"^overdue_export_.*|^cancel$")],
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel_igc),
                CallbackQueryHandler(self.cancel_igc, pattern="^cancel$"),
            ],
            per_user=True,
            conversation_timeout=CONVERSATION_TIMEOUT,
        )
        self.application.add_handler(overdue_conv_handler)

        

        # Location to IGC conversion conversation
        loc2igc_conv_handler = ConversationHandler(
            entry_points=[CommandHandler('loc2igc', self.loc2igc_command)],
            states={
                LOC2IGC_SELECT_AIRCRAFT: [
                    CallbackQueryHandler(self.loc2igc_aircraft_selected, pattern=r"^aircraft:.*"),
                    CallbackQueryHandler(self.loc2igc_aircraft_selected, pattern=r"^back$"),
                ],
                LOC2IGC_SELECT_DATE: [
                    CallbackQueryHandler(self.loc2igc_date_selected, pattern=r"^date:.*"),
                    CallbackQueryHandler(self.loc2igc_date_selected, pattern=r"^back$"),
                ],
            },
            fallbacks=[CallbackQueryHandler(self.cancel_igc, pattern=r"^cancel$"), CommandHandler('cancel', self.cancel_igc)],
            per_user=True,
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
