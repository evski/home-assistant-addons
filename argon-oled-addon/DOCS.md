# Argon ONE OLED Home Assistant Add-on

This is a Home Assistant add-on for the Argon ONE case OLED display.

## Development

### Building locally

```bash
docker build -t argon-oled-test .
```

### Testing

```bash
docker run --rm -it \
  --device /dev/i2c-1 \
  -e TEMP_UNIT=C \
  -e SWITCH_DURATION=30 \
  -e SCREEN_LIST="logo clock cpu ram" \
  argon-oled-test
```

### Debugging

To debug the Python script:

```bash
python3 argon_oled.py
```

Make sure to set environment variables before running:

```bash
export TEMP_UNIT=C
export SWITCH_DURATION=30
export SCREEN_LIST="logo clock cpu ram"
export DEBUG_LOGGING=true
export SHOW_CREDITS=true
export SCREEN_OFF_UNTIL_BUTTON=false
export IDLE_DIM_TIMEOUT=60
export BUTTON_SOURCE=power_key
export ADDON_VERSION=1.18.0
export SUPERVISOR_TOKEN=your_token_here
python3 argon_oled.py
```

For unit testing without hardware:

```bash
# Test a specific module
python -m unittest tests.test_system_info -v

# Test all modules
python -m unittest discover -s tests -v
```

## Project Structure

```
.
├── argon_oled.py          # Main orchestrator (538 lines)
├── system_info.py         # System metrics collection module
├── supervisor_api.py      # Home Assistant Supervisor API client
├── screens.py             # Screen rendering module (all draw methods)
├── run.sh                 # Entry point script
├── Dockerfile             # Docker build configuration
├── config.yaml            # Add-on configuration
├── build.yaml             # Build configuration for multiple architectures
├── apparmor.txt           # AppArmor security profile
├── README.md              # User documentation
├── CHANGELOG.md           # Version history
├── DOCS.md                # This file
├── LICENSE                # License information
└── tests/                 # Unit test suite
    ├── test_system_info.py
    ├── test_supervisor_api.py
    ├── test_screens.py
    ├── run_tests.sh
    └── README_TESTING.md
```

## Modular Architecture

The addon uses a modular design:

- **argon_oled.py** - Main entry point, handles:
  - Screen rotation logic
  - Button monitoring (GPIO)
  - Power management (reboot/shutdown)
  - Credits splash screen on startup
  
- **system_info.py** - System metrics:
  - CPU usage and temperature
  - Memory usage (used, total, percentage)
  - Disk usage (used, total, percentage)
  
- **supervisor_api.py** - HA integration:
  - IP address detection
  - Home Assistant URL
  - System status (updates, backups)
  - Host reboot/shutdown commands
  
- **screens.py** - All rendering logic:
  - 10 different screen types
  - Progress bar rendering with custom units
  - 7-segment digit drawing
  - QR code generation

## Testing

The addon includes comprehensive unit tests:

```bash
# Run all tests
cd tests
./run_tests.sh

# Or use Python directly
cd argon-oled-addon
python -m unittest discover -s tests -v
```

Tests cover:
- System information collection
- Supervisor API interactions
- Screen rendering logic
- Error handling and edge cases

Tests are hardware-independent using mocks, allowing development on Windows, Linux, or Mac without physical Raspberry Pi or OLED display.

## Configuration Options

`run.sh` reads each option with `bashio::config` and exports it for `argon_oled.py`:

| Option | Environment variable | Default |
|--------|----------------------|---------|
| `temp_unit` | `TEMP_UNIT` | `C` |
| `switch_duration` | `SWITCH_DURATION` | `30` |
| `screen_list` | `SCREEN_LIST` | `logo qr hastatus clock cpu storage ram temp ip` |
| `debug_logging` | `DEBUG_LOGGING` | `false` |
| `show_credits` | `SHOW_CREDITS` | `true` |
| `screen_off_until_button` | `SCREEN_OFF_UNTIL_BUTTON` | `false` |
| `idle_dim_timeout` | `IDLE_DIM_TIMEOUT` | `60` |
| `button_source` | `BUTTON_SOURCE` | `power_key` |
| `night_sleep` | `NIGHT_SLEEP` | `false` |
| `night_sleep_start` | `NIGHT_SLEEP_START` | `22:00` |
| `night_sleep_end` | `NIGHT_SLEEP_END` | `06:30` |

`screen_off_until_button` keeps the SSD1306 off (`device.hide()` / `device.show()`) until the case button is pressed, then blanks it again after `idle_dim_timeout` seconds without a press. Button gestures are not remapped; the waking press still runs through the single / double / long / power-hold logic. Reboot and shutdown prompts turn the panel on before they draw.

`night_sleep` blanks the panel on a local-time schedule (default 22:00–06:30, and off until enabled). The start time turns the panel off immediately. After that it matches button-idle mode: a press wakes the panel and still performs that press (next, previous, first screen, or a 10s/15s power hold), then `idle_dim_timeout` blanks it again. The window keeps doing that until it ends. A press does not disable the window for the rest of the night. A start later than the end crosses midnight. Identical start and end times are an empty window. Times are compared as wall-clock hours and minutes, so a DST change does not stretch the window by elapsed UTC time.

When the window ends, normal behaviour returns. With `screen_off_until_button` on, a dark panel stays dark until the next press. Otherwise the panel turns on and cycling resumes. A start or restart inside the window boots dark and skips the credits splash. Power prompts call `device.show()` before they draw.

`idle_dim_timeout` applies both to `screen_off_until_button` and to an active night-sleep window.

Supervisor's add-on schema has no time-of-day type (the supported types are `str`, `bool`, `int`, `float`, `email`, `url`, `password`, `port`, `match(regex)`, `list(...)`, and `device`). `night_sleep_start` and `night_sleep_end` are therefore strings constrained with `match(^([01][0-9]|2[0-3]):[0-5][0-9]$)`. The configuration UI describes them as 24-hour HH:MM via `translations/en.yaml`. A dashboard time picker is available through the helpers below.

### Night sleep helpers

The add-on reads three Home Assistant helpers and, when all three are usable, uses them instead of the add-on options. That is the way to toggle night sleep and pick the times from a dashboard without restarting the add-on. `homeassistant_api` is enabled so the add-on can call the Supervisor proxy:

`GET http://supervisor/core/api/states/<entity_id>` with `Authorization: Bearer $SUPERVISOR_TOKEN`.

It re-reads the three states about every 30 seconds from the main loop and caches the last good values. A change applies on the next read. If a previously good set becomes unreadable for a moment, it checks once more after about 5 seconds. The window itself is evaluated every loop from that cache, so 22:00 does not wait for the next helper read.

Create them under **Settings → Devices & services → Helpers → Create helper**:

1. **Toggle**, name `OLED Night Sleep`. Confirm the entity ID is exactly `input_boolean.oled_night_sleep`.
2. **Time** (time only, not date and time), name `OLED Night Sleep Start`. Entity ID `input_datetime.oled_night_sleep_start`.
3. **Time**, name `OLED Night Sleep End`. Entity ID `input_datetime.oled_night_sleep_end`.

Home Assistant builds the entity ID from the name. If a name is already taken it may append `_2`. Open the helper and set the entity ID to the value above. The three IDs have to match exactly.

Creating or editing the helpers does not require an add-on restart. Renaming a helper changes its entity ID. The add-on then no longer sees that entity and falls back to the add-on options for the whole window until all three IDs exist again. It does not mix a helper time with an option time.

If any one state is missing, `unknown`, `unavailable`, or not a time, the add-on uses the options. A time may be `HH:MM:SS` in the state (a date prefix is accepted) or a `timestamp` attribute, which is converted to local time. A transport error keeps the last good helper values. With no helpers at all, the options work on their own.

`button_source` selects the input:

- `power_key` (default) reads `KEY_POWER` from the `pwr_button` input device. Use this on Argon ONE V5 / HAOS. The node is discovered from `/proc/bus/input/devices`; `/dev/input/eventN` is not hardcoded. The by-path device `/dev/input/by-path/platform-pwr_button-event` is a fallback. The device is not grabbed, so logind still sees it.
- `gpio` polls GPIO line 4 the way v1.17.0 did. Use this when that line is actually the button.

If the chosen source cannot be opened, startup logs a warning that names `button_source` and the other value. It does not switch by itself.

`config.yaml` lists `/dev/input/event0` through `event15` plus the by-path node so Supervisor can allow those device numbers. Paths that do not exist on a given board are ignored and do not stop the add-on from starting. On HAOS, a short power-key press does not shut down (`HandlePowerKey=ignore`). A hold of about 5 seconds does (`HandlePowerKeyLongPress=poweroff`), which happens before the add-on's 10s/15s prompts. Set the long-press action to `ignore` on the host to use those holds.

See README.md for the full description of every option.

## Development Workflow

1. Make code changes
2. Run unit tests: `python -m unittest discover -s tests -v`
3. Test locally with Docker (optional)
4. Update CHANGELOG.md and version in config.yaml
5. Commit and push changes

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add/update unit tests for your changes
4. Ensure all tests pass
5. Update documentation (README.md, CHANGELOG.md)
6. Submit a Pull Request

### Code Style

- Follow PEP 8 guidelines
- Use descriptive variable names
- Add docstrings to functions and classes
- Keep functions focused and modular
- Write unit tests for new features
