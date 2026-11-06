"""Mocked home API. No network and no real credentials."""

from __future__ import annotations

import base64
import json
import logging
import time

import pytest

from custom_components.igloohome_algopin.api import (
    API_ROOT,
    AlgoPin,
    IglooApiError,
    IglooAuthError,
    IglooClient,
    IglooPaymentError,
    bridge_ids_by_lock,
)
from custom_components.igloohome_algopin.api import Device
from custom_components.igloohome_algopin.const import API_ROOT as CONST_ROOT
from custom_components.igloohome_algopin.schedule import validate_window
from custom_components.igloohome_algopin.snapshot import diagnostics_payload
from datetime import datetime, timezone

import igloohome_api
from igloohome_api import BRIDGE_JOB_DELETE_CUSTOM_PIN, Api

PIN = "1357902"
PIN_ID = "TESTPINID"
SECRET = "test-secret"
CLIENT = "test-client"
LOCK = "IGB403fe3b9d"
BRIDGE = "EB1X01abc123"
LEGACY = "api.igloodeveloper.co"


def _b64(value: dict) -> str:
    raw = json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(exp: int) -> str:
    return f"{_b64({'alg': 'none'})}.{_b64({'exp': exp})}.x"


class FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload


class FakeSession:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple] = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._reply("POST", url)

    async def request(self, method, url, **kwargs):
        self.calls.append((method.upper(), url, kwargs))
        return self._reply(method.upper(), url, kwargs)

    def _reply(self, method, url, kwargs=None):
        if LEGACY in url:
            raise AssertionError(f"legacy host was called: {url}")
        key = (method, url.split("?")[0])
        if key not in self.routes:
            raise AssertionError(f"unexpected {method} {url}")
        route = self.routes[key]
        if callable(route):
            return route(kwargs or {})
        return route


def token_route(exp_offset: int = 3600):
    def route(_kwargs):
        return FakeResponse(200, {"access_token": make_token(int(time.time()) + exp_offset), "expires_in": 86400})

    return route


def client_with(routes) -> tuple[IglooClient, FakeSession]:
    session = FakeSession(routes)
    return IglooClient(session, CLIENT, SECRET), session


def _device(device_id, name, device_type="Lock", linked=None):
    return {
        "deviceId": device_id,
        "deviceName": name,
        "type": device_type,
        "linkedDevices": [{"deviceId": item, "type": "Bridge"} for item in (linked or [])],
    }


@pytest.mark.asyncio
async def test_hourly_and_daily_posts_use_home_api_and_do_not_keep_the_pin(caplog):
    caplog.set_level(logging.DEBUG)
    pages = {"support": 0}

    def support(_kwargs):
        pages["support"] += 1
        if pages["support"] == 1:
            return FakeResponse(
                200,
                {"nextCursor": "page-2", "payload": [_device(LOCK, "Front door")]},
            )
        return FakeResponse(
            200,
            {
                "nextCursor": "",
                "payload": [
                    _device("OE1X01cd3a5f", "Retrofit"),
                    _device("ZZZZ01abcdef", "Unknown keypad", "Keypad"),
                ],
            },
        )

    def devices(_kwargs):
        return FakeResponse(
            200,
            {
                "nextCursor": "",
                "payload": [
                    _device(LOCK, "Front door", linked=[BRIDGE]),
                    _device(BRIDGE, "Hall bridge", "Bridge"),
                    _device("OE1X01cd3a5f", "Retrofit"),
                ],
            },
        )

    routes = {
        ("POST", "https://auth.igloohome.co/oauth2/token"): token_route(),
        ("POST", f"{API_ROOT}/devices/{LOCK}/algopin/hourly"): FakeResponse(200, {"pin": PIN, "pinId": PIN_ID}),
        ("POST", f"{API_ROOT}/devices/{LOCK}/algopin/daily"): FakeResponse(200, {"pin": PIN, "pinId": PIN_ID}),
        ("GET", f"{API_ROOT}/devices/support-pins"): support,
        ("GET", f"{API_ROOT}/devices"): devices,
        ("GET", f"{API_ROOT}/server-health"): FakeResponse(
            200,
            {"status": "active", "endsAt": "2026-12-01T00:00:00Z", "tier": "paid", "clientId": CLIENT},
        ),
    }
    client, session = client_with(routes)
    window = validate_window(
        "hourly",
        datetime(2026, 1, 15, 14, 0),
        datetime(2026, 1, 15, 18, 0),
        timezone.utc,
        datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
    )

    hourly = await client.create_hourly(LOCK, "Guest stay", window.start_api, window.end_api, 1)
    daily = await client.create_daily(LOCK, "Guest stay", window.start_api, window.end_api, 2)

    assert isinstance(hourly, AlgoPin)
    assert hourly.pin == PIN
    assert hourly.pin_id == PIN_ID
    assert PIN not in repr(hourly)
    assert daily.pin == PIN
    body = session.calls[1][2]["json"]
    assert body == {
        "accessName": "Guest stay",
        "startDate": "2026-01-15T14:00:00+00:00",
        "endDate": "2026-01-15T18:00:00+00:00",
        "variance": 1,
    }
    assert session.calls[2][2]["json"]["variance"] == 2
    assert all(LEGACY not in call[1] for call in session.calls)
    assert PIN not in caplog.text
    assert SECRET not in caplog.text
    assert PIN not in repr(client.__dict__)

    snapshot = await client.fetch_snapshot({LOCK, "OE1X01cd3a5f", "ZZZZ01abcdef"})
    assert [lock.device_id for lock in snapshot.locks] == [LOCK, "ZZZZ01abcdef"]
    metal = next(lock for lock in snapshot.locks if lock.device_id == LOCK)
    assert metal.table_prefix == "IGB4"
    assert metal.device_type == "Lock"
    assert metal.bridge_ids == (BRIDGE,)
    unknown = next(lock for lock in snapshot.locks if lock.device_id == "ZZZZ01abcdef")
    assert unknown.table_prefix is None
    assert snapshot.status == "active"
    assert snapshot.tier == "paid"
    payload = diagnostics_payload(snapshot)
    encoded = json.dumps(payload)
    assert PIN not in encoded
    assert SECRET not in encoded
    assert "client_secret" not in encoded
    assert "access_token" not in encoded
    assert CLIENT not in encoded
    assert "Deadbolt" not in encoded
    assert "model" not in payload["locks"][0]


@pytest.mark.asyncio
async def test_token_is_reused_until_the_expiry_window():
    session = FakeSession(
        {
            ("POST", "https://auth.igloohome.co/oauth2/token"): token_route(3600),
            ("GET", f"{API_ROOT}/server-health"): FakeResponse(200, {"status": "trial", "endsAt": None, "tier": "trial"}),
        }
    )
    client = IglooClient(session, CLIENT, SECRET)
    first = await client.server_health()
    second = await client.server_health()
    assert first.status == second.status == "trial"
    token_posts = [call for call in session.calls if call[1].endswith("/oauth2/token")]
    assert len(token_posts) == 1

    session.routes[("POST", "https://auth.igloohome.co/oauth2/token")] = token_route(30)
    client._auth.expires_at = datetime.fromtimestamp(int(time.time()) + 30, tz=timezone.utc)
    await client.server_health()
    token_posts = [call for call in session.calls if call[1].endswith("/oauth2/token")]
    assert len(token_posts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "exc"),
    [
        (401, {"error": "unauthorized"}, IglooAuthError),
        (402, {"error": "30 days igloohome API trial has ended"}, IglooPaymentError),
        (400, {"error": f"bad window {PIN}", "pin": PIN}, IglooApiError),
        (403, {"error": "missing scope igloohomeapi/algopin-hourly"}, IglooApiError),
        (418, {"error": "this device type does not support the feature."}, IglooApiError),
    ],
)
async def test_create_errors_do_not_echo_a_pin_or_the_secret(status, payload, exc):
    client, _session = client_with(
        {
            ("POST", "https://auth.igloohome.co/oauth2/token"): token_route(),
            ("POST", f"{API_ROOT}/devices/{LOCK}/algopin/hourly"): FakeResponse(status, payload),
        }
    )
    with pytest.raises(exc) as caught:
        await client.create_hourly(LOCK, "Guest", "2026-01-15T14:00:00+00:00", "2026-01-15T18:00:00+00:00", 1)
    text = str(caught.value)
    assert PIN not in text
    assert SECRET not in text
    if status == 400:
        assert "bad window" in text
        assert "[redacted]" in text


@pytest.mark.asyncio
async def test_token_endpoint_failure_is_auth_and_hides_the_secret():
    client, _session = client_with(
        {("POST", "https://auth.igloohome.co/oauth2/token"): FakeResponse(401, {"error": "invalid_client"})}
    )
    with pytest.raises(IglooAuthError) as caught:
        await client.server_health()
    assert SECRET not in str(caught.value)


@pytest.mark.asyncio
async def test_revoke_posts_job_type_5_and_returns_status_without_the_pin():
    job_id = "6345191a82bcfc12a921bdb6"
    client, session = client_with(
        {
            ("POST", "https://auth.igloohome.co/oauth2/token"): token_route(),
            ("POST", f"{API_ROOT}/devices/{LOCK}/jobs/bridges/{BRIDGE}"): FakeResponse(200, {"jobId": job_id}),
            ("GET", f"{API_ROOT}/jobs/{job_id}"): FakeResponse(
                200,
                {
                    "jobId": job_id,
                    "completed": False,
                    "jobType": "DELETE_PIN",
                    "jobResponse": {"jobStatus": 1, "pin": PIN, "opResult": {"result": 0}},
                },
            ),
        }
    )
    result = await client.revoke(LOCK, BRIDGE, PIN)
    posted = session.calls[1][2]["json"]
    assert posted["jobType"] == BRIDGE_JOB_DELETE_CUSTOM_PIN == 5
    assert posted["jobData"] == {"pin": PIN}
    assert result.job_id == job_id
    assert result.completed is False
    assert result.job_status == 1
    assert PIN not in repr(result)
    assert PIN not in repr(client.__dict__)


@pytest.mark.asyncio
async def test_revoke_406_says_the_bridge_was_offline_without_the_pin():
    client, _session = client_with(
        {
            ("POST", "https://auth.igloohome.co/oauth2/token"): token_route(),
            ("POST", f"{API_ROOT}/devices/{LOCK}/jobs/bridges/{BRIDGE}"): FakeResponse(
                406,
                {"error": f"Unable to contact bridge, pin {PIN} unchanged"},
            ),
        }
    )
    with pytest.raises(IglooApiError) as caught:
        await client.revoke(LOCK, BRIDGE, PIN)
    assert caught.value.status == 406
    assert PIN not in str(caught.value)
    assert "Unable to contact bridge" in str(caught.value)


def test_bridge_links_are_only_bridges_the_device_list_returned():
    lock = Device(LOCK, "Front door", "Lock", (BRIDGE,))
    other = Device("IGP101abc123", "Padlock", "Lock", ())
    bridge = Device(BRIDGE, "Hall bridge", "Bridge", ("IGP101abc123",))
    linked = bridge_ids_by_lock([lock, other], [lock, other, bridge])
    assert linked[LOCK] == (BRIDGE,)
    assert linked["IGP101abc123"] == (BRIDGE,)


def test_package_surface_is_0_1_2_and_has_no_algopin_method(monkeypatch):
    import importlib.metadata as metadata

    assert metadata.version("igloohome-api") == "0.1.2"
    assert CONST_ROOT == "https://api.igloohome.co/home"
    assert igloohome_api._BASE_URL == "https://api.igloodeveloper.co"
    methods = {name for name, value in vars(Api).items() if callable(value) and not name.startswith("_")}
    assert methods == {"get_devices", "get_device_info", "create_bridge_proxied_job", "get_job_status"}
    assert "algopin-hourly" in igloohome_api._OAUTH2_SCOPE_EVERYTHING
    assert "algopin-daily" in igloohome_api._OAUTH2_SCOPE_EVERYTHING
    assert BRIDGE_JOB_DELETE_CUSTOM_PIN == 5

    monkeypatch.setattr(igloohome_api.jwt, "decode", lambda *args, **kwargs: {"exp": 9_999_999_999})
    assert igloohome_api.is_access_token_valid("header.payload.sig") is not True


def test_sdk_auth_methods_are_coroutines():
    import inspect

    assert inspect.iscoroutinefunction(igloohome_api.Auth.async_get_access_token)
    assert inspect.iscoroutinefunction(igloohome_api.Auth.request)
    assert inspect.iscoroutinefunction(Api.get_devices)
    assert inspect.iscoroutinefunction(Api.create_bridge_proxied_job)
