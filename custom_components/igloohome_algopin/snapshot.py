"""Account snapshot shown in Home Assistant. PINs are not part of this data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_DEVICE_ID = re.compile(r"^[A-Za-z0-9]{4,32}$")


def parse_chosen(options: dict) -> tuple[tuple[str, str], ...]:
    """Lock ids saved from a setup GET. Accepts ``{device_id, device_name}`` or a bare id."""

    raw = options.get("locks") or []
    if not isinstance(raw, list):
        return ()
    chosen: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, str) and _DEVICE_ID.fullmatch(item):
            chosen.append((item, item))
            continue
        if not isinstance(item, dict):
            continue
        device_id = item.get("device_id")
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            continue
        name = item.get("device_name")
        chosen.append((device_id, name.strip() if isinstance(name, str) and name.strip() else device_id))
    return tuple(chosen)


@dataclass(frozen=True)
class LockView:
    """A lock the user chose from a real device list."""

    device_id: str
    device_name: str
    device_type: str
    table_prefix: str | None
    bridge_ids: tuple[str, ...]


@dataclass(frozen=True)
class AccountSnapshot:
    """Billing status and chosen locks. No credentials and no PIN."""

    status: str
    ends_at: datetime | None
    tier: str | None
    locks: tuple[LockView, ...]
    devices_fresh: bool


def diagnostics_payload(snapshot: AccountSnapshot) -> dict:
    """Diagnostics fields that are safe to download. No PIN, token, or secret."""

    return {
        "status": snapshot.status,
        "ends_at": snapshot.ends_at.isoformat() if snapshot.ends_at else None,
        "tier": snapshot.tier,
        "devices_fresh": snapshot.devices_fresh,
        "locks": [
            {
                "device_id": lock.device_id,
                "device_name": lock.device_name,
                "device_type": lock.device_type,
                "table_prefix": lock.table_prefix,
                "bridge_ids": list(lock.bridge_ids),
            }
            for lock in snapshot.locks
        ],
    }
