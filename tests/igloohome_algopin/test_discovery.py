"""Discovery only. The support-pins fixture uses the public object shape.

No AlgoPIN create call and no jobs call. The device id and name are
sanitized stand-ins, not an account's deployed device.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from custom_components.igloohome_algopin.api import API_ROOT, Device
from custom_components.igloohome_algopin.models import is_offered, lookup_model
from custom_components.igloohome_algopin.snapshot import AccountSnapshot, LockView, entity_locks

from test_api import FakeResponse, client_with, token_route

DEVICE_ID = "IGB4test"
DEVICE_NAME = "42battery"
HOME_ID = "homeTest"
INTERNAL_ID = "1000000000001"


def live_shaped_lock() -> dict:
    """Same keys as GET /home/devices/support-pins. Values are not from an account."""

    return {
        "id": INTERNAL_ID,
        "type": "Lock",
        "deviceId": DEVICE_ID,
        "deviceName": DEVICE_NAME,
        "batteryLevel": 42,
        "pairedAt": "2024-01-01T00:00:00+00:00",
        "homeId": [HOME_ID],
        "linkedDevices": [],
    }


def test_fixture_uses_the_sanitized_public_shape():
    body = {"nextCursor": "", "payload": [live_shaped_lock()]}
    assert set(body) == {"nextCursor", "payload"}
    assert set(body["payload"][0]) == {
        "id",
        "type",
        "deviceId",
        "deviceName",
        "batteryLevel",
        "pairedAt",
        "homeId",
        "linkedDevices",
    }
    assert body["payload"][0]["deviceId"] == "IGB4test"
    assert body["payload"][0]["deviceName"] == "42battery"
    assert body["payload"][0]["batteryLevel"] == 42
    assert body["nextCursor"] == ""


@pytest.mark.asyncio
async def test_support_pins_discovery_reads_the_live_shape_and_does_not_request_a_pin():
    body = {"nextCursor": "", "payload": [live_shaped_lock()]}
    client, session = client_with(
        {
            ("POST", "https://auth.igloohome.co/oauth2/token"): token_route(),
            ("GET", f"{API_ROOT}/devices/support-pins"): FakeResponse(200, body),
        }
    )

    devices = await client.list_algopin_devices()

    assert [device.device_id for device in devices] == [DEVICE_ID]
    device = devices[0]
    assert device == Device(DEVICE_ID, DEVICE_NAME, "Lock", ())
    assert {field.name for field in dataclasses.fields(device)} == {
        "device_id",
        "device_name",
        "device_type",
        "linked_ids",
    }
    assert is_offered(device.device_id, device.device_type) is True
    info = lookup_model(device.device_id)
    assert info is not None
    assert info.prefix == "IGB4"
    assert info.algopin is True
    assert info.name == "Deadbolt 2S Metal Grey"
    encoded = json.dumps(dataclasses.asdict(device))
    assert HOME_ID not in encoded
    assert INTERNAL_ID not in encoded
    assert "batteryLevel" not in encoded
    assert "pairedAt" not in encoded
    assert not hasattr(device, "battery_level")
    urls = [call[1] for call in session.calls]
    assert urls == [
        "https://auth.igloohome.co/oauth2/token",
        f"{API_ROOT}/devices/support-pins",
    ]
    assert all("/algopin/" not in url and "/jobs/" not in url for url in urls)


@pytest.mark.asyncio
async def test_snapshot_keeps_only_the_selected_offered_lock():
    lock = live_shaped_lock()
    extra = [
        lock,
        {
            "id": "1000000000002",
            "type": "Bridge",
            "deviceId": "EB1Xtest",
            "deviceName": "42battery",
            "batteryLevel": 42,
            "pairedAt": "2024-01-01T00:00:00+00:00",
            "homeId": [HOME_ID],
            "linkedDevices": [],
        },
        {
            "id": "1000000000003",
            "type": "Lock",
            "deviceId": "OE1Xtest",
            "deviceName": "42battery",
            "batteryLevel": 42,
            "pairedAt": "2024-01-01T00:00:00+00:00",
            "homeId": [HOME_ID],
            "linkedDevices": [],
        },
    ]

    def support_pins(_kwargs):
        return FakeResponse(200, {"nextCursor": "", "payload": extra})

    client, session = client_with(
        {
            ("POST", "https://auth.igloohome.co/oauth2/token"): token_route(),
            ("GET", f"{API_ROOT}/devices/support-pins"): support_pins,
            ("GET", f"{API_ROOT}/devices"): FakeResponse(200, {"nextCursor": "", "payload": extra}),
            ("GET", f"{API_ROOT}/server-health"): FakeResponse(200, {"status": "active", "endsAt": None}),
        }
    )

    snapshot = await client.fetch_snapshot({DEVICE_ID, "EB1Xtest", "OE1Xtest", "IGB4other"})

    assert [lock.device_id for lock in snapshot.locks] == [DEVICE_ID]
    assert snapshot.locks[0].bridge_ids == ()
    assert snapshot.locks[0].device_name == DEVICE_NAME
    assert snapshot.locks[0].table_prefix == "IGB4"
    assert [lock.device_id for lock in entity_locks(snapshot)] == [DEVICE_ID]
    denied = AccountSnapshot(
        status="active",
        ends_at=None,
        tier=None,
        locks=(
            LockView("EB1Xtest", DEVICE_NAME, "Bridge", None, ()),
            LockView("OE1Xtest", DEVICE_NAME, "Lock", None, ()),
        ),
        devices_fresh=True,
    )
    assert entity_locks(denied) == ()
    encoded = json.dumps(
        {
            "device_id": snapshot.locks[0].device_id,
            "device_name": snapshot.locks[0].device_name,
            "bridge_ids": list(snapshot.locks[0].bridge_ids),
        }
    )
    assert HOME_ID not in encoded
    assert "batteryLevel" not in encoded
    assert all("/algopin/" not in call[1] and "/jobs/" not in call[1] for call in session.calls)
