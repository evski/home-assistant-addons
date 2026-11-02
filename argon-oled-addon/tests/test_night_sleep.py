"""Tests for the scheduled night-sleep window.

Hardware is not required. Time of day is injected, and the panel is a mock.
A press inside the window still wakes the panel and still counts as that
press; the window blanks again after idle_dim_timeout and stays in force.
"""

import os
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

try:
    import argon_oled
    from argon_oled import (
        NIGHT_SLEEP_BOOL_ENTITY,
        NIGHT_SLEEP_END_ENTITY,
        NIGHT_SLEEP_HELPER_INTERVAL,
        NIGHT_SLEEP_START_ENTITY,
        format_hhmm,
        night_window_contains,
        parse_clock,
        parse_helper_override,
        parse_time_entity,
    )
    from tests.test_display_blanking import _fake_canvas, make_oled
    ARGON_AVAILABLE = True
except ImportError:
    ARGON_AVAILABLE = False
    argon_oled = None


if ARGON_AVAILABLE and argon_oled.Value is None:
    class _Value:
        ACTIVE = 1
        INACTIVE = 0

    argon_oled.Value = _Value


INSIDE = datetime(2026, 11, 2, 23, 10)
MORNING = datetime(2026, 11, 2, 7, 0)
NEW_YORK = ZoneInfo("America/New_York")


def _states(enabled="on", start="22:00:00", end="06:30:00", start_attributes=None, end_attributes=None):
    return {
        NIGHT_SLEEP_BOOL_ENTITY: {"state": enabled, "attributes": {}},
        NIGHT_SLEEP_START_ENTITY: {
            "state": start,
            "attributes": start_attributes or {},
        },
        NIGHT_SLEEP_END_ENTITY: {
            "state": end,
            "attributes": end_attributes or {},
        },
    }


@contextmanager
def _frozen(instant, when):
    with patch("argon_oled.time.time", return_value=instant), \
            patch("argon_oled.local_now", return_value=when):
        yield


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestNightWindowClock(unittest.TestCase):
    """Wall-clock window, including midnight, an empty window, and DST."""

    def test_overnight_window_includes_start_and_excludes_end(self):
        start, end = (22, 0), (6, 30)
        self.assertTrue(night_window_contains(datetime(2026, 1, 1, 22, 0), start, end))
        self.assertTrue(night_window_contains(datetime(2026, 1, 1, 23, 59), start, end))
        self.assertTrue(night_window_contains(datetime(2026, 1, 2, 0, 0), start, end))
        self.assertTrue(night_window_contains(datetime(2026, 1, 2, 6, 29), start, end))
        self.assertFalse(night_window_contains(datetime(2026, 1, 2, 6, 30), start, end))
        self.assertFalse(night_window_contains(datetime(2026, 1, 1, 21, 59), start, end))
        self.assertFalse(night_window_contains(datetime(2026, 1, 1, 12, 0), start, end))

    def test_same_day_window(self):
        start, end = (9, 0), (17, 0)
        self.assertFalse(night_window_contains(datetime(2026, 1, 1, 8, 59), start, end))
        self.assertTrue(night_window_contains(datetime(2026, 1, 1, 9, 0), start, end))
        self.assertTrue(night_window_contains(datetime(2026, 1, 1, 16, 59), start, end))
        self.assertFalse(night_window_contains(datetime(2026, 1, 1, 17, 0), start, end))

    def test_equal_start_and_end_is_never_a_window(self):
        start = end = (22, 0)
        self.assertFalse(night_window_contains(datetime(2026, 1, 1, 22, 0), start, end))
        self.assertFalse(night_window_contains(datetime(2026, 1, 1, 0, 0), start, end))
        self.assertFalse(night_window_contains(datetime(2026, 1, 1, 12, 0), start, end))

    def test_unparseable_clock_is_not_a_time(self):
        self.assertEqual(parse_clock("22:00"), (22, 0))
        self.assertEqual(parse_clock("06:30:45"), (6, 30))
        self.assertEqual(parse_clock("2026-11-02 06:30:00"), (6, 30))
        self.assertEqual(parse_clock("2026-11-02T22:15:00"), (22, 15))
        for bad in ("24:00", "22:60", "7:30", "22", "evening", "", None, 2200):
            self.assertIsNone(parse_clock(bad), bad)

    def test_spring_forward_uses_wall_clock_not_elapsed_time(self):
        # 2026-03-08: America/New_York jumps from 01:59 EST to 03:00 EDT.
        start, end = (1, 0), (3, 30)
        before = datetime(2026, 3, 8, 1, 30, tzinfo=NEW_YORK)
        after = datetime(2026, 3, 8, 3, 15, tzinfo=NEW_YORK)
        self.assertTrue(night_window_contains(before, start, end))
        self.assertTrue(night_window_contains(after, start, end))
        self.assertFalse(
            night_window_contains(datetime(2026, 3, 8, 3, 30, tzinfo=NEW_YORK), start, end)
        )
        # 02:15-02:45 does not occur that morning. The times on either side
        # of the gap are outside that window.
        gap_start, gap_end = (2, 15), (2, 45)
        self.assertFalse(
            night_window_contains(datetime(2026, 3, 8, 1, 59, tzinfo=NEW_YORK), gap_start, gap_end)
        )
        self.assertFalse(
            night_window_contains(datetime(2026, 3, 8, 3, 0, tzinfo=NEW_YORK), gap_start, gap_end)
        )

    def test_fall_back_repeated_hour_stays_inside_a_covering_window(self):
        # 2026-11-01: 01:30 happens twice, an hour apart in UTC.
        first = datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK, fold=0)
        second = datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK, fold=1)
        self.assertNotEqual(first.utcoffset(), second.utcoffset())
        self.assertEqual(first.hour, second.hour)
        start, end = (0, 30), (2, 30)
        self.assertTrue(night_window_contains(first, start, end))
        self.assertTrue(night_window_contains(second, start, end))
        self.assertFalse(
            night_window_contains(datetime(2026, 11, 1, 2, 30, tzinfo=NEW_YORK), start, end)
        )


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestNightSleepPanel(unittest.TestCase):
    """Blank at the start time, wake on the press, blank again on idle."""

    def _night(self, **overrides):
        base = dict(
            night_sleep_enabled=True,
            night_sleep_start="22:00",
            night_sleep_end="06:30",
            screen_off_until_button=False,
            idle_dim_timeout=60,
            display_blanked=False,
            night_sleep_active=False,
        )
        base.update(overrides)
        return make_oled(**base)

    def test_window_opens_immediately_even_if_the_panel_was_just_used(self):
        oled = self._night(last_activity=5000.0)
        with _frozen(5001.0, datetime(2026, 1, 1, 22, 0)):
            oled._sync_night_sleep()
        self.assertTrue(oled.night_sleep_active)
        self.assertTrue(oled.display_blanked)
        oled.device.hide.assert_called_once()
        oled.device.show.assert_not_called()

    def test_equal_times_do_not_blank(self):
        oled = self._night(night_sleep_start="22:00", night_sleep_end="22:00")
        oled._sync_night_sleep(datetime(2026, 1, 1, 22, 0))
        self.assertFalse(oled.night_sleep_active)
        oled.device.hide.assert_not_called()

    def test_bad_option_times_do_not_sleep_forever(self):
        oled = self._night(night_sleep_start="99:99", night_sleep_end="06:30")
        oled._sync_night_sleep(datetime(2026, 1, 1, 23, 0))
        self.assertFalse(oled.night_sleep_active)
        oled.device.hide.assert_not_called()

    def test_press_wakes_advances_then_idle_blanks_and_the_window_continues(self):
        oled = self._night(last_activity=1000.0, current_screen=0, switch_duration=100000)
        oled.display_screen = MagicMock()
        messages = []
        oled.debug_log = messages.append

        with _frozen(1000.0, INSIDE):
            oled._service_once(1000.0)
        self.assertTrue(oled.night_sleep_active)
        self.assertTrue(oled.display_blanked)
        self.assertEqual(oled.current_screen, 0)

        # The press that wakes the panel is still "next". It does not
        # cancel the window for the rest of the night.
        with _frozen(1010.0, INSIDE):
            oled._on_button_activity()
            oled.button_action = "next"
            oled._service_once(1010.0)
        oled.device.show.assert_called()
        self.assertFalse(oled.display_blanked)
        self.assertTrue(oled.night_sleep_active)
        self.assertEqual(oled.current_screen, 1)
        self.assertIsNone(oled.button_action)
        oled.device.show.reset_mock()

        with _frozen(1069.0, INSIDE):
            oled._service_once(1069.0)
        self.assertFalse(oled.display_blanked)
        self.assertTrue(oled.night_sleep_active)

        with _frozen(1070.0, INSIDE):
            oled._service_once(1070.0)
        self.assertTrue(oled.display_blanked)
        self.assertTrue(oled.night_sleep_active)
        oled.device.show.assert_not_called()

        # Later the same night the panel stays dark and the window is still on.
        with _frozen(1400.0, datetime(2026, 11, 2, 23, 40)):
            oled._service_once(1400.0)
        self.assertTrue(oled.display_blanked)
        self.assertTrue(oled.night_sleep_active)
        oled.device.show.assert_not_called()

        # A later press wakes it again, and again advances one screen.
        with _frozen(1410.0, datetime(2026, 11, 2, 23, 41)):
            oled._on_button_activity()
            oled.button_action = "next"
            oled._service_once(1410.0)
        self.assertFalse(oled.display_blanked)
        self.assertTrue(oled.night_sleep_active)
        self.assertEqual(oled.current_screen, 2)

        started = [line for line in messages if line.startswith("Night sleep started")]
        ended = [line for line in messages if line == "Night sleep ended"]
        self.assertEqual(started, ["Night sleep started (22:00-06:30)"])
        self.assertEqual(ended, [])

    def test_window_end_turns_the_panel_on_unless_screen_off_is_set(self):
        oled = self._night(night_sleep_active=True, display_blanked=True)
        oled._night_logged_active = True
        oled._sync_night_sleep(MORNING)
        self.assertFalse(oled.night_sleep_active)
        self.assertFalse(oled.display_blanked)
        oled.device.show.assert_called_once()

        stayed = self._night(
            night_sleep_active=True,
            display_blanked=True,
            screen_off_until_button=True,
        )
        stayed._night_logged_active = True
        stayed._sync_night_sleep(MORNING)
        self.assertFalse(stayed.night_sleep_active)
        self.assertTrue(stayed.display_blanked)
        stayed.device.show.assert_not_called()
        stayed.device.hide.assert_not_called()

    def test_transitions_are_logged_once_at_debug_not_every_pass(self):
        oled = self._night()
        messages = []
        oled.debug_log = messages.append
        for minute in (23, 0, 1, 2):
            hour = 23 if minute == 23 else minute
            oled._sync_night_sleep(datetime(2026, 11, 2, hour, minute))
        oled._sync_night_sleep(MORNING)
        oled._sync_night_sleep(MORNING)
        oled._sync_night_sleep(datetime(2026, 11, 2, 8, 0))
        self.assertEqual(
            [line for line in messages if line.startswith("Night sleep started")],
            ["Night sleep started (22:00-06:30)"],
        )
        self.assertEqual(
            [line for line in messages if line == "Night sleep ended"],
            ["Night sleep ended"],
        )

    def test_disabled_option_never_logs_or_blanks(self):
        oled = self._night(night_sleep_enabled=False)
        messages = []
        oled.debug_log = messages.append
        oled._sync_night_sleep(INSIDE)
        oled._sync_night_sleep(INSIDE)
        self.assertFalse(oled.night_sleep_active)
        self.assertEqual(messages, [])
        oled.device.hide.assert_not_called()

    def test_restart_inside_the_window_skips_the_logo(self):
        oled = self._night(show_credits=True, credits_shown=False, display_blanked=False)
        oled.gpio_line = None

        def stop(_current_time):
            raise KeyboardInterrupt

        oled._service_once = stop
        with _frozen(1000.0, INSIDE), patch("argon_oled.time.sleep", lambda _dt: None):
            oled.run()
        oled.renderer.draw_credits.assert_not_called()
        self.assertTrue(oled.credits_shown)
        self.assertTrue(oled.display_blanked)
        self.assertTrue(oled.night_sleep_active)
        oled.device.hide.assert_called()

    def test_restart_outside_the_window_still_shows_credits(self):
        oled = self._night(show_credits=True, credits_shown=False, display_blanked=False)
        oled.gpio_line = None

        def stop(_current_time):
            raise KeyboardInterrupt

        oled._service_once = stop
        with _frozen(1000.0, datetime(2026, 11, 2, 12, 0)), \
                patch("argon_oled.time.sleep", lambda _dt: None):
            oled.run()
        oled.renderer.draw_credits.assert_called_once()
        self.assertFalse(oled.display_blanked)
        oled.device.hide.assert_not_called()


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestGesturesDuringNightSleep(unittest.TestCase):
    """Gestures match the daytime rules. The panel is on before a power prompt."""

    def setUp(self):
        self.clock = {"t": 1000.0}

    def _now(self):
        return self.clock["t"]

    def _sleep(self, dt):
        self.clock["t"] += dt

    def _oled(self):
        return make_oled(
            night_sleep_enabled=True,
            night_sleep_active=True,
            screen_off_until_button=False,
            display_blanked=True,
            power_management_enabled=True,
        )

    def _run(self, oled, presses, countdown=None):
        def get_value(_pin):
            if oled.button_action:
                raise KeyboardInterrupt
            now = self.clock["t"]
            for start, end in presses:
                if start <= now < end:
                    return argon_oled.Value.INACTIVE
            if countdown is None and now > presses[-1][1] + 2:
                raise KeyboardInterrupt
            return argon_oled.Value.ACTIVE

        oled.gpio_line.get_value.side_effect = get_value
        if countdown is not None:
            oled._draw_confirmation_countdown = countdown
        with patch("argon_oled.time.time", self._now), \
                patch("argon_oled.time.sleep", self._sleep), \
                patch("argon_oled.canvas", _fake_canvas):
            with self.assertRaises(KeyboardInterrupt):
                oled.button_monitor()

    def test_single_double_and_long_press_wake_and_keep_their_gesture(self):
        cases = [
            ([(1000.0, 1000.1)], "next"),
            ([(1000.0, 1000.1), (1000.2, 1000.3)], "prev"),
            ([(1000.0, 1000.8)], "first"),
        ]
        for presses, action in cases:
            with self.subTest(action=action):
                self.clock["t"] = 999.0
                oled = self._oled()
                self._run(oled, presses)
                self.assertEqual(oled.button_action, action)
                self.assertFalse(oled.display_blanked)
                self.assertTrue(oled.night_sleep_active)
                oled.device.show.assert_called()
                oled.device.hide.assert_not_called()

    def test_single_press_then_service_advances_one_screen(self):
        oled = self._oled()
        oled.current_screen = 0
        self._run(oled, [(1000.0, 1000.1)])
        self.assertEqual(oled.button_action, "next")
        with _frozen(self.clock["t"], INSIDE):
            oled._service_once(self.clock["t"])
        self.assertEqual(oled.current_screen, 1)
        self.assertFalse(oled.display_blanked)
        self.assertTrue(oled.night_sleep_active)

    def test_ten_second_hold_draws_the_prompt_on_a_lit_panel(self):
        oled = self._oled()
        frames = []

        @contextmanager
        def canvas(_device):
            frames.append(oled.display_blanked)
            yield MagicMock()

        seen = {}

        def countdown(name):
            seen["name"] = name
            seen["blanked"] = oled.display_blanked
            seen["show_called"] = oled.device.show.called
            oled._restore_after_power_ui()
            return True

        oled.gpio_line.get_value.side_effect = lambda _pin: (
            argon_oled.Value.INACTIVE if self.clock["t"] < 1010.25 else argon_oled.Value.ACTIVE
        )
        # Stop once the confirmation has restored the idle timer.
        real_restore = oled._restore_after_power_ui

        def restore_and_stop():
            real_restore()
            raise KeyboardInterrupt

        oled._draw_confirmation_countdown = countdown
        oled._restore_after_power_ui = restore_and_stop
        with patch("argon_oled.time.time", self._now), \
                patch("argon_oled.time.sleep", self._sleep), \
                patch("argon_oled.canvas", canvas):
            with self.assertRaises(KeyboardInterrupt):
                oled.button_monitor()
        self.assertEqual(seen["name"], "REBOOT")
        self.assertFalse(seen["blanked"])
        self.assertTrue(seen["show_called"])
        self.assertTrue(frames)
        self.assertTrue(all(blanked is False for blanked in frames))
        self.assertTrue(oled.night_sleep_active)
        self.assertFalse(oled.display_blanked)

    def test_countdown_frames_are_drawn_only_after_show(self):
        oled = self._oled()
        oled.button_in_power_hold = True
        states = [argon_oled.Value.ACTIVE, argon_oled.Value.INACTIVE]

        def get_value(_pin):
            if states:
                return states.pop(0)
            return argon_oled.Value.INACTIVE

        oled.gpio_line.get_value.side_effect = get_value
        shown_before_draw = []

        @contextmanager
        def canvas(_device):
            shown_before_draw.append(oled.display_blanked)
            yield MagicMock()

        with patch("argon_oled.time.sleep", lambda _dt: None), \
                patch("argon_oled.canvas", canvas):
            cancelled = oled._draw_confirmation_countdown("SHUTDOWN")
        self.assertTrue(cancelled)
        self.assertTrue(shown_before_draw)
        self.assertTrue(all(blanked is False for blanked in shown_before_draw))
        oled.device.show.assert_called()
        self.assertFalse(oled.display_blanked)

    def test_power_key_single_press_wakes_during_night_sleep(self):
        from tests.test_power_button import ScriptedKey

        oled = self._oled()
        oled.button_source = "power_key"
        device = ScriptedKey([(1000.0, 1000.1)], self.clock, oled)
        with patch("argon_oled.time.time", self._now), \
                patch("argon_oled.time.sleep", self._sleep), \
                patch("argon_oled.canvas", _fake_canvas):
            with self.assertRaises(KeyboardInterrupt):
                oled._monitor_power_key(device)
        self.assertEqual(oled.button_action, "next")
        self.assertFalse(oled.display_blanked)
        self.assertTrue(oled.night_sleep_active)
        oled.device.show.assert_called()


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestHelperOverride(unittest.TestCase):
    """Dashboard helpers replace the options only when all three are usable."""

    def test_state_string_and_timestamp_both_parse(self):
        self.assertEqual(parse_time_entity({"state": "22:00:00"}), (22, 0))
        self.assertEqual(parse_time_entity({"state": "06:30:00"}), (6, 30))
        self.assertEqual(
            parse_time_entity({"state": "2026-11-02 06:30:00"}),
            (6, 30),
        )
        # Build the sample through the local zone so the offset matches the
        # date. Attaching summer's 22:15 to the current (winter) tzinfo gives
        # 21:15 UTC, which reads back as 23:15 here and would pass only on a
        # UTC runner.
        local = datetime(2026, 6, 15, 22, 15).astimezone()
        from_timestamp = parse_time_entity({
            "state": "",
            "attributes": {"timestamp": local.timestamp()},
        })
        self.assertEqual(from_timestamp, (22, 15))
        self.assertEqual(
            parse_time_entity({"state": "", "attributes": {"hour": 6, "minute": 30}}),
            (6, 30),
        )

    def test_unavailable_does_not_fall_through_to_timestamp(self):
        payload = {"state": "unavailable", "attributes": {"timestamp": 1_700_000_000, "hour": 22, "minute": 0}}
        self.assertIsNone(parse_time_entity(payload))
        self.assertIsNone(parse_time_entity({"state": "unknown"}))
        self.assertIsNone(parse_helper_override(_states(enabled="unknown")))
        self.assertIsNone(parse_helper_override(_states(start="unavailable")))

    def test_partial_helpers_fall_back_to_options_without_mixing(self):
        # Options would be asleep at 08:30. A mixed read (helper start 22:00
        # plus this option end) would also be asleep at 23:00. Neither may happen.
        oled = make_oled(
            night_sleep_enabled=True,
            night_sleep_start="08:00",
            night_sleep_end="09:00",
        )
        payloads = _states(start="22:00:00", end="06:30:00")
        payloads[NIGHT_SLEEP_END_ENTITY] = {"state": "unavailable", "attributes": {}}
        override = parse_helper_override(payloads)
        self.assertIsNone(override)
        oled._helper_override = override
        oled._sync_night_sleep(datetime(2026, 11, 2, 8, 30))
        self.assertTrue(oled.night_sleep_active)
        oled._night_logged_active = True
        oled._sync_night_sleep(datetime(2026, 11, 2, 23, 0))
        self.assertFalse(oled.night_sleep_active)

    def test_helpers_override_options_until_one_disappears(self):
        # Options are a daytime window. Helpers must win while they are present,
        # including turning the feature off, and the daytime window returns
        # only after a helper disappears.
        oled = make_oled(night_sleep_enabled=True, night_sleep_start="08:00", night_sleep_end="09:00")
        messages = []
        oled.debug_log = messages.append
        store = _states()

        def get_entity_state(entity_id, timeout=5):
            payload = store.get(entity_id)
            if payload is None:
                return "missing", None
            return "ok", payload

        oled.supervisor_api = MagicMock()
        oled.supervisor_api.get_entity_state.side_effect = get_entity_state
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            oled._poll_night_sleep_helpers(1000.0, force=True)
            oled._sync_night_sleep(INSIDE)
            self.assertEqual(oled._schedule_source, "homeassistant")
            self.assertTrue(oled.night_sleep_active)
            self.assertTrue(oled.display_blanked)

            # Same values again: no second source log, and the window stays.
            oled._poll_night_sleep_helpers(1010.0)
            oled._sync_night_sleep(INSIDE)
            self.assertTrue(oled.night_sleep_active)

            store[NIGHT_SLEEP_BOOL_ENTITY] = {"state": "off", "attributes": {}}
            oled._poll_night_sleep_helpers(1030.0, force=True)
            oled._sync_night_sleep(INSIDE)
            self.assertEqual(oled._schedule_source, "homeassistant")
            self.assertFalse(oled.night_sleep_active)
            self.assertFalse(oled.display_blanked)

            del store[NIGHT_SLEEP_START_ENTITY]
            oled._poll_night_sleep_helpers(1060.0, force=True)
            oled._sync_night_sleep(datetime(2026, 11, 2, 8, 30))
        self.assertEqual(oled._schedule_source, "options")
        self.assertIsNone(oled._helper_override)
        # Options are 08:00-09:00 and enabled, so 08:30 is inside the fallback.
        self.assertTrue(oled.night_sleep_active)
        sources = [line for line in messages if line.startswith("Night sleep schedule source:")]
        self.assertEqual(sources, [
            "Night sleep schedule source: Home Assistant helpers",
            "Night sleep schedule source: add-on options",
        ])

    def test_transport_error_keeps_the_last_good_helpers(self):
        oled = make_oled(night_sleep_enabled=False)
        calls = {"n": 0}

        def get_entity_state(entity_id, timeout=5):
            calls["n"] += 1
            if calls["n"] <= 3:
                return "ok", _states()[entity_id]
            return "error", None

        oled.supervisor_api = MagicMock()
        oled.supervisor_api.get_entity_state.side_effect = get_entity_state
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            oled._poll_night_sleep_helpers(1000.0, force=True)
            oled._poll_night_sleep_helpers(1030.0, force=True)
        self.assertEqual(oled._helper_override[0], True)
        self.assertEqual(oled._helper_override[1], (22, 0))
        self.assertEqual(oled._helper_override[2], (6, 30))
        self.assertEqual(oled._schedule_source, "homeassistant")

    def test_poll_interval_and_one_fast_retry_after_a_good_read_goes_bad(self):
        oled = make_oled(night_sleep_enabled=False)
        calls = {"n": 0}
        good = True

        def get_entity_state(entity_id, timeout=5):
            calls["n"] += 1
            if not good:
                return "missing", None
            return "ok", _states()[entity_id]

        oled.supervisor_api = MagicMock()
        oled.supervisor_api.get_entity_state.side_effect = get_entity_state
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            oled._poll_night_sleep_helpers(1000.0)
            after_first = calls["n"]
            oled._poll_night_sleep_helpers(1010.0)
            oled._poll_night_sleep_helpers(1029.0)
            self.assertEqual(calls["n"], after_first)
            self.assertEqual(after_first, 3)

            good = False
            oled._poll_night_sleep_helpers(1000.0 + NIGHT_SLEEP_HELPER_INTERVAL)
            after_bad = calls["n"]
            self.assertEqual(after_bad, 6)
            self.assertIsNone(oled._helper_override)
            # Not due just before the shortened retry, due once it elapses.
            oled._poll_night_sleep_helpers(1034.0)
            self.assertEqual(calls["n"], after_bad)
            oled._poll_night_sleep_helpers(1035.0)
            self.assertGreater(calls["n"], after_bad)
            retried = calls["n"]
            # The retry was the one extra look. The next gap is the full interval.
            oled._poll_night_sleep_helpers(1035.0 + NIGHT_SLEEP_HELPER_INTERVAL - 1)
            self.assertEqual(calls["n"], retried)

    def test_missing_helpers_on_startup_use_options_and_do_not_retry_early(self):
        oled = make_oled(night_sleep_enabled=True, night_sleep_start="22:00", night_sleep_end="06:30")
        calls = {"n": 0}

        def get_entity_state(entity_id, timeout=5):
            calls["n"] += 1
            return "missing", None

        oled.supervisor_api = MagicMock()
        oled.supervisor_api.get_entity_state.side_effect = get_entity_state
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            oled._poll_night_sleep_helpers(1000.0, force=True)
            oled._sync_night_sleep(INSIDE)
            oled._poll_night_sleep_helpers(1005.0)
        self.assertEqual(oled._schedule_source, "options")
        self.assertTrue(oled.night_sleep_active)
        self.assertEqual(calls["n"], 3)

    def test_no_token_uses_options_without_calling_the_proxy(self):
        oled = make_oled(night_sleep_enabled=True)
        oled.supervisor_api = MagicMock()
        with patch("argon_oled.SUPERVISOR_TOKEN", ""):
            oled._poll_night_sleep_helpers(1000.0, force=True)
        oled.supervisor_api.get_entity_state.assert_not_called()
        self.assertEqual(oled._schedule_source, "options")

    def test_service_loop_picks_up_a_helper_change_without_a_new_instance(self):
        oled = make_oled(
            night_sleep_enabled=False,
            screen_off_until_button=False,
            display_blanked=False,
        )
        store = {"mode": "asleep"}

        def get_entity_state(entity_id, timeout=5):
            if store["mode"] == "missing":
                return "missing", None
            if store["mode"] == "off":
                states = _states(enabled="off")
            else:
                states = _states()
            return "ok", states[entity_id]

        oled.supervisor_api = MagicMock()
        oled.supervisor_api.get_entity_state.side_effect = get_entity_state
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _frozen(1000.0, INSIDE):
                oled._service_once(1000.0)
            self.assertTrue(oled.night_sleep_active)
            self.assertTrue(oled.display_blanked)

            store["mode"] = "off"
            with _frozen(1000.0 + NIGHT_SLEEP_HELPER_INTERVAL, INSIDE):
                oled._service_once(1000.0 + NIGHT_SLEEP_HELPER_INTERVAL)
        self.assertFalse(oled.night_sleep_active)
        self.assertFalse(oled.display_blanked)
        oled.device.show.assert_called()


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestNightSleepConfig(unittest.TestCase):
    def test_main_reads_night_sleep_options(self):
        env = {
            "SCREEN_LIST": "clock",
            "SWITCH_DURATION": "30",
            "TEMP_UNIT": "C",
            "DEBUG_LOGGING": "false",
            "SHOW_CREDITS": "true",
            "SCREEN_OFF_UNTIL_BUTTON": "false",
            "IDLE_DIM_TIMEOUT": "45",
            "BUTTON_SOURCE": "power_key",
            "NIGHT_SLEEP": "true",
            "NIGHT_SLEEP_START": "23:15",
            "NIGHT_SLEEP_END": "05:45",
            "ADDON_VERSION": "1.19.0",
        }
        with patch.dict(os.environ, env, clear=False), \
                patch("argon_oled.ArgonOLED") as cls:
            argon_oled.main()
        args = cls.call_args[0]
        self.assertTrue(args[9])
        self.assertEqual(args[10], "23:15")
        self.assertEqual(args[11], "05:45")
        self.assertEqual(args[7], 45)
        self.assertEqual(args[8], "power_key")

    def test_config_schema_is_hhmm_match_and_api_is_enabled(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        with open(config_path, encoding="utf-8") as handle:
            text = handle.read()
        needle = 'match(^([01][0-9]|2[0-3]):[0-5][0-9]$)'
        self.assertEqual(text.count(needle), 2)
        self.assertIn("homeassistant_api: true", text)
        self.assertIn('night_sleep_start: "22:00"', text)
        self.assertIn('night_sleep_end: "06:30"', text)
        self.assertIn("night_sleep: false", text)
        self.assertIn("version: 1.19.0", text)
        self.assertIn("slug: argon_one_oled_display", text)
        self.assertIn("name: Argon ONE OLED Display", text)
        pattern = __import__("re").compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
        self.assertTrue(pattern.match("22:00"))
        self.assertTrue(pattern.match("06:30"))
        self.assertTrue(pattern.match("00:00"))
        self.assertTrue(pattern.match("23:59"))
        self.assertFalse(pattern.match("24:00"))
        self.assertFalse(pattern.match("22:60"))
        self.assertFalse(pattern.match("7:30"))
        # The parser accepts every string the schema accepts.
        self.assertEqual(parse_clock("22:00"), (22, 0))
        self.assertEqual(format_hhmm((6, 30)), "06:30")


if __name__ == "__main__":
    unittest.main()
