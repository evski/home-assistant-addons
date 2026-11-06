"""igloohome home API client built on igloohome-api 0.1.2.

The package is async (aiohttp). Callers await it on the event loop. Nothing
here performs synchronous HTTP, so these coroutines stay on the event loop.

What 0.1.2 actually ships was read from the sdist ``igloohome_api/__init__.py``,
not from the PyPI description. That description only shows ``get_devices()``
and does not document AlgoPIN. The module itself has no hourly or daily
AlgoPIN method. The scopes string does mention ``algopin-hourly`` and
``algopin-daily``. AlgoPIN calls below are therefore plain requests to the
documented home API, using ``Auth.request`` for the bearer token.

* ``Auth`` posts client credentials to ``https://auth.igloohome.co/oauth2/token``.
  The default scope includes ``igloohomeapi/algopin-hourly`` and
  ``igloohomeapi/algopin-daily``.
* ``Api.get_devices``, ``Api.get_device_info``, ``Api.create_bridge_proxied_job``,
  and ``Api.get_job_status`` exist.
* No hourly, daily, onetime, or permanent AlgoPIN method exists.
* Those ``Api`` methods build ``https://api.igloodeveloper.co/igloohome/...``.
  The current home reference uses ``https://api.igloohome.co/home/...``.
* ``is_access_token_valid`` does not return True (``jwt.decode`` is called
  without a key, and a successful decode would still fall off the end of the
  function). ``ExpiryAwareAuth`` reads the ``exp`` claim instead.
* ``BRIDGE_JOB_DELETE_CUSTOM_PIN`` is 5, matching the documented delete job.
* No idempotency key and no idempotent AlgoPIN method exist. This client
  does not invent one. A repeat create is another POST.

Revoke uses the documented bridge job, via ``Auth.request``, not
``Api.create_bridge_proxied_job``, because the package method targets the
legacy host. Creating an AlgoPIN does not contact the lock. Deleting one
does, and only through a Bridge that can reach the lock over Bluetooth.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from aiohttp import ClientError, ClientSession
from igloohome_api import (
    BRIDGE_JOB_DELETE_CUSTOM_PIN,
    Auth,
    AuthException,
)

from .const import API_ROOT, TOKEN_SKEW
from .models import is_offered, lookup_model
from .snapshot import AccountSnapshot, LockView

_LOGGER = logging.getLogger(__name__)

_DEVICE_ID = re.compile(r"^[A-Za-z0-9]{4,32}$")


class IglooError(Exception):
    """Base error whose message is safe to show. It never includes a PIN or secret."""


class IglooAuthError(IglooError):
    """Client credentials or the access token were rejected."""


class IglooPaymentError(IglooError):
    """Trial ended or billing is required (HTTP 402)."""


class IglooConnectionError(IglooError):
    """The auth host or the API host could not be reached."""


class IglooApiError(IglooError):
    """Any other unsuccessful API response."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Device:
    """A device object from GET /home/devices or GET /home/devices/support-pins."""

    device_id: str
    device_name: str
    device_type: str
    linked_ids: tuple[str, ...]


@dataclass(frozen=True)
class AlgoPin:
    """Transient create result. ``repr`` hides the PIN so a log of the object does not."""

    pin: str
    pin_id: str

    def __repr__(self) -> str:
        return f"AlgoPin(pin_id={self.pin_id!r}, pin='[redacted]')"


@dataclass(frozen=True)
class RevokeResult:
    """Bridge delete-job acknowledgement. The PIN is not echoed."""

    job_id: str
    completed: bool | None
    job_status: int | None
    op_result: int | None


@dataclass(frozen=True)
class Health:
    """Fields from GET /home/server-health that are safe to store."""

    status: str
    ends_at: datetime | None
    tier: str | None


def read_token_expiry(access_token: str) -> datetime | None:
    """Read the JWT ``exp`` claim without verifying the signature.

    The token was just returned by ``https://auth.igloohome.co`` over HTTPS.
    igloohome-api 0.1.2 cannot check expiry itself.
    """

    try:
        payload_b64 = access_token.split(".")[1]
    except IndexError:
        return None
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = int(payload["exp"])
    except (ValueError, KeyError, json.JSONDecodeError, TypeError):
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc)


class ExpiryAwareAuth(Auth):
    """``Auth`` that refreshes a client-credentials token before ``exp``."""

    def __init__(self, session: ClientSession, client_id: str, client_secret: str) -> None:
        super().__init__(session=session, client_id=client_id, client_secret=client_secret)
        self.expires_at: datetime | None = None

    async def async_get_access_token(self) -> str:
        try:
            token = await super().async_get_access_token()
        except AuthException as err:
            raise IglooAuthError("igloohome rejected the client credentials") from err
        except ClientError as err:
            raise IglooConnectionError("could not reach the igloohome auth server") from err
        self.expires_at = read_token_expiry(token)
        return token

    async def async_get_valid_access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self.access_token and self.expires_at and now < self.expires_at - TOKEN_SKEW:
            return self.access_token
        return await self.async_get_access_token()


def _check_device_id(device_id: str) -> str:
    if not _DEVICE_ID.fullmatch(device_id or ""):
        raise IglooApiError("device id is not a valid igloohome id", 400)
    return device_id


def _safe_detail(payload: object, secret: str | None = None) -> str:
    """Error text for the UI. PIN values and 4–9 digit runs are removed."""

    message = "igloohome API request failed"
    secrets: list[str] = []
    if secret:
        secrets.append(secret)
    if isinstance(payload, dict):
        for key in ("error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                message = value.strip()
                break
        for key, value in payload.items():
            if key.lower() in {"pin", "pinid", "newpin"} and isinstance(value, str) and value:
                secrets.append(value)
    for item in secrets:
        message = message.replace(item, "[redacted]")
    message = re.sub(r"\d{4,9}", "[redacted]", message)
    return message[:300]


def _is_bridge(device: Device) -> bool:
    if device.device_type.lower() == "bridge":
        return True
    info = lookup_model(device.device_id)
    return info is not None and info.kind == "bridge"


def bridge_ids_by_lock(locks: list[Device], devices: list[Device]) -> dict[str, tuple[str, ...]]:
    """Bridge ids seen on the device list, associated with each lock.

    A bridge counts only when the device list returned it. Links are read in
    both directions, including when the AlgoPIN list and the full device list
    disagree about ``linkedDevices`` for the same id.
    """

    by_id = {device.device_id: device for device in devices}
    bridges = {device.device_id: device for device in devices if _is_bridge(device)}
    found: dict[str, tuple[str, ...]] = {}
    for lock in locks:
        ids: list[str] = []
        sources = [lock]
        listed = by_id.get(lock.device_id)
        if listed is not None and listed is not lock:
            sources.append(listed)
        for source in sources:
            for linked in source.linked_ids:
                if linked in bridges and linked not in ids:
                    ids.append(linked)
        for bridge in bridges.values():
            if lock.device_id in bridge.linked_ids and bridge.device_id not in ids:
                ids.append(bridge.device_id)
        found[lock.device_id] = tuple(ids)
    return found


def _parse_device(raw: object) -> Device | None:
    """Read one ``payload`` object from the devices or support-pins list.

    Official and observed objects also carry ``id``, ``batteryLevel``,
    ``pairedAt``, and ``homeId``. Those stay on the wire. The picker and the
    stored config entry use ``deviceId``, ``deviceName``, ``type``, and
    ``linkedDevices`` only, so a home id and a battery reading are not stored.
    """

    if not isinstance(raw, dict):
        return None
    device_id = raw.get("deviceId")
    if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
        return None
    name = raw.get("deviceName")
    device_type = raw.get("type")
    linked: list[str] = []
    for item in raw.get("linkedDevices") or []:
        if isinstance(item, dict) and isinstance(item.get("deviceId"), str):
            linked.append(item["deviceId"])
    return Device(
        device_id=device_id,
        device_name=name.strip() if isinstance(name, str) and name.strip() else device_id,
        device_type=device_type if isinstance(device_type, str) else "",
        linked_ids=tuple(linked),
    )


def _parse_ends_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class IglooClient:
    """Home API calls. The access token stays on the auth object, in memory only."""

    def __init__(self, session: ClientSession, client_id: str, client_secret: str) -> None:
        self._auth = ExpiryAwareAuth(session, client_id, client_secret)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        secret: str | None = None,
    ) -> dict:
        url = f"{API_ROOT}{path}"
        _LOGGER.debug("igloohome %s %s", method, path)
        kwargs: dict = {}
        if json_body is not None:
            kwargs["json"] = json_body
        if params is not None:
            kwargs["params"] = params
        try:
            response = await self._auth.request(method, url, **kwargs)
        except IglooError:
            raise
        except ClientError as err:
            raise IglooConnectionError("could not reach the igloohome API") from err
        except AuthException as err:
            raise IglooAuthError("igloohome rejected the client credentials") from err

        try:
            payload = await response.json(content_type=None)
        except (json.JSONDecodeError, ValueError, TypeError):
            payload = None

        status = response.status
        if status == 401:
            raise IglooAuthError("igloohome rejected the access token")
        if status == 402:
            raise IglooPaymentError(_safe_detail(payload, secret))
        if status != 200:
            raise IglooApiError(_safe_detail(payload, secret), status)
        if not isinstance(payload, dict):
            raise IglooApiError("igloohome returned an unexpected response", status)
        return payload

    async def _list_devices(self, path: str) -> list[Device]:
        devices: list[Device] = []
        cursor = ""
        for _page in range(20):
            params: dict[str, object] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            payload = await self._request("GET", path, params=params)
            for item in payload.get("payload") or []:
                device = _parse_device(item)
                if device is not None:
                    devices.append(device)
            cursor = payload.get("nextCursor") or ""
            if not isinstance(cursor, str) or not cursor:
                return devices
        raise IglooApiError("device list did not finish within 20 pages", 500)

    async def list_algopin_devices(self) -> list[Device]:
        """GET /home/devices/support-pins, every page.

        This is the setup discovery call. It does not create an AlgoPIN and
        it does not call the jobs API.
        """

        return await self._list_devices("/devices/support-pins")

    async def list_devices(self) -> list[Device]:
        """GET /home/devices, every page. Used to find Bridges. The lock is not contacted."""

        return await self._list_devices("/devices")

    async def server_health(self) -> Health:
        """GET /home/server-health. Status and endsAt only."""

        payload = await self._request("GET", "/server-health")
        status = payload.get("status")
        tier = payload.get("tier")
        return Health(
            status=status if isinstance(status, str) and status else "unknown",
            ends_at=_parse_ends_at(payload.get("endsAt")),
            tier=tier if isinstance(tier, str) and tier else None,
        )

    async def _create(self, kind: str, device_id: str, access_name: str, window_start: str, window_end: str, variance: int) -> AlgoPin:
        payload = await self._request(
            "POST",
            f"/devices/{_check_device_id(device_id)}/algopin/{kind}",
            json_body={
                "accessName": access_name,
                "startDate": window_start,
                "endDate": window_end,
                "variance": variance,
            },
        )
        pin = payload.get("pin")
        pin_id = payload.get("pinId")
        if not isinstance(pin, str) or not pin:
            raise IglooApiError("AlgoPIN response did not include a pin", 200)
        return AlgoPin(pin=pin, pin_id=pin_id if isinstance(pin_id, str) else "")

    async def create_hourly(
        self,
        device_id: str,
        access_name: str,
        start_api: str,
        end_api: str,
        variance: int,
    ) -> AlgoPin:
        """POST /home/devices/{id}/algopin/hourly.

        The igloohome cloud returns the PIN. This client does not run a local
        algorithm and does not contact the lock or a Bridge.
        """

        return await self._create("hourly", device_id, access_name, start_api, end_api, variance)

    async def create_daily(
        self,
        device_id: str,
        access_name: str,
        start_api: str,
        end_api: str,
        variance: int,
    ) -> AlgoPin:
        """POST /home/devices/{id}/algopin/daily. Does not contact the lock."""

        return await self._create("daily", device_id, access_name, start_api, end_api, variance)

    async def revoke(self, device_id: str, bridge_id: str, pin: str) -> RevokeResult:
        """Ask a Bridge to delete a PIN over Bluetooth.

        This is job type 5 (``BRIDGE_JOB_DELETE_CUSTOM_PIN``). The cloud does
        not remove the code from a lock the Bridge cannot reach. HTTP 406
        means the Bridge was offline and the lock was not changed.

        The published 256-hour rule (an unactivated AlgoPIN whose start is
        more than 256 hours after creation cannot be deleted) is not applied
        here. Create responses do not include a stored creation time, and
        this client does not keep PINs. Submitting the job does not guarantee
        that the lock deleted the code.
        """

        if BRIDGE_JOB_DELETE_CUSTOM_PIN != 5:
            raise IglooApiError("igloohome-api delete-job constant is not 5", 500)
        payload = await self._request(
            "POST",
            f"/devices/{_check_device_id(device_id)}/jobs/bridges/{_check_device_id(bridge_id)}",
            json_body={
                "jobType": BRIDGE_JOB_DELETE_CUSTOM_PIN,
                "jobData": {"pin": pin},
            },
            secret=pin,
        )
        job_id = payload.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            raise IglooApiError("bridge job response did not include a job id", 200)

        completed: bool | None = None
        job_status: int | None = None
        op_result: int | None = None
        status_payload = None
        if re.fullmatch(r"[A-Za-z0-9]{8,64}", job_id):
            try:
                status_payload = await self._request("GET", f"/jobs/{job_id}")
            except IglooAuthError:
                raise
            except IglooError:
                status_payload = None
        if isinstance(status_payload, dict):
            if isinstance(status_payload.get("completed"), bool):
                completed = status_payload["completed"]
            job_response = status_payload.get("jobResponse")
            if isinstance(job_response, dict):
                raw_status = job_response.get("jobStatus")
                if isinstance(raw_status, int):
                    job_status = raw_status
                op = job_response.get("opResult")
                if isinstance(op, dict) and isinstance(op.get("result"), int):
                    op_result = op["result"]
        return RevokeResult(
            job_id=job_id,
            completed=completed,
            job_status=job_status,
            op_result=op_result,
        )

    async def fetch_snapshot(self, chosen_ids: set[str]) -> AccountSnapshot:
        """Health plus the chosen locks that support-pins still returns."""

        health = await self.server_health()
        algopin_devices = await self.list_algopin_devices()
        all_devices = await self.list_devices()
        by_id = {device.device_id: device for device in algopin_devices}
        eligible = [device for device in algopin_devices if is_offered(device.device_id, device.device_type)]
        bridges = bridge_ids_by_lock(eligible, all_devices)
        locks: list[LockView] = []
        for device_id in sorted(chosen_ids):
            device = by_id.get(device_id)
            if device is None or not is_offered(device.device_id, device.device_type):
                continue
            info = lookup_model(device.device_id)
            locks.append(
                LockView(
                    device_id=device.device_id,
                    device_name=device.device_name,
                    device_type=device.device_type,
                    table_prefix=info.prefix if info and info.algopin else None,
                    bridge_ids=bridges.get(device.device_id, ()),
                )
            )
        return AccountSnapshot(
            status=health.status,
            ends_at=health.ends_at,
            tier=health.tier,
            locks=tuple(locks),
            devices_fresh=True,
        )
