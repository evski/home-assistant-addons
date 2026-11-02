"""Tests for the daytime idle-blanking helper.

The helper is input_boolean.oled_screen_off_until_button. It is read on the
same Supervisor pass as the night-sleep helpers and overrides
screen_off_until_button when its state is on or off. A missing or unusable
state falls back to the option. The night-sleep helpers stay all-or-nothing.

Hardware is not required. Time of day is injected, and the panel is a mock.
Naive datetimes are used on purpose: attaching a wall time to the runner's
current tzinfo has failed when the runner's zone and the sample's season
did not match.
"""

import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

try:
    import argon_oled
    from argon_oled import (
        NIGHT_SLEEP_BOOL_ENTITY,
        NIGHT_SLEEP_END_ENTITY,
        NIGHT_SLEEP_HELPER_INTERVAL,
        NIGHT_SLEEP_START_ENTITY,
        SCREEN_OFF_ENTITY,
        parse_screen_off_helper,
    )
    from tests.test_display_blanking import make_oled
    ARGON_AVAILABLE = True
except ImportError:
    ARGON_AVAILABLE = False
    argon_oled = None


DAY = datetime(2026, 11, 2, 12, 0)
INSIDE = datetime(2026, 11, 2, 23, 10)
MORNING = datetime(2026, 11, 2, 7, 0)


class _Frozen:
    def __init__(self, instant, when):
        self.instant = instant
        self.when = when
        self._patches = None

    def __enter__(self):
        self._patches = [
            patch("argon_oled.time.time", return_value=self.instant),
            patch("argon_oled.local_now", return_value=self.when),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        for item in reversed(self._patches):
            item.stop()
        return False


def _attach(oled, store):
    """Serve helper payloads from a dict. A missing key is a missing entity."""

    def get_entity_state(entity_id, timeout=5):
        if entity_id not in store:
            return "missing", None
        value = store[entity_id]
        if value == "error":
            return "error", None
        return "ok", value

    oled.supervisor_api = MagicMock()
    oled.supervisor_api.get_entity_state.side_effect = get_entity_state
    return oled.supervisor_api


def _on_off(state):
    return {"state": state, "attributes": {}}


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestScreenOffHelperParse(unittest.TestCase):
    def test_on_and_off_parse_and_everything_else_is_unusable(self):
        self.assertTrue(parse_screen_off_helper(_on_off("on")))
        self.assertTrue(parse_screen_off_helper(_on_off(" ON ")))
        self.assertFalse(parse_screen_off_helper(_on_off("off")))
        self.assertFalse(parse_screen_off_helper(_on_off("Off")))
        for bad in ("unknown", "unavailable", "none", "null", "", "true", "1"):
            self.assertIsNone(parse_screen_off_helper(_on_off(bad)), bad)
        self.assertIsNone(parse_screen_off_helper(None))
        self.assertIsNone(parse_screen_off_helper({"state": True}))
        self.assertEqual(SCREEN_OFF_ENTITY, "input_boolean.oled_screen_off_until_button")


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestScreenOffHelperLive(unittest.TestCase):
    """The helper overrides the option, live, without a new instance."""

    def _oled(self, **overrides):
        base = dict(
            screen_off_until_button=False,
            night_sleep_enabled=False,
            idle_dim_timeout=60,
            display_blanked=False,
            last_activity=0,
            last_switch=0,
            current_screen=0,
            switch_duration=30,
        )
        base.update(overrides)
        oled = make_oled(**base)
        oled.display_screen = MagicMock()
        return oled

    def test_helper_on_overrides_option_false_without_blanking_immediately(self):
        oled = self._oled(screen_off_until_button=False)
        store = {SCREEN_OFF_ENTITY: _on_off("off")}
        api = _attach(oled, store)
        messages = []
        oled.debug_log = messages.append

        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(1000.0, DAY):
                oled._service_once(1000.0)
            self.assertFalse(oled._effective_screen_off())
            self.assertFalse(oled.screen_off_until_button)
            self.assertFalse(oled.display_blanked)
            oled.device.hide.assert_not_called()

            store[SCREEN_OFF_ENTITY] = _on_off("on")
            with _Frozen(1000.0 + NIGHT_SLEEP_HELPER_INTERVAL, DAY):
                oled._service_once(1000.0 + NIGHT_SLEEP_HELPER_INTERVAL)
            self.assertTrue(oled._effective_screen_off())
            self.assertFalse(oled.screen_off_until_button)
            self.assertFalse(oled.display_blanked)
            self.assertEqual(oled.last_activity, 1030.0)
            oled.device.hide.assert_not_called()
            self.assertEqual(oled._idle_blanking_source, "homeassistant")

            # Still inside the idle timeout that started when the helper came on.
            with _Frozen(1089.0, DAY):
                oled._service_once(1089.0)
            self.assertFalse(oled.display_blanked)
            oled.device.hide.assert_not_called()

            with _Frozen(1090.0, DAY):
                oled._service_once(1090.0)
            self.assertTrue(oled.display_blanked)
            oled.device.hide.assert_called_once()

        screen_reads = [
            call.args[0]
            for call in api.get_entity_state.call_args_list
            if call.args[0] == SCREEN_OFF_ENTITY
        ]
        self.assertGreaterEqual(len(screen_reads), 2)

    def test_helper_off_overrides_option_true_and_wakes_a_blanked_panel(self):
        oled = self._oled(
            screen_off_until_button=True,
            display_blanked=True,
            _idle_blanking_applied=True,
            _screen_off_override=True,
            _idle_blanking_source="homeassistant",
        )
        oled.current_screen = 1
        store = {SCREEN_OFF_ENTITY: _on_off("off")}
        _attach(oled, store)

        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(2000.0, DAY):
                oled._service_once(2000.0)
        self.assertFalse(oled._effective_screen_off())
        self.assertTrue(oled.screen_off_until_button)
        self.assertFalse(oled.display_blanked)
        self.assertFalse(oled.night_sleep_active)
        oled.device.show.assert_called_once()
        oled.device.hide.assert_not_called()
        # Wake draws the current screen and restarts rotation from now.
        oled.display_screen.assert_called_once_with("clock")
        self.assertEqual(oled.current_screen, 1)
        self.assertEqual(oled.last_switch, 2000.0)

        oled.display_screen.reset_mock()
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(2030.0, DAY):
                oled._service_once(2030.0)
        self.assertEqual(oled.current_screen, 2)
        oled.display_screen.assert_called_once_with("cpu")
        self.assertFalse(oled.display_blanked)

    def test_turning_helper_off_mid_day_stops_any_further_blanking(self):
        oled = self._oled(screen_off_until_button=False, _idle_blanking_applied=False)
        store = {SCREEN_OFF_ENTITY: _on_off("on")}
        _attach(oled, store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(3000.0, DAY):
                oled._service_once(3000.0)
            self.assertFalse(oled.display_blanked)
            self.assertEqual(oled.last_activity, 3000.0)

            store[SCREEN_OFF_ENTITY] = _on_off("off")
            with _Frozen(3030.0, DAY):
                oled._service_once(3030.0)
            self.assertFalse(oled._effective_screen_off())
            with _Frozen(9999.0, DAY):
                oled._service_once(9999.0)
        self.assertFalse(oled.display_blanked)
        oled.device.hide.assert_not_called()

    def test_unusable_helper_falls_back_to_the_option(self):
        cases = ("unknown", "unavailable", "none", "null", "", "maybe")
        for state in cases:
            with self.subTest(state=state):
                for option, expect in ((True, True), (False, False)):
                    oled = self._oled(screen_off_until_button=option)
                    store = {SCREEN_OFF_ENTITY: _on_off(state)}
                    _attach(oled, store)
                    with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
                        oled._poll_night_sleep_helpers(1000.0, force=True)
                    self.assertIsNone(oled._screen_off_override)
                    self.assertEqual(oled._effective_screen_off(), expect)
                    self.assertEqual(oled._idle_blanking_source, "options")

        missing = self._oled(screen_off_until_button=True)
        _attach(missing, {})
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            missing._poll_night_sleep_helpers(1000.0, force=True)
        self.assertIsNone(missing._screen_off_override)
        self.assertTrue(missing._effective_screen_off())
        self.assertEqual(missing._idle_blanking_source, "options")

    def test_helper_becoming_unusable_falls_back_and_wakes_when_option_is_off(self):
        oled = self._oled(
            screen_off_until_button=False,
            display_blanked=True,
            _idle_blanking_applied=True,
            _screen_off_override=True,
            _idle_blanking_source="homeassistant",
        )
        messages = []
        oled.debug_log = messages.append
        store = {SCREEN_OFF_ENTITY: _on_off("unknown")}
        _attach(oled, store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(4000.0, DAY):
                oled._service_once(4000.0)
            with _Frozen(4001.0, DAY):
                oled._service_once(4001.0)
        self.assertFalse(oled._effective_screen_off())
        self.assertFalse(oled.display_blanked)
        oled.device.show.assert_called()
        sources = [line for line in messages if line.startswith("Idle blanking source:")]
        self.assertEqual(sources, ["Idle blanking source: add-on options"])

    def test_source_is_logged_once_per_change_not_every_pass(self):
        oled = self._oled()
        messages = []
        oled.debug_log = messages.append
        store = {SCREEN_OFF_ENTITY: _on_off("on")}
        _attach(oled, store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            oled._poll_night_sleep_helpers(1000.0, force=True)
            oled._poll_night_sleep_helpers(1010.0, force=True)
            store[SCREEN_OFF_ENTITY] = _on_off("off")
            oled._poll_night_sleep_helpers(1020.0, force=True)
            oled._poll_night_sleep_helpers(1030.0, force=True)
            del store[SCREEN_OFF_ENTITY]
            oled._poll_night_sleep_helpers(1040.0, force=True)
            oled._poll_night_sleep_helpers(1050.0, force=True)
        sources = [line for line in messages if line.startswith("Idle blanking source:")]
        self.assertEqual(sources, [
            "Idle blanking source: Home Assistant helpers",
            "Idle blanking source: add-on options",
        ])

    def test_a_good_screen_off_read_that_goes_bad_retries_once(self):
        oled = self._oled(screen_off_until_button=False)
        calls = {"n": 0}
        good = {"value": True}

        def get_entity_state(entity_id, timeout=5):
            calls["n"] += 1
            if entity_id != SCREEN_OFF_ENTITY or not good["value"]:
                return "missing", None
            return "ok", _on_off("on")

        oled.supervisor_api = MagicMock()
        oled.supervisor_api.get_entity_state.side_effect = get_entity_state
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            oled._poll_night_sleep_helpers(1000.0)
            first = calls["n"]
            self.assertEqual(first, len(argon_oled.NIGHT_SLEEP_ENTITIES) + 1)
            self.assertTrue(oled._screen_off_override)
            oled._poll_night_sleep_helpers(1029.0)
            self.assertEqual(calls["n"], first)

            good["value"] = False
            oled._poll_night_sleep_helpers(1030.0)
            self.assertIsNone(oled._screen_off_override)
            self.assertFalse(oled._effective_screen_off())
            after_bad = calls["n"]
            oled._poll_night_sleep_helpers(1034.0)
            self.assertEqual(calls["n"], after_bad)
            oled._poll_night_sleep_helpers(1035.0)
            self.assertGreater(calls["n"], after_bad)

    def test_transport_error_keeps_the_last_good_helper(self):
        oled = self._oled(screen_off_until_button=False)
        calls = {"n": 0}

        def get_entity_state(entity_id, timeout=5):
            calls["n"] += 1
            if calls["n"] > 4:
                return "error", None
            if entity_id == SCREEN_OFF_ENTITY:
                return "ok", _on_off("on")
            return "missing", None

        oled.supervisor_api = MagicMock()
        oled.supervisor_api.get_entity_state.side_effect = get_entity_state
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            oled._poll_night_sleep_helpers(1000.0, force=True)
            oled._poll_night_sleep_helpers(1030.0, force=True)
        self.assertTrue(oled._screen_off_override)
        self.assertTrue(oled._effective_screen_off())
        self.assertFalse(oled.screen_off_until_button)
        self.assertEqual(oled._idle_blanking_source, "homeassistant")

    def test_screen_off_helper_stands_alone_from_the_night_sleep_trio(self):
        # A bad night-sleep helper must not throw away a good idle-blanking
        # helper, and a bad idle-blanking helper must not throw away a good
        # night-sleep set. The night trio stays all-or-nothing.
        oled = self._oled(
            night_sleep_enabled=True,
            night_sleep_start="08:00",
            night_sleep_end="09:00",
            screen_off_until_button=False,
        )
        store = {
            NIGHT_SLEEP_BOOL_ENTITY: _on_off("off"),
            NIGHT_SLEEP_START_ENTITY: _on_off("22:00:00"),
            NIGHT_SLEEP_END_ENTITY: _on_off("unavailable"),
            SCREEN_OFF_ENTITY: _on_off("on"),
        }
        # Time payloads are not on/off. Replace the two times.
        store[NIGHT_SLEEP_START_ENTITY] = {"state": "22:00:00", "attributes": {}}
        store[NIGHT_SLEEP_END_ENTITY] = {"state": "unavailable", "attributes": {}}
        _attach(oled, store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            oled._poll_night_sleep_helpers(1000.0, force=True)
        self.assertIsNone(oled._helper_override)
        self.assertTrue(oled._screen_off_override)
        self.assertEqual(oled._schedule_source, "options")
        self.assertEqual(oled._idle_blanking_source, "homeassistant")
        oled._sync_night_sleep(datetime(2026, 11, 2, 8, 30))
        self.assertTrue(oled.night_sleep_active)
        oled._night_logged_active = True
        oled._sync_night_sleep(datetime(2026, 11, 2, 23, 0))
        self.assertFalse(oled.night_sleep_active)
        self.assertTrue(oled._effective_screen_off())

        other = self._oled(screen_off_until_button=True, night_sleep_enabled=False)
        other_store = {
            NIGHT_SLEEP_BOOL_ENTITY: _on_off("off"),
            NIGHT_SLEEP_START_ENTITY: {"state": "22:00:00", "attributes": {}},
            NIGHT_SLEEP_END_ENTITY: {"state": "06:30:00", "attributes": {}},
            SCREEN_OFF_ENTITY: _on_off("unknown"),
        }
        _attach(other, other_store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            other._poll_night_sleep_helpers(1000.0, force=True)
        self.assertEqual(other._helper_override[0], False)
        self.assertIsNone(other._screen_off_override)
        self.assertTrue(other._effective_screen_off())
        self.assertEqual(other._schedule_source, "homeassistant")
        self.assertEqual(other._idle_blanking_source, "options")

    def test_power_prompt_still_wakes_a_panel_the_helper_blanked(self):
        oled = self._oled(screen_off_until_button=False, display_blanked=True)
        oled._screen_off_override = True
        oled._ensure_panel_on_for_power_ui()
        self.assertFalse(oled.display_blanked)
        self.assertTrue(oled.button_in_power_hold)
        oled.device.show.assert_called_once()


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestScreenOffHelperAndNightSleep(unittest.TestCase):
    """Night sleep stays in charge of the panel while its window is active."""

    def _night(self, **overrides):
        base = dict(
            night_sleep_enabled=True,
            night_sleep_start="22:00",
            night_sleep_end="06:30",
            screen_off_until_button=False,
            idle_dim_timeout=60,
            display_blanked=False,
            night_sleep_active=False,
            last_activity=1000.0,
        )
        base.update(overrides)
        oled = make_oled(**base)
        oled.display_screen = MagicMock()
        return oled

    def test_night_window_still_blanks_when_the_helper_is_off(self):
        oled = self._night(screen_off_until_button=True)
        store = {SCREEN_OFF_ENTITY: _on_off("off")}
        _attach(oled, store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(1000.0, INSIDE):
                oled._service_once(1000.0)
        self.assertTrue(oled.night_sleep_active)
        self.assertFalse(oled._effective_screen_off())
        self.assertTrue(oled.screen_off_until_button)
        self.assertTrue(oled.display_blanked)
        oled.device.hide.assert_called()
        oled.device.show.assert_not_called()

    def test_turning_helper_off_during_the_night_window_leaves_the_panel_dark(self):
        oled = self._night(screen_off_until_button=False)
        store = {SCREEN_OFF_ENTITY: _on_off("on")}
        _attach(oled, store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(1000.0, INSIDE):
                oled._service_once(1000.0)
            self.assertTrue(oled.display_blanked)
            self.assertTrue(oled.night_sleep_active)
            oled.device.show.assert_not_called()

            store[SCREEN_OFF_ENTITY] = _on_off("off")
            with _Frozen(1000.0 + NIGHT_SLEEP_HELPER_INTERVAL, INSIDE):
                oled._service_once(1000.0 + NIGHT_SLEEP_HELPER_INTERVAL)
        self.assertFalse(oled._effective_screen_off())
        self.assertTrue(oled.night_sleep_active)
        self.assertTrue(oled.display_blanked)
        oled.device.show.assert_not_called()

    def test_window_end_follows_the_effective_value_both_ways(self):
        # Option true, helper off: the panel comes back on when the window ends.
        wakes = self._night(screen_off_until_button=True)
        wake_store = {SCREEN_OFF_ENTITY: _on_off("off")}
        _attach(wakes, wake_store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(1000.0, INSIDE):
                wakes._service_once(1000.0)
            self.assertTrue(wakes.display_blanked)
            self.assertTrue(wakes.night_sleep_active)
            with _Frozen(1001.0, MORNING):
                wakes._service_once(1001.0)
        self.assertFalse(wakes.night_sleep_active)
        self.assertFalse(wakes._effective_screen_off())
        self.assertTrue(wakes.screen_off_until_button)
        self.assertFalse(wakes.display_blanked)
        wakes.device.show.assert_called()

        # Option false, helper on: a dark panel stays dark until the next press.
        stays = self._night(screen_off_until_button=False)
        stay_store = {SCREEN_OFF_ENTITY: _on_off("on")}
        _attach(stays, stay_store)
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"):
            with _Frozen(1000.0, INSIDE):
                stays._service_once(1000.0)
            self.assertTrue(stays.display_blanked)
            stays.device.show.reset_mock()
            with _Frozen(1001.0, MORNING):
                stays._service_once(1001.0)
        self.assertFalse(stays.night_sleep_active)
        self.assertTrue(stays._effective_screen_off())
        self.assertFalse(stays.screen_off_until_button)
        self.assertTrue(stays.display_blanked)
        stays.device.show.assert_not_called()
        stays.device.hide.assert_called()


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestScreenOffHelperBoot(unittest.TestCase):
    """Boot uses the effective value, before the credits splash."""

    def _run_boot(self, oled, when, store):
        _attach(oled, store)
        oled.gpio_line = None
        oled.show_credits = True
        oled.credits_shown = False

        def stop(_current_time):
            raise KeyboardInterrupt

        oled._service_once = stop
        with patch("argon_oled.SUPERVISOR_TOKEN", "token"), \
                patch("argon_oled.time.time", return_value=1000.0), \
                patch("argon_oled.local_now", return_value=when), \
                patch("argon_oled.time.sleep", lambda _dt: None):
            oled.run()

    def test_helper_off_does_not_boot_blank_when_the_option_is_true(self):
        oled = make_oled(
            screen_off_until_button=True,
            display_blanked=True,
            night_sleep_enabled=False,
            show_credits=True,
            credits_shown=False,
        )
        self._run_boot(oled, DAY, {SCREEN_OFF_ENTITY: _on_off("off")})
        self.assertFalse(oled._effective_screen_off())
        self.assertTrue(oled.screen_off_until_button)
        self.assertFalse(oled.display_blanked)
        self.assertFalse(oled.night_sleep_active)
        oled.device.show.assert_called()
        oled.renderer.draw_credits.assert_called_once()

    def test_helper_on_boots_dark_when_the_option_is_false(self):
        oled = make_oled(
            screen_off_until_button=False,
            display_blanked=False,
            night_sleep_enabled=False,
            show_credits=True,
            credits_shown=False,
        )
        self._run_boot(oled, DAY, {SCREEN_OFF_ENTITY: _on_off("on")})
        self.assertTrue(oled._effective_screen_off())
        self.assertFalse(oled.screen_off_until_button)
        self.assertTrue(oled.display_blanked)
        oled.device.hide.assert_called()
        oled.renderer.draw_credits.assert_not_called()
        self.assertTrue(oled.credits_shown)

    def test_restart_inside_the_night_window_still_boots_dark(self):
        oled = make_oled(
            night_sleep_enabled=True,
            night_sleep_start="22:00",
            night_sleep_end="06:30",
            screen_off_until_button=False,
            display_blanked=False,
            show_credits=True,
            credits_shown=False,
        )
        self._run_boot(oled, INSIDE, {SCREEN_OFF_ENTITY: _on_off("off")})
        self.assertTrue(oled.night_sleep_active)
        self.assertFalse(oled._effective_screen_off())
        self.assertTrue(oled.display_blanked)
        oled.device.hide.assert_called()
        oled.device.show.assert_not_called()
        oled.renderer.draw_credits.assert_not_called()
        self.assertTrue(oled.credits_shown)


@unittest.skipUnless(ARGON_AVAILABLE, "argon_oled module required")
class TestScreenOffHelperDocs(unittest.TestCase):
    def test_version_and_entity_id_are_documented_next_to_night_sleep(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for name in ("DOCS.md", "README.md", "CHANGELOG.md", "translations/en.yaml"):
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("input_boolean.oled_screen_off_until_button", text, name)
            self.assertIn("input_boolean.oled_night_sleep", text, name)
        with open(os.path.join(root, "config.yaml"), encoding="utf-8") as handle:
            config = handle.read()
        self.assertIn("version: 1.20.0", config)
        self.assertIn("screen_off_until_button: false", config)
        self.assertIn("screen_off_until_button: bool", config)
        self.assertIn("idle_dim_timeout: 60", config)
        self.assertIn("homeassistant_api: true", config)
        self.assertNotIn("oled_idle_dim_timeout", config)


if __name__ == "__main__":
    unittest.main()
