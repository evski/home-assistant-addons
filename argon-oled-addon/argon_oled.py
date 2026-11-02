#!/usr/bin/env python3
"""
Argon ONE OLED Display Service for Home Assistant
Displays system information on the Argon ONE OLED screen
"""

import time
import os
import sys
import re
import struct
import select
import threading
from datetime import datetime
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw, ImageFont
import qrcode
import requests

# Import our modules
from system_info import SystemInfo
from supervisor_api import SupervisorAPI
from screens import ScreenRenderer

try:
    import smbus2
    SMBUS_AVAILABLE = True
except ImportError:
    SMBUS_AVAILABLE = False

try:
    import gpiod
    from gpiod.line import Direction, Value
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    Direction = None
    Value = None

# Configuration
I2C_BUS = 1
I2C_ADDRESS = 0x3C
SCREEN_WIDTH = 128
SCREEN_HEIGHT = 64
SWITCH_DURATION = 30  # seconds between screens
PIN_BUTTON = 4  # BCM Pin 4 for button

# Linux input event values. KEY_POWER is what the Argon ONE V5 case button
# emits through the kernel gpio-keys driver (device name "pwr_button").
EV_KEY = 0x01
KEY_POWER = 116
POWER_BUTTON_NAME = "pwr_button"
POWER_BUTTON_BY_PATH = "/dev/input/by-path/platform-pwr_button-event"
INPUT_DEVICES_PROC = "/proc/bus/input/devices"

# Reading the evdev node is not exclusive. EVIOCGRAB is intentionally not used:
# HAOS logind keeps seeing the key. HandlePowerKey=ignore, so a short press
# does not shut the host down. HandlePowerKeyLongPress=poweroff fires at about
# 5 seconds, which is before this add-on's 10s/15s prompts.
POWER_KEY_SOURCE_WARNING = (
    "WARNING: button_source is 'power_key' but the pwr_button input device "
    "could not be opened. The case button will do nothing. Change the add-on "
    "option button_source to 'gpio' if this case exposes the button as GPIO "
    "line 4, and check that the add-on is allowed to read /dev/input."
)
GPIO_SOURCE_WARNING = (
    "WARNING: button_source is 'gpio' but GPIO line 4 is not available. "
    "The case button will do nothing. Change the add-on option button_source "
    "to 'power_key' on an Argon ONE V5 running Home Assistant OS, where the "
    "button is the kernel pwr_button input device."
)
LOGIND_POWER_KEY_NOTE = (
    "NOTE: button_source 'power_key' does not grab the device (no EVIOCGRAB), "
    "so systemd-logind still receives KEY_POWER. On HAOS a short press is "
    "ignored and does not shut down. A hold of about 5 seconds is "
    "HandlePowerKeyLongPress=poweroff and will power off the host before this "
    "add-on's 10s reboot or 15s shutdown prompt. Set "
    "HandlePowerKeyLongPress=ignore on the host to use those hold gestures."
)


def input_event_format():
    """struct input_event layout for this process's word size."""
    if struct.calcsize("l") == 8:
        return "llHHi"
    return "iiHHi"


def unpack_input_events(buffer, fmt=None):
    """Split a byte buffer into (type, code, value) events.

    Returns the events and any incomplete trailing bytes.
    """
    fmt = fmt or input_event_format()
    size = struct.calcsize(fmt)
    events = []
    offset = 0
    while offset + size <= len(buffer):
        _sec, _usec, ev_type, code, value = struct.unpack_from(fmt, buffer, offset)
        events.append((ev_type, code, value))
        offset += size
    return events, buffer[offset:]


def discover_power_button_paths(proc_text, by_path=POWER_BUTTON_BY_PATH):
    """Find /dev/input/eventN for the pwr_button device.

    The event index is not stable, so it is taken from Handlers= rather than
    hardcoded. The by-path symlink is appended as a second candidate; udev
    names it from the platform device, which stays platform-pwr_button-event.
    """
    paths = []
    name = None
    handlers = ""

    def finish():
        nonlocal name, handlers
        if name == POWER_BUTTON_NAME:
            for token in handlers.split():
                if token.startswith("event") and token[5:].isdigit():
                    candidate = f"/dev/input/{token}"
                    if candidate not in paths:
                        paths.append(candidate)
        name = None
        handlers = ""

    for line in proc_text.splitlines():
        if line.strip() == "":
            finish()
            continue
        if line.startswith("N: Name="):
            raw = line.split("=", 1)[1].strip()
            if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
                raw = raw[1:-1]
            name = raw
        elif line.startswith("H: Handlers="):
            handlers = line.split("=", 1)[1].strip()
    finish()
    if by_path and by_path not in paths:
        paths.append(by_path)
    return paths


class PowerKeyDevice:
    """Non-exclusive reader for KEY_POWER press and release events."""

    def __init__(self, fd, fmt=None):
        self.fd = fd
        self.fmt = fmt or input_event_format()
        self._pending = []
        self._buffer = b""
        self.held = False

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

    def _read_more(self, block):
        if not block:
            readable, _, _ = select.select([self.fd], [], [], 0)
            if not readable:
                return
        try:
            chunk = os.read(self.fd, 1024)
        except BlockingIOError:
            return
        if not chunk:
            raise OSError("power button input device closed")
        self._buffer += chunk
        events, self._buffer = unpack_input_events(self._buffer, self.fmt)
        self._pending.extend(events)

    def _apply(self, event):
        ev_type, code, value = event
        if ev_type != EV_KEY or code != KEY_POWER:
            return
        if value == 1:
            self.held = True
        elif value == 0:
            self.held = False
        # value 2 is autorepeat; the key is still down, so leave held as-is.

    def wait_until_pressed(self):
        """Block until a KEY_POWER press is seen. Do not consume past it."""
        while not self.held:
            if not self._pending:
                self._read_more(block=True)
            if not self._pending:
                continue
            self._apply(self._pending.pop(0))

    def is_down(self):
        """True while the key is held.

        When the key is up, queued events are applied only until the next
        press so a following release stays buffered. When it is already
        down, events are applied until that hold ends.
        """
        self._read_more(block=False)
        if not self.held:
            while self._pending and not self.held:
                self._apply(self._pending.pop(0))
                if self.held:
                    break
            return self.held
        while self._pending and self.held:
            self._apply(self._pending.pop(0))
            if not self.held:
                break
        return self.held


def open_power_button(proc_path=INPUT_DEVICES_PROC):
    """Open the pwr_button evdev node. Returns (path, device) or (None, reason)."""
    try:
        with open(proc_path, "r", encoding="utf-8", errors="replace") as handle:
            proc_text = handle.read()
    except OSError as exc:
        proc_text = ""
        proc_error = str(exc)
    else:
        proc_error = ""
    paths = discover_power_button_paths(proc_text)
    errors = []
    if proc_error:
        errors.append(f"{proc_path}: {proc_error}")
    if not paths:
        errors.append("no pwr_button device listed")
    for path in paths:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        return path, PowerKeyDevice(fd), ""
    return None, None, "; ".join(errors)

# Home Assistant API
SUPERVISOR_TOKEN = os.environ.get('SUPERVISOR_TOKEN', '')
HA_API_URL = 'http://supervisor/core/api'

# How often to re-read the night-sleep helpers. The window itself is checked
# every main-loop pass; this interval is only the Home Assistant read.
NIGHT_SLEEP_HELPER_INTERVAL = 30
# One quicker follow-up when a previously good helper set becomes unusable
# (a dashboard edit can land one entity at a time).
NIGHT_SLEEP_HELPER_RETRY = 5
NIGHT_SLEEP_BOOL_ENTITY = "input_boolean.oled_night_sleep"
NIGHT_SLEEP_START_ENTITY = "input_datetime.oled_night_sleep_start"
NIGHT_SLEEP_END_ENTITY = "input_datetime.oled_night_sleep_end"
NIGHT_SLEEP_ENTITIES = (
    NIGHT_SLEEP_BOOL_ENTITY,
    NIGHT_SLEEP_START_ENTITY,
    NIGHT_SLEEP_END_ENTITY,
)
# Daytime idle blanking. Read on the same pass as the night-sleep helpers.
# It stands alone: a usable on/off state overrides the option even when the
# night-sleep helpers are missing, and the reverse is also true.
SCREEN_OFF_ENTITY = "input_boolean.oled_screen_off_until_button"
_UNUSABLE_STATES = {"unknown", "unavailable", "none", "null"}
# HH:MM, HH:MM:SS, or "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DDTHH:MM:SS".
_CLOCK_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[ T])?([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$"
)


def local_now():
    """Timezone-aware local time.

    Wall-clock fields follow the container timezone, which Home Assistant OS
    sets from the supervisor timezone. DST is whatever that zone says now.
    """
    return datetime.now().astimezone()


def parse_clock(value):
    """Return (hour, minute) from a time-of-day string, or None."""
    if not isinstance(value, str):
        return None
    match = _CLOCK_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def format_hhmm(clock):
    if not clock:
        return "--:--"
    return f"{clock[0]:02d}:{clock[1]:02d}"


def night_window_contains(now, start, end):
    """Whether local `now` is inside [start, end).

    start and end are (hour, minute). start == end is an empty window, not
    a 24-hour sleep. When start is later than end the window crosses
    midnight. The comparison uses the wall clock, so a DST spring-forward
    or fall-back does not stretch the window by elapsed UTC time. A civil
    time that does not exist on a spring-forward morning is simply not
    inside a window that only covers that missing hour. Both occurrences
    of a repeated fall-back hour are inside a window that covers it.
    """
    if start is None or end is None or now is None:
        return False
    if start == end:
        return False
    current = now.hour * 60 + now.minute
    start_m = start[0] * 60 + start[1]
    end_m = end[0] * 60 + end[1]
    if start_m < end_m:
        return start_m <= current < end_m
    return current >= start_m or current < end_m


def parse_on_off(state):
    """Home Assistant on/off. Anything else, including unknown, is unusable."""
    if not isinstance(state, str):
        return None
    value = state.strip().lower()
    if value == "on":
        return True
    if value == "off":
        return False
    return None


def parse_time_entity(payload):
    """Time of day from an input_datetime REST payload.

    Prefers an HH:MM[:SS] state (optional date prefix). Otherwise uses the
    timestamp attribute converted to local time, then hour and minute
    attributes. unknown / unavailable do not fall through to attributes.
    """
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    if isinstance(state, str) and state.strip().lower() in _UNUSABLE_STATES:
        return None
    if isinstance(state, str):
        parsed = parse_clock(state)
        if parsed:
            return parsed
    attributes = payload.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    timestamp = attributes.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        try:
            local = datetime.fromtimestamp(float(timestamp)).astimezone()
        except (OverflowError, OSError, ValueError):
            local = None
        if local is not None:
            return local.hour, local.minute
    hour = attributes.get("hour")
    minute = attributes.get("minute")
    if (
        isinstance(hour, int) and not isinstance(hour, bool)
        and isinstance(minute, int) and not isinstance(minute, bool)
        and 0 <= hour <= 23 and 0 <= minute <= 59
    ):
        return hour, minute
    return None


def parse_helper_override(payloads):
    """Return (enabled, start, end) when all three helpers are usable.

    Any missing, unusable, or unparseable entity returns None so the caller
    uses add-on options for the whole window instead of mixing the two.
    """
    if not isinstance(payloads, dict):
        return None
    bool_payload = payloads.get(NIGHT_SLEEP_BOOL_ENTITY)
    start_payload = payloads.get(NIGHT_SLEEP_START_ENTITY)
    end_payload = payloads.get(NIGHT_SLEEP_END_ENTITY)
    if not all(isinstance(item, dict) for item in (bool_payload, start_payload, end_payload)):
        return None
    enabled = parse_on_off(bool_payload.get("state"))
    start = parse_time_entity(start_payload)
    end = parse_time_entity(end_payload)
    if enabled is None or start is None or end is None:
        return None
    return enabled, start, end


def parse_screen_off_helper(payload):
    """Return True or False when the idle-blanking helper state is on or off.

    None means the entity is missing or unusable, so the caller keeps the
    add-on option. This helper is not grouped with the night-sleep three.
    """
    if not isinstance(payload, dict):
        return None
    return parse_on_off(payload.get("state"))


class ArgonOLED:
    """Argon ONE OLED Display Manager"""
    
    def __init__(self, screen_list, switch_duration=30, temp_unit='C', debug_logging=False, show_credits=True, version="1.0.0", screen_off_until_button=False, idle_dim_timeout=60, button_source="power_key", night_sleep=False, night_sleep_start="22:00", night_sleep_end="06:30"):
        """Initialize the OLED display"""
        self.screen_off_until_button = screen_off_until_button
        self.idle_dim_timeout = idle_dim_timeout
        self.debug_logging = debug_logging
        self.night_sleep_enabled = bool(night_sleep)
        self.night_sleep_start = night_sleep_start or "22:00"
        self.night_sleep_end = night_sleep_end or "06:30"
        self.night_sleep_active = False
        self._night_logged_active = None
        self._schedule_source = None
        self._helper_override = None
        self._screen_off_override = None
        self._idle_blanking_source = None
        self._idle_blanking_applied = None
        self._idle_wake_error_logged = False
        self._last_helper_poll = 0.0
        self._helper_fast_retry = False
        self._night_wake_pending = False
        self._night_wake_error_logged = False
        if button_source not in ("gpio", "power_key"):
            print(f"WARNING: button_source '{button_source}' is not gpio or power_key; using power_key")
            button_source = "power_key"
        self.button_source = button_source
        self._power_key = None
        # True only after a successful SSD1306 display-off (device.hide()).
        self.display_blanked = False
        self.button_held = False  # Physical press in progress; do not blank mid-gesture
        self._panel_lock = threading.Lock()
        self._refresh = threading.Event()
        self.last_activity = time.time()

        try:
            self.serial = i2c(port=I2C_BUS, address=I2C_ADDRESS)
            self.device = ssd1306(self.serial, width=SCREEN_WIDTH, height=SCREEN_HEIGHT)
            self.device.clear()
            # luma's init leaves the panel on (DISPLAYON). hide() sends
            # DISPLAYOFF so a dark boot has no residual OLED glow.
            # Night sleep and idle blanking use the add-on options here.
            # Helper overrides are applied in run() before the credits splash,
            # so a restart inside the window does not draw the logo, and a
            # helper that says the panel should stay on does not boot dark.
            options_window = self.night_sleep_enabled and night_window_contains(
                local_now(),
                parse_clock(self.night_sleep_start),
                parse_clock(self.night_sleep_end),
            )
            if self.screen_off_until_button or options_window:
                self.device.hide()
                self.display_blanked = True
                if self.screen_off_until_button:
                    print(f"OLED panel off until button press (idle {self.idle_dim_timeout}s)")
                if options_window:
                    self.night_sleep_active = True
        except Exception as e:
            print(f"Error initializing OLED: {e}")
            sys.exit(1)
        
        self.screen_list = screen_list
        self.switch_duration = switch_duration
        self.temp_unit = temp_unit
        self.debug_logging = debug_logging
        self.show_credits = show_credits
        self.version = version
        self.credits_shown = False
        self.current_screen = 0
        self.last_switch = time.time()
        self.button_action = None  # For button press communication
        self.power_management_enabled = False  # Will be set after permission check
        self.button_in_power_hold = False  # Track when button is held for reboot/shutdown
        
        # Initialize our modules
        self.system_info = SystemInfo(temp_unit=temp_unit)
        self.supervisor_api = SupervisorAPI(debug_callback=self.debug_log)
        
        # Check if we have permissions for host power management
        self.check_power_permissions()
        
        # Initialize GPIO only when that source is selected. On Argon ONE V5
        # the case button is not this line; requesting it anyway stays high
        # and never reports a press.
        self.gpio_chip = None
        self.gpio_line = None
        if self.button_source == "gpio":
            if GPIO_AVAILABLE:
                # First, list available GPIO chips
                print("Checking for available GPIO chips...")
                import glob
                gpio_chips = sorted(glob.glob('/dev/gpiochip*'))
                if gpio_chips:
                    print(f"Found GPIO chips: {', '.join(gpio_chips)}")
                else:
                    print("No /dev/gpiochip* devices found")
                
                # Try each available GPIO chip
                for chip_path in gpio_chips:
                    try:
                        print(f"Trying to request line {PIN_BUTTON} on {chip_path}...")
                        
                        # Use gpiod.request_lines() with LineSettings
                        self.gpio_line = gpiod.request_lines(
                            chip_path,
                            consumer="argon_oled",
                            config={
                                PIN_BUTTON: gpiod.LineSettings(
                                    direction=Direction.INPUT,
                                    bias=gpiod.line.Bias.PULL_UP
                                )
                            },
                        )
                        print(f"GPIO initialized successfully on {chip_path} pin {PIN_BUTTON}")
                        
                        # Test reading the line
                        val = self.gpio_line.get_value(PIN_BUTTON)
                        print(f"Button initial state: {val}")
                        break
                            
                    except Exception as e:
                        print(f"Failed to request line on {chip_path}: {e}")
                        self.gpio_line = None
                
                if not self.gpio_line:
                    print(GPIO_SOURCE_WARNING)
            else:
                print("gpiod library not available")
                print(GPIO_SOURCE_WARNING)
        else:
            print("Button source is power_key; GPIO line 4 will not be requested")
        
        # Load fonts
        try:
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
            font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        except:
            font_small = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_large = ImageFont.load_default()
        
        # Store fonts as instance variables for confirmation methods
        self.font_small = font_small
        self.font_medium = font_medium
        self.font_large = font_large
        
        fonts = {
            'small': font_small,
            'medium': font_medium,
            'large': font_large
        }
        
        # Load logo image
        from PIL import Image
        logo_image = None
        logo_paths = [
            '/data/logo.png',
            '/data/logo.jpg',
            '/data/logo.bmp',
            '/config/argon_logo.png',
            '/config/argon_logo.jpg',
            '/logo.png',  # Default logo bundled with addon
        ]
        
        for logo_path in logo_paths:
            try:
                if os.path.exists(logo_path):
                    img = Image.open(logo_path)
                    # Convert to monochrome
                    img = img.convert('1')
                    # Resize to fit screen (max 128x64)
                    img.thumbnail((SCREEN_WIDTH, SCREEN_HEIGHT), Image.Resampling.LANCZOS)
                    logo_image = img
                    self.debug_log(f"Loaded logo image from: {logo_path}")
                    break
            except Exception as e:
                self.debug_log(f"Could not load logo from {logo_path}: {e}")
        
        if not logo_image:
            self.debug_log("No logo image could be loaded")
            self.debug_log("Tip: Place logo.png in /data/ or /config/ directory")
        
        # Initialize screen renderer
        self.renderer = ScreenRenderer(
            device=self.device,
            fonts=fonts,
            temp_unit=temp_unit,
            logo_image=logo_image
        )
    
    def debug_log(self, message):
        """Print debug message if debug logging is enabled"""
        if self.debug_logging:
            print(message)
            sys.stdout.flush()

    def _blanking_enabled(self):
        """Panel turns itself off for idle blanking or the night window."""
        return self._effective_screen_off() or self.night_sleep_active

    def _effective_screen_off(self):
        """Idle blanking flag. A usable helper wins; otherwise the option."""
        if self._screen_off_override is not None:
            return bool(self._screen_off_override)
        return bool(self.screen_off_until_button)

    def _note_idle_blanking_source(self, source):
        """Log which idle-blanking value is in use, once per change, at debug level."""
        if source == self._idle_blanking_source:
            return
        self._idle_blanking_source = source
        if source == "homeassistant":
            self.debug_log("Idle blanking source: Home Assistant helpers")
        else:
            self.debug_log("Idle blanking source: add-on options")

    def _note_schedule_source(self, source):
        """Log which schedule is in use, once per change, at debug level."""
        if source == self._schedule_source:
            return
        self._schedule_source = source
        if source == "homeassistant":
            self.debug_log("Night sleep schedule source: Home Assistant helpers")
        else:
            self.debug_log("Night sleep schedule source: add-on options")

    def _effective_schedule(self):
        """(enabled, start, end). Helpers win only when all three parsed."""
        if self._helper_override is not None:
            return self._helper_override
        return (
            bool(self.night_sleep_enabled),
            parse_clock(self.night_sleep_start),
            parse_clock(self.night_sleep_end),
        )

    def _read_helper_entity(self, api, entity):
        """Return (status, payload) for one helper.

        status is "ok", "missing", or "error". An error on one entity does
        not drop the others; the caller decides that per group.
        """
        try:
            status, data = api.get_entity_state(entity)
        except Exception as exc:
            if entity == SCREEN_OFF_ENTITY:
                self.debug_log(f"Idle blanking helper read failed: {exc}")
            else:
                self.debug_log(f"Night sleep helper read failed: {exc}")
            return "error", None
        if status == "error":
            return "error", None
        if status == "ok":
            return "ok", data
        return "missing", None

    def _fetch_helper_override(self):
        """Return (night, screen_off) from one Supervisor pass.

        Each value is a parsed result, None if that helper set was read and
        is unusable, or "error" if its proxy read failed. "error" keeps the
        previous good value for that set only. The night-sleep three still
        have to parse together. The idle-blanking helper does not.
        """
        api = getattr(self, "supervisor_api", None)
        if api is None or not hasattr(api, "get_entity_state"):
            return "error", "error"
        payloads = {}
        night_error = False
        for entity in NIGHT_SLEEP_ENTITIES:
            status, data = self._read_helper_entity(api, entity)
            if status == "error":
                night_error = True
                break
            payloads[entity] = data if status == "ok" else None
        screen_status, screen_data = self._read_helper_entity(api, SCREEN_OFF_ENTITY)
        if night_error:
            night_outcome = "error"
        else:
            night_outcome = parse_helper_override(payloads)
        if screen_status == "error":
            screen_outcome = "error"
        elif screen_status == "ok":
            screen_outcome = parse_screen_off_helper(screen_data)
        else:
            screen_outcome = None
        return night_outcome, screen_outcome

    def _poll_night_sleep_helpers(self, now_ts, force=False):
        """Re-read the night-sleep helpers and the idle-blanking helper.

        Both use this cadence and the same Supervisor proxy. There is no
        second poller. The night window and the idle-blanking flag are
        applied every main-loop pass from the cached values. A transport
        error keeps the last good value for that set only.
        """
        if not force and (now_ts - self._last_helper_poll) < NIGHT_SLEEP_HELPER_INTERVAL:
            return
        self._last_helper_poll = now_ts
        if not SUPERVISOR_TOKEN:
            self._helper_override = None
            self._screen_off_override = None
            self._note_schedule_source("options")
            self._note_idle_blanking_source("options")
            return
        night_outcome, screen_outcome = self._fetch_helper_override()

        night_retry = False
        if night_outcome == "error":
            if self._schedule_source is None:
                self._note_schedule_source(
                    "homeassistant" if self._helper_override is not None else "options"
                )
        elif night_outcome is None:
            had_helpers = self._helper_override is not None
            self._helper_override = None
            self._note_schedule_source("options")
            # A dashboard save can make one helper unreadable for a moment.
            # Check again shortly, once, instead of waiting the full interval.
            if had_helpers and not self._helper_fast_retry:
                night_retry = True
        else:
            self._helper_fast_retry = False
            self._helper_override = night_outcome
            self._note_schedule_source("homeassistant")

        screen_retry = False
        if screen_outcome == "error":
            if self._idle_blanking_source is None:
                self._note_idle_blanking_source(
                    "homeassistant" if self._screen_off_override is not None else "options"
                )
        elif screen_outcome is None:
            had_screen = self._screen_off_override is not None
            self._screen_off_override = None
            self._note_idle_blanking_source("options")
            if had_screen and not self._helper_fast_retry and not night_retry:
                screen_retry = True
        else:
            self._screen_off_override = bool(screen_outcome)
            self._note_idle_blanking_source("homeassistant")

        if night_retry or screen_retry:
            self._last_helper_poll = now_ts - (
                NIGHT_SLEEP_HELPER_INTERVAL - NIGHT_SLEEP_HELPER_RETRY
            )
            self._helper_fast_retry = True

    def _sync_night_sleep(self, now=None):
        """Enter or leave the night window. Log only on a real transition.

        Entering blanks immediately, without waiting out idle_dim_timeout.
        Leaving returns to normal behaviour.
        """
        if now is None:
            now = local_now()
        enabled, start, end = self._effective_schedule()
        active = bool(enabled) and night_window_contains(now, start, end)
        was_active = self.night_sleep_active
        if self._night_logged_active != active:
            if active:
                self.debug_log(
                    f"Night sleep started ({format_hhmm(start)}-{format_hhmm(end)})"
                )
            elif self._night_logged_active is not None or was_active:
                self.debug_log("Night sleep ended")
            self._night_logged_active = active
        # Publish before blanking so a press that lands as the window opens
        # still wakes the panel.
        self.night_sleep_active = active
        if active and not was_active:
            self._night_wake_pending = False
            self._maybe_blank(force=True)
        elif was_active and not active:
            # Effective idle blanking stays dark until the next press.
            # Otherwise the panel comes back on and cycling resumes.
            # The helper wins over the option when it has a usable state.
            self._night_wake_pending = not self._effective_screen_off()
            self._finish_night_wake()
        elif self._night_wake_pending and not active:
            self._finish_night_wake()

    def _finish_night_wake(self):
        """Turn the panel on after the window when it was left dark.

        Retries a failed show() on later passes. The failure is logged once.
        """
        if (
            not self._night_wake_pending
            or self.night_sleep_active
            or self._effective_screen_off()
        ):
            return
        with self._panel_lock:
            if self.button_in_power_hold or self.button_held:
                return
            if not self.display_blanked:
                self._night_wake_pending = False
                return
            try:
                self.device.show()
            except Exception as e:
                if not self._night_wake_error_logged:
                    self.debug_log(f"Error waking display after night sleep: {e}")
                    self._night_wake_error_logged = True
                return
            self.display_blanked = False
            self.last_switch = time.time()
            self.last_activity = time.time()
            self._night_wake_pending = False
            self._night_wake_error_logged = False
            self._refresh.set()

    def _sync_idle_blanking(self, boot=False):
        """Apply the effective idle-blanking flag.

        At boot, a true flag blanks before the credits splash and a false
        flag leaves the panel on, matching the option. A later change to
        true does not blank immediately; the idle timeout starts from that
        moment. A change to false wakes a blanked panel unless the night
        window is holding it dark.
        """
        enabled = self._effective_screen_off()
        previous = self._idle_blanking_applied
        self._idle_blanking_applied = enabled
        if boot and previous is None:
            if self.night_sleep_active:
                return
            if enabled and not self.display_blanked:
                self._maybe_blank(force=True)
            elif not enabled and self.display_blanked:
                self._wake_if_idle_blanked()
            return
        if previous is None or previous == enabled:
            # First live observation is not a transition. Keep retrying a
            # wake that failed after idle blanking was turned off.
            if previous is not None and not enabled:
                self._wake_if_idle_blanked()
            return
        if enabled:
            # False to true during the day. Do not hide on this pass.
            if not self.night_sleep_active:
                self.last_activity = time.time()
            return
        self._wake_if_idle_blanked()

    def _wake_if_idle_blanked(self):
        """Turn the panel on when idle blanking is off and night sleep is not holding it.

        A failed show() is retried on a later pass. The failure is logged once.
        """
        if self.night_sleep_active or self._effective_screen_off():
            return
        if not self.display_blanked:
            return
        with self._panel_lock:
            if self.button_in_power_hold or self.button_held:
                return
            if not self.display_blanked:
                return
            try:
                self.device.show()
            except Exception as e:
                if not self._idle_wake_error_logged:
                    self.debug_log(f"Error waking display after idle blanking turned off: {e}")
                    self._idle_wake_error_logged = True
                return
            self.display_blanked = False
            # The panel may have been dark longer than switch_duration.
            # Restart the interval so the wake itself does not also advance
            # the screen, then keep rotating from here.
            self.last_switch = time.time()
            self.last_activity = time.time()
            self._idle_wake_error_logged = False
            self._refresh.set()

    def _on_button_activity(self, end_hold=False):
        """Reset the idle timer and wake a blanked panel.

        Called on the leading edge of a press and again when it is released.
        This is not a gesture. press_count, pulsetime and last_press_time
        stay entirely inside button_monitor, so the press that lights the
        panel is still classified afterwards as next, previous, first
        screen, or a 10s/15s power hold, including the first click of a
        double press.

        end_hold clears the physical-hold flag in the same critical section
        as the timer update. Otherwise a hold longer than idle_dim_timeout
        could hide() in the gap before the release was recorded.
        """
        if not end_hold and not self._blanking_enabled():
            return
        with self._panel_lock:
            if end_hold:
                self.button_held = False
            if not self._blanking_enabled():
                return
            self.last_activity = time.time()
            if not self.display_blanked:
                return
            try:
                self.device.show()
            except Exception as e:
                self.debug_log(f"Error waking display: {e}")
                return
            self.display_blanked = False
            # The panel may have been dark longer than switch_duration.
            # Restart the interval so auto-rotation does not also advance
            # the screen on top of whatever this press classifies as.
            self.last_switch = time.time()
            self.debug_log("Panel woken by button")
            self._refresh.set()

    def _maybe_blank(self, force=False):
        """Turn the panel off with display-off after the idle timeout.

        Does nothing when neither idle blanking nor night sleep is active,
        the button is held, or a power confirmation is on screen. force=True
        blanks as the night window opens, without waiting out the idle
        timeout. Safe to call from the main loop; the button thread is not
        waited on.
        """
        if not self._blanking_enabled():
            return False
        with self._panel_lock:
            if self.display_blanked or self.button_held or self.button_in_power_hold:
                return False
            if not force and time.time() - self.last_activity < self.idle_dim_timeout:
                return False
            try:
                self.device.hide()
            except Exception as e:
                self.debug_log(f"Error blanking display: {e}")
                return False
            self.display_blanked = True
            if not force:
                self.debug_log("Panel blanked after idle timeout")
            return True

    def _ensure_panel_on_for_power_ui(self):
        """Wake the panel and pause rotation before reboot/shutdown UI.

        A hold that started while the panel was dark must still draw its
        "release to confirm" message and the 5-second countdown on a panel
        that is actually on. This applies whichever mode blanked it, including
        night sleep with screen_off_until_button left off. Does not change
        gesture timing or classification.
        """
        with self._panel_lock:
            self.button_in_power_hold = True
            if not self.display_blanked:
                return
            self.last_activity = time.time()
            try:
                self.device.show()
            except Exception as e:
                self.debug_log(f"Error waking display for power prompt: {e}")
                return
            self.display_blanked = False
            self.last_switch = time.time()
            self.debug_log("Panel woken for power confirmation")
            self._refresh.set()

    def _restore_after_power_ui(self):
        """Leave the panel awake with a fresh idle timer after a power prompt ends."""
        with self._panel_lock:
            if self._blanking_enabled():
                self.last_activity = time.time()
                if self.display_blanked:
                    try:
                        self.device.show()
                    except Exception as e:
                        self.debug_log(f"Error waking display: {e}")
                    else:
                        self.display_blanked = False
                        self.last_switch = time.time()
                        self._refresh.set()
            # Same point the confirmation path used to clear this flag,
            # so screen rotation resumes when the prompt is finished.
            self.button_in_power_hold = False

    def _button_is_down(self):
        """Whether the selected button source is currently held.

        Returns None when that source is not open. GPIO is active-low.
        """
        if self.button_source == "power_key" and self._power_key is not None:
            return self._power_key.is_down()
        if self.gpio_line and Value is not None:
            return self.gpio_line.get_value(PIN_BUTTON) == Value.INACTIVE
        return None
    
    def _draw_confirmation_countdown(self, action_name):
        """Draw and handle confirmation countdown for power actions
        Returns: True if cancelled, False if confirmed
        """
        cancelled = False
        button_was_released = False
        
        self.debug_log(f"Starting {action_name} confirmation countdown...")
        # Countdown pixels must not be written while the panel is off.
        self._ensure_panel_on_for_power_ui()
        
        for countdown in range(5, 0, -1):
            # Draw countdown with progress bar
            with canvas(self.device) as draw:
                draw.rectangle(self.device.bounding_box, outline="white", fill="black")
                draw.text((10 if action_name == "SHUTDOWN" else 15, 10), f"{action_name}?", fill="white", font=self.font_large)
                draw.text((5, 35), f"Confirm in {countdown}s", fill="white", font=self.font_small)
                draw.text((5, 48), "Press to cancel", fill="white", font=self.font_small)
                
                # Progress bar
                bar_width = int((countdown / 5.0) * 118)
                draw.rectangle((5, 57, 122, 62), outline=255)
                draw.rectangle((5, 57, 5 + bar_width, 62), fill=255)
            
            # Check for button press to cancel. Require a release first so the
            # tail of the hold that opened this prompt is not itself a cancel.
            for i in range(10):  # Check 10 times per second
                try:
                    held = self._button_is_down()
                    if held is None:
                        pass
                    else:
                        if countdown == 5 and i < 3:
                            self.debug_log(f"Button held: {held}, released: {button_was_released}")
                        if not held:
                            if not button_was_released:
                                self.debug_log("Button released, ready to detect cancel press")
                            button_was_released = True
                        elif button_was_released:
                            self.debug_log(f"{action_name} cancelled by button press")
                            cancelled = True
                            # Awake, idle timer restarted, then the flag drops
                            # so the main loop may draw again.
                            self._restore_after_power_ui()
                            with canvas(self.device) as draw:
                                draw.rectangle(self.device.bounding_box, outline="white", fill="black")
                                draw.text((20, 20), "CANCELLED", fill="white", font=self.font_large)
                            time.sleep(2)
                            break
                except Exception as e:
                    self.debug_log(f"Button check error: {e}")
                time.sleep(0.1)
            
            if cancelled:
                break
        
        return cancelled
    
    def _execute_power_command(self, command, display_name):
        """Execute a power command (reboot or shutdown) via Supervisor API"""
        self.debug_log(f"{display_name.upper()}: Executing {command} command")
        self._ensure_panel_on_for_power_ui()
        
        # Display executing message
        with canvas(self.device) as draw:
            draw.rectangle(self.device.bounding_box, outline="white", fill="black")
            draw.text((10 if command == "shutdown" else 15, 20), display_name, fill="white", font=self.font_large)
            draw.text((20, 45), "Please wait...", fill="white", font=self.font_small)
        time.sleep(1)
        
        # Use Supervisor API
        response = self.supervisor_api.request(f'host/{command}', method='POST', timeout=10)
        
        if response:
            self.debug_log(f"{display_name} response status: {response.status_code}")
            self.debug_log(f"{display_name} response body: {response.text}")
            
            if response.status_code not in [200, 202]:
                self.debug_log(f"WARNING: Unexpected status code: {response.status_code}")
        
        # Clear screen immediately after command
        self.device.clear()
        time.sleep(2)
        sys.exit(0)
    
    def check_power_permissions(self):
        """Check if we have permissions to reboot/shutdown the host"""
        if not SUPERVISOR_TOKEN:
            print("WARNING: No supervisor token available - power management disabled")
            self.power_management_enabled = False
            return
        
        # Try to get supervisor info to check our permissions
        response = self.supervisor_api.request('supervisor/info')
        
        if response and response.status_code == 200:
            # We have at least some API access
            # Now test if we can access host endpoints (requires manager role)
            test_response = self.supervisor_api.request('host/info')
            
            if test_response and test_response.status_code == 200:
                print("✓ Power management enabled - addon has host control permissions")
                self.power_management_enabled = True
            elif test_response and test_response.status_code == 403:
                print("⚠ Power management disabled - addon lacks 'manager' role permissions")
                print("  To enable reboot/shutdown via button, set 'hassio_role: manager' in config.yaml")
                self.power_management_enabled = False
            else:
                print(f"⚠ Power management disabled - unexpected response")
                self.power_management_enabled = False
        else:
            print(f"⚠ Power management disabled - cannot access supervisor API")
            self.power_management_enabled = False
    
    def display_screen(self, screen_name):
        """Display a specific screen"""
        # device.display() updates SSD1306 RAM without sending DISPLAYON,
        # but a frame must not be composed while the panel is meant to be off.
        if self.display_blanked:
            return
        # Delegate to renderer with system_info and supervisor_api
        if screen_name == "clock":
            self.renderer.draw_clock()
        elif screen_name == "cpu":
            self.renderer.draw_cpu(self.system_info)
        elif screen_name == "ram":
            self.renderer.draw_ram(self.system_info)
        elif screen_name == "storage":
            self.renderer.draw_storage(self.system_info)
        elif screen_name == "temp":
            self.renderer.draw_temp(self.system_info)
        elif screen_name == "fan":
            self.renderer.draw_fan(self.system_info)
        elif screen_name == "ip":
            self.renderer.draw_ip(self.supervisor_api)
        elif screen_name == "qr":
            self.renderer.draw_qr(self.supervisor_api)
        elif screen_name == "hastatus" or screen_name == "status":
            self.renderer.draw_ha_status(self.supervisor_api)
        elif screen_name == "logo" or screen_name == "logo1v5":
            self.renderer.draw_logo()
        else:
            # Handle unknown screen locally
            img = Image.new('1', (SCREEN_WIDTH, SCREEN_HEIGHT))
            draw = ImageDraw.Draw(img)
            draw.text((5, 25), f"Unknown: {screen_name}", font=self.font_small, fill=255)
            self.device.display(img)
    
    def _await_release(self, press_start, is_down):
        """Block until the button is released, drawing power prompts on the way.

        Wake/idle handling runs here and does not touch press_count, pulsetime,
        or last_press_time. is_down() is true while the button is held.
        """
        reboot_displayed = False
        shutdown_displayed = False
        # Wake is a side effect of this press, not a new gesture.
        self.button_held = True
        try:
            self._on_button_activity()
            while is_down():
                hold_time = time.time() - press_start
                
                # Display shutdown message if held for 15+ seconds
                if hold_time >= 15.0 and not shutdown_displayed:
                    self.debug_log("Button: 15 seconds - will shutdown on release")
                    self._ensure_panel_on_for_power_ui()
                    with canvas(self.device) as draw:
                        draw.rectangle(self.device.bounding_box, outline="white", fill="black")
                        draw.text((10, 15), "SHUTDOWN", fill="white", font=self.font_large)
                        draw.text((5, 40), "Release to confirm", fill="white", font=self.font_small)
                    shutdown_displayed = True
                
                # Display reboot message if held for 10+ seconds (but less than 15)
                elif hold_time >= 10.0 and not reboot_displayed and not shutdown_displayed:
                    self.debug_log("Button: 10 seconds - will reboot on release")
                    self._ensure_panel_on_for_power_ui()
                    with canvas(self.device) as draw:
                        draw.rectangle(self.device.bounding_box, outline="white", fill="black")
                        draw.text((15, 15), "REBOOTING", fill="white", font=self.font_large)
                        draw.text((5, 40), "Release to confirm", fill="white", font=self.font_small)
                    reboot_displayed = True
                
                time.sleep(0.1)
        finally:
            # Cover the whole physical hold, then start the idle timeout
            # from the release rather than from the press.
            self._on_button_activity(end_hold=True)

    def _sample_second_click(self, last_press_time, press_count, is_down):
        """Watch until 0.5s after the previous release for a second click.

        The old code slept 0.3s here without reading the button, so a second
        press could not increment press_count. A click that is still held
        when that window closes is left for the next gesture.
        """
        deadline = last_press_time + 0.5
        while time.time() < deadline:
            if is_down():
                while is_down() and time.time() < deadline:
                    time.sleep(0.05)
                if not is_down() and time.time() < deadline:
                    press_count += 1
                break
            time.sleep(0.05)
        return press_count

    def _handle_completed_press(self, press_start, press_end, last_press_time, press_count, is_down):
        """Power prompts and the single/double/long classifier. Same for both sources."""
        total_hold = press_end - press_start
        pulsetime = int(total_hold * 10)
        
        # Keep power hold flag set during confirmation to prevent screen rotation
        # It will be cleared after cancel or execution
        
        # Execute action based on total hold time (only if power management is enabled)
        if total_hold >= 15.0 and self.power_management_enabled:
            self.debug_log("Button released after 15+ seconds - shutdown selected")
            cancelled = self._draw_confirmation_countdown("SHUTDOWN")
            if not cancelled:
                self._execute_power_command("shutdown", "SHUTDOWN")
        
        elif total_hold >= 15.0 and not self.power_management_enabled:
            # User tried to shutdown but we don't have permissions
            self.debug_log("Button held for shutdown but power management is disabled")
            self._ensure_panel_on_for_power_ui()
            with canvas(self.device) as draw:
                draw.rectangle(self.device.bounding_box, outline="white", fill="black")
                draw.text((15, 15), "NO PERMISSION", fill="white", font=self.font_medium)
                draw.text((5, 35), "Need manager role", fill="white", font=self.font_small)
            time.sleep(3)
            if self._blanking_enabled():
                # The power-hold flag would otherwise stay set and
                # the idle blanker would never run again. When neither
                # blanking mode is on, leave the flag as the previous code did.
                self._restore_after_power_ui()
        
        elif total_hold >= 10.0 and self.power_management_enabled:
            self.debug_log("Button released after 10+ seconds - reboot selected")
            cancelled = self._draw_confirmation_countdown("REBOOT")
            if not cancelled:
                self._execute_power_command("reboot", "REBOOTING")
        
        elif total_hold >= 10.0 and not self.power_management_enabled:
            # User tried to reboot but we don't have permissions
            self.debug_log("Button held for reboot but power management is disabled")
            self._ensure_panel_on_for_power_ui()
            with canvas(self.device) as draw:
                draw.rectangle(self.device.bounding_box, outline="white", fill="black")
                draw.text((15, 15), "NO PERMISSION", fill="white", font=self.font_medium)
                draw.text((5, 35), "Need manager role", fill="white", font=self.font_small)
            time.sleep(3)
            if self._blanking_enabled():
                self._restore_after_power_ui()
        
        # A press that woke the panel falls through to here. Do not clear
        # press_count before this comparison: that would turn a double press
        # into a single "next".
        if press_end - last_press_time < 0.5:
            press_count += 1
        else:
            press_count = 1
        
        last_press_time = press_end
        press_count = self._sample_second_click(last_press_time, press_count, is_down)
        
        if press_count >= 2:
            # Double press - go back
            self.debug_log("Button: Double press detected - previous screen")
            self.button_action = "prev"
            press_count = 0
        elif pulsetime >= 6:
            # Long press - go to first screen (~0.6s)
            self.debug_log("Button: Long press detected - first screen")
            self.button_action = "first"
            press_count = 0
        elif press_count == 1:
            # Single press - next screen
            self.debug_log("Button: Single press detected - next screen")
            self.button_action = "next"
            press_count = 0
        return last_press_time, press_count

    def _monitor_level(self, is_down, source_name):
        """Shared press loop. is_down() is true while the chosen source is held."""
        self.debug_log(f"Button monitor thread started ({source_name})")
        last_press_time = 0
        press_count = 0
        try:
            while True:
                try:
                    if not is_down():
                        time.sleep(0.1)
                        continue
                    press_start = time.time()
                    self._await_release(press_start, is_down)
                    press_end = time.time()
                except Exception as read_error:
                    self.debug_log(f"Error reading {source_name} button: {read_error}")
                    time.sleep(1)
                    continue
                last_press_time, press_count = self._handle_completed_press(
                    press_start, press_end, last_press_time, press_count, is_down
                )
        except Exception as e:
            self.debug_log(f"Button monitor error: {e}")

    def button_monitor(self):
        """Monitor button presses in separate thread"""
        if self.button_source == "power_key":
            path, device, reason = open_power_button()
            if device is None:
                detail = f" ({reason})" if reason else ""
                print(f"{POWER_KEY_SOURCE_WARNING}{detail}")
                return
            self._power_key = device
            print(f"Button source power_key: reading KEY_POWER from {path}")
            print(LOGIND_POWER_KEY_NOTE)
            self._monitor_power_key(device)
            return
        
        if not self.gpio_line:
            print(GPIO_SOURCE_WARNING)
            return
        # GPIO line is active-low: INACTIVE (0) while held, ACTIVE when released.
        self._monitor_level(
            lambda: self.gpio_line.get_value(PIN_BUTTON) == Value.INACTIVE,
            "gpio",
        )

    def _monitor_power_key(self, device):
        """Block on KEY_POWER edges, then use the same gesture handler as GPIO."""
        self.debug_log("Button monitor thread started (power_key)")
        last_press_time = 0
        press_count = 0
        try:
            while True:
                try:
                    device.wait_until_pressed()
                    press_start = time.time()
                    self._await_release(press_start, device.is_down)
                    press_end = time.time()
                except Exception as read_error:
                    self.debug_log(f"Error reading power_key button: {read_error}")
                    time.sleep(1)
                    continue
                last_press_time, press_count = self._handle_completed_press(
                    press_start, press_end, last_press_time, press_count, device.is_down
                )
        except Exception as e:
            self.debug_log(f"Button monitor error: {e}")
        finally:
            device.close()
            self._power_key = None
    
    def cleanup(self):
        """Clean up and clear display"""
        try:
            if self._power_key:
                self._power_key.close()
                self._power_key = None
        except Exception:
            pass
        try:
            self.device.clear()
            self.device.cleanup()
        except:
            pass
    
    def _service_once(self, current_time):
        """One pass of blanking, button actions, rotation and drawing.

        Auto-rotation and screen draws are skipped while the panel is off
        so a stale switch interval cannot paint a frame after hide().
        Night sleep is re-checked every pass. The night-sleep helpers and
        the idle-blanking helper are re-read on the same slower cadence.
        """
        self._poll_night_sleep_helpers(current_time)
        self._sync_night_sleep()
        self._sync_idle_blanking()

        if self._blanking_enabled():
            self._maybe_blank()

        # Check for button actions
        if self.button_action:
            if self.button_action == "next":
                self.current_screen = (self.current_screen + 1) % len(self.screen_list)
            elif self.button_action == "prev":
                self.current_screen = (self.current_screen - 1) % len(self.screen_list)
            elif self.button_action == "first":
                self.current_screen = 0
            self.button_action = None
            self.last_switch = current_time

        # Switch screen if needed (auto-rotation) - but not during power hold
        # and not while the panel is hardware-off.
        elif current_time - self.last_switch >= self.switch_duration and not self.button_in_power_hold and not self.display_blanked:
            self.current_screen = (self.current_screen + 1) % len(self.screen_list)
            self.last_switch = current_time

        # Display current screen - but not during power hold or while blanked.
        # Blank again immediately before the draw so this pass cannot both
        # turn the panel off and then paint a screen.
        if not self.button_in_power_hold and not self.display_blanked:
            if self._blanking_enabled():
                self._maybe_blank()
            if not self.button_in_power_hold and not self.display_blanked:
                screen_name = self.screen_list[self.current_screen]
                self.display_screen(screen_name)

    def run(self):
        """Main loop"""
        self.debug_log(f"Starting Argon OLED Display")
        self.debug_log(f"Screen rotation: {' -> '.join(self.screen_list)}")
        self.debug_log(f"Switch duration: {self.switch_duration}s")
        self.debug_log(f"Temperature unit: {self.temp_unit}")
        self.debug_log(f"Screen off until button: {self.screen_off_until_button}")
        if self.screen_off_until_button or self.night_sleep_enabled:
            self.debug_log(f"Idle dim timeout: {self.idle_dim_timeout}s")
        self.debug_log(f"Button source: {self.button_source}")
        self.debug_log(
            f"Night sleep: {self.night_sleep_enabled} "
            f"({self.night_sleep_start}-{self.night_sleep_end})"
        )
        # Helpers can move the window and the idle-blanking flag relative
        # to the options used in __init__. Resolve both before the credits
        # splash so a restart inside the window does not flash the logo,
        # and so a helper that says the panel should stay on does not boot dark.
        self._poll_night_sleep_helpers(time.time(), force=True)
        self._sync_night_sleep()
        self._sync_idle_blanking(boot=True)

        # Show credits splash screen if enabled. Skip it when the panel
        # is configured to boot dark, otherwise the splash would light it.
        if self.show_credits and not self.credits_shown and not self.display_blanked:
            self.debug_log("Displaying credits splash screen")
            self.renderer.draw_credits(version=self.version)
            self.credits_shown = True
            time.sleep(5)  # Show for 5 seconds
        elif self.show_credits and not self.credits_shown and self.display_blanked:
            self.debug_log("Skipping credits splash so the panel stays off")
            self.credits_shown = True
        
        # Always start the monitor. It warns and returns if the chosen
        # source cannot be opened, instead of quietly doing nothing.
        button_thread = threading.Thread(target=self.button_monitor, daemon=True)
        button_thread.start()
        self.debug_log("Button monitoring thread started")
        
        loop_count = 0
        try:
            while True:
                # Log heartbeat for first 10 loops
                if loop_count < 10:
                    self.debug_log(f"[MAIN LOOP] Iteration {loop_count}")
                
                loop_count += 1
                current_time = time.time()
                self._service_once(current_time)

                if self._blanking_enabled() and self.display_blanked:
                    # Return as soon as the button thread turns the panel on.
                    self._refresh.wait(timeout=1.0)
                    self._refresh.clear()
                else:
                    time.sleep(1)
        
        except KeyboardInterrupt:
            print("\nShutting down...")
        except Exception as e:
            print(f"Error in main loop: {e}")
        finally:
            self.cleanup()


def main():
    """Main entry point"""
    # Get configuration from environment variables
    screen_list_str = os.environ.get('SCREEN_LIST', 'logo clock cpu storage ram temp fan ip')
    screen_list = screen_list_str.split()
    
    switch_duration = int(os.environ.get('SWITCH_DURATION', '30'))
    temp_unit = os.environ.get('TEMP_UNIT', 'C')
    debug_logging = os.environ.get('DEBUG_LOGGING', 'false').lower() in ('true', '1', 'yes')
    show_credits = os.environ.get('SHOW_CREDITS', 'true').lower() in ('true', '1', 'yes')
    screen_off_until_button = os.environ.get('SCREEN_OFF_UNTIL_BUTTON', 'false').lower() in ('true', '1', 'yes')
    idle_dim_timeout = int(os.environ.get('IDLE_DIM_TIMEOUT', '60'))
    button_source = os.environ.get('BUTTON_SOURCE', 'power_key').strip().lower()
    night_sleep = os.environ.get('NIGHT_SLEEP', 'false').lower() in ('true', '1', 'yes')
    night_sleep_start = os.environ.get('NIGHT_SLEEP_START', '22:00').strip() or "22:00"
    night_sleep_end = os.environ.get('NIGHT_SLEEP_END', '06:30').strip() or "06:30"
    
    # Get version from environment (set by run.sh from config.yaml)
    version = os.environ.get('ADDON_VERSION', '1.0.0')
    
    # Create and run the OLED display
    oled = ArgonOLED(
        screen_list,
        switch_duration,
        temp_unit,
        debug_logging,
        show_credits,
        version,
        screen_off_until_button,
        idle_dim_timeout,
        button_source,
        night_sleep,
        night_sleep_start,
        night_sleep_end,
    )
    oled.run()


if __name__ == '__main__':
    main()
