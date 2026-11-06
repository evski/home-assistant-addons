# Igloo AlgoPIN

Home Assistant custom integration that creates hourly and daily [AlgoPIN](https://docs.igloohome.co/home/algopin-codes) guest codes for an igloohome account.

There are two different installs, and only one of them applies here.

| What | Where it lives | How it is installed |
| --- | --- | --- |
| This integration | `custom_components/igloohome_algopin` | Copy that folder to `/config/custom_components/igloohome_algopin` and restart Home Assistant |
| The add-ons in this same git repository | `argon-oled-addon/`, `active-heating-manager-addon/` | Add-on store, from `repository.json` |

An add-on runs in its own container. It cannot write `/config/custom_components` on the host. Adding this git URL as an add-on repository does not install Igloo AlgoPIN. There is no add-on folder for it, and `repository.json` does not list it.

## Install the integration

Home Assistant 2025.1 or newer.

1. Copy `custom_components/igloohome_algopin` to `/config/custom_components/igloohome_algopin`. Home Assistant looks for `/config/custom_components/igloohome_algopin/manifest.json`.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Igloo AlgoPIN**.
4. Enter the client ID and client secret from an [individual igloohome API](https://docs.igloohome.co/home/individual-account) client (OAuth client credentials).
5. Choose devices from the list `GET https://api.igloohome.co/home/devices/support-pins` returns. There is no built-in lock. If that call returns nothing, setup stops. Setup does not create an AlgoPIN and does not call the jobs API.

The manual zip has `custom_components/igloohome_algopin` at the zip root plus this README. Unzip it so the folder lands in `/config/custom_components/`.

Credentials are stored in the config entry. Generated PINs are not.

## Not a HACS install from this repository

`evski/home-assistant-addons` is an add-on repository. It has no root `hacs.json`. HACS will not install this integration from that URL.

`igloohome-algopin-install/hacs.json` is a file you would put at the root of a **different** repository whose only job is this integration. It does nothing in this add-on repository.

## Check the device id before you rely on a retail name

The supported-devices page is <https://docs.igloohome.co/home/supported-devices> (table last updated 15 October 2026).

That page lists prefix **IGB4** as **Deadbolt 2S Metal Grey**, AlgoPIN yes, and prefix **OE1X** as **Retrofit Lock**, AlgoPIN no. It does not list a retail description "Smart Deadbolt 4 Metal Grey". This integration does not treat that retail text as a device, and it does not write a product model onto the Home Assistant device.

If you are not sure what the lock is:

1. Open the lock in the igloohome app, or the device list in the igloohome API portal.
2. Read the device id. It is the Bluetooth name, 12 characters, such as `IGB403fe3b9d`.
3. The prefix is the leading letters in that id (`IGB4` in the example, or `IGB4A` when the id starts with that longer prefix).
4. Compare the prefix with the supported-devices table. The setup screen shows the same device id and prefix. It does not show a model name.

Setup then applies the [devices reference](https://docs.igloohome.co/home/api_devices) and the supported-devices table.

`GET /home/devices/support-pins` returns AlgoPIN-capable devices. The 200 body is the same shape as `GET /home/devices`: `nextCursor` (an empty string ends the list) and `payload`. Each payload object documents `id`, `type` (Lock, Keypad, Bridge, Fob), `deviceId`, `deviceName`, `batteryLevel`, `pairedAt`, `homeId`, and `linkedDevices`. It does not document a model, SKU, or AlgoPIN flag, and none is invented. The picker stores `deviceId` and `deviceName` only. `id`, `batteryLevel`, `pairedAt`, and `homeId` are not written to the config entry, the device registry, or diagnostics, and this integration does not create a battery entity.

- Returned `type` Bridge or Fob is not offered.
- A device id whose prefix is AlgoPIN no on the published table (including OE1X) is not offered.
- A prefix the table marks AlgoPIN yes, with `type` missing, Lock, or Keypad, can be chosen. IGB4 is that case: the table names it Deadbolt 2S Metal Grey and marks AlgoPIN yes. The label says the prefix is on the table. It does not write that product name onto the Home Assistant device.
- Any other prefix stays selectable only because `support-pins` returned it, and the label tells you to read the device id in the account. No model is assigned.
- A diagnostic entity is created only for a lock that was selected and is still offered on a later `support-pins` refresh. Devices that were not selected do not get an entity.

## What you can do

- Create an hourly AlgoPIN (1 to 672 hours, variance 1–3). The igloohome cloud creates it. No Bridge is used. No PIN algorithm runs in Home Assistant.
- Create a daily AlgoPIN (29 to 367 whole days, variance 1–3), the same way.
- Ask a Bridge that the account device list already linked to submit delete job type 5.
- See API status and `endsAt` from `GET /home/server-health`. Those sensors do not contain a PIN.

The PIN comes back only as the service response (`SupportsResponse.ONLY`):

| Service | Response |
| --- | --- |
| `igloohome_algopin.create_hourly_algopin` | `pin`, `pin_id` |
| `igloohome_algopin.create_daily_algopin` | `pin`, `pin_id` |
| `igloohome_algopin.revoke_algopin` | `job_id`, `completed`, `job_status` |

The integration does not write the PIN to an entity state, the logbook, a Home Assistant event, or its own Store. It does not email the PIN.

Home Assistant still keeps the service response in the trace of the script or automation that called the service. Treat that trace as sensitive.

## Times

Enter start and end in the Home Assistant timezone. Naive values are read in that timezone. The integration converts the instant to UTC and sends `YYYY-MM-DDTHH:00:00+00:00`.

The [hourly](https://docs.igloohome.co/home/api_access) and [daily](https://docs.igloohome.co/home/api_access) endpoints accept hour resolution only. A converted time that is not on a UTC hour is rejected, not rounded. Hourly length must be 1 to 672 hours, and the start may be at most 1 hour in the past. Daily length must be 29 to 367 whole UTC days, and the UTC hour of the start and the end must match.

A window that crosses a daylight-saving change can fail the daily check even when both local clock hours match, because the UTC hours then differ. Zones whose offset is not a whole hour (for example Asia/Kolkata, UTC+05:30) need a local time that lands on a UTC hour. `14:00` in Kolkata is `08:30Z` and is rejected. `14:30` is `09:00Z` and is a valid hourly boundary.

Example, Europe/London in July (BST, UTC+1): local `2026-07-01 15:00` through `2026-07-30 15:00` is sent as `2026-07-01T14:00:00+00:00` through `2026-07-30T14:00:00+00:00` (29 days).

## Cloud generate versus Bridge delete

Creating an hourly or daily AlgoPIN is a request to `POST /home/devices/{deviceId}/algopin/hourly` or `.../algopin/daily`. The cloud returns `pin` and `pinId`. The lock is not on that call, a Bridge is not required, and this integration does not compute the PIN itself. The guest types the PIN on the keypad. The lock checks it offline. That split is what [AlgoPIN codes](https://docs.igloohome.co/home/algopin-codes) describes.

Deleting a PIN is a different call, and it is not guaranteed.

The home [Jobs API](https://docs.igloohome.co/home/api_jobs) deletes a PIN with a Bridge-proxied job:

`POST https://api.igloohome.co/home/devices/{deviceId}/jobs/bridges/{bridgeId}`

```json
{ "jobType": 5, "jobData": { "pin": "..." } }
```

Job type 5 is "Delete PIN code". The Bridge delivers it to the lock over Bluetooth. `igloohome-api` 0.1.2 names that constant `BRIDGE_JOB_DELETE_CUSTOM_PIN` and its value is 5. This integration sends that job through `Auth.request` to the home API URL above. It does not call `Api.create_bridge_proxied_job`, because that method still targets `https://api.igloodeveloper.co/igloohome/...`.

The jobs reference returns HTTP 406 when the Bridge is offline or the lock is not linked to that Bridge. The lock is unchanged. An empty `linkedDevices` array means this integration will not submit a delete job, because no Bridge id was on the device list. There is no documented home-API call that removes an AlgoPIN from a lock the Bridge cannot reach. The mobile SDK `deletePin` also deletes over Bluetooth from a phone in range. This integration cannot do that.

[AlgoPIN limitations](https://docs.igloohome.co/home/algopin-codes) include a 256-hour rule: an unactivated AlgoPIN whose start is more than 256 hours after it was created cannot be deleted. This integration does not apply that rule. The create response used here is `pin` and `pinId` only, those values are not stored, and no creation timestamp is kept to measure 256 hours. Submitting job type 5 does not mean the lock deleted the code. `GET /home/jobs/{jobId}` is returned as `job_id`, `completed`, and `job_status` only. The separate webhook payload uses job status 3 for "PIN not found". This integration does not poll until the lock confirms deletion.

The same limitations page also describes a 199-code capacity and a 24-hour activation window for codes longer than 24 hours. Those are igloohome rules. This integration does not enforce them locally.

`DELETE /home/pass/{id}` revokes an iglooKey pass, not an AlgoPIN. This integration does not call it.

The AlgoPIN create endpoints do not document an `Idempotency-Key`, and the 0.1.2 source has no idempotent AlgoPIN method. A repeat create is a new POST. This integration does not add an idempotency key.

## Python package

`igloohome-api` **0.1.2** (PyPI, 19 July 2026) is the pinned release. The PyPI description only demonstrates `get_devices()` and does not document AlgoPIN. The sdist module `igloohome_api/__init__.py` was read directly:

- `Auth` is async and posts client credentials to `https://auth.igloohome.co/oauth2/token`. The default scope string includes `igloohomeapi/algopin-hourly` and `igloohomeapi/algopin-daily`.
- `Api` methods are `get_devices`, `get_device_info`, `create_bridge_proxied_job`, and `get_job_status`. There is no AlgoPIN method to call.
- Those `Api` methods still build `https://api.igloodeveloper.co/igloohome/...`. The current home reference uses `https://api.igloohome.co/home/...`, which is what this integration calls via `Auth.request`.
- `is_access_token_valid` does not return true, so this integration reads the JWT `exp` claim and refreshes about a minute early. Documented client-credentials tokens last 86400 seconds and have no refresh token.

HTTP calls are awaited on the event loop.

The built-in [igloohome integration](https://www.home-assistant.io/integrations/igloohome/) documents Bridge lock/unlock and battery updates. Its documentation does not list AlgoPIN. This integration does not add lock, unlock, or battery entities. Its diagnostic sensors are attached only to the selected lock.

## API account

The published individual flow is self-serve client credentials. igloohome's own pricing and support pages describe a 30-day trial and, after that, USD $2 per active smart lock per month. HTTP 402 is the documented billing failure.

This change did not create an API client, did not start a trial, did not enter a card, and did not call igloohome or a lock. No credentials from a real account are in the repository. It was not copied onto a Home Assistant host.

## Tests

Unit tests cover UTC conversion, hourly and daily limits, the prefix check (including that a retail name is not a device id and that prefix IGB4 is Deadbolt 2S Metal Grey, AlgoPIN yes), mocked hourly/daily/auth/billing errors, token reuse, and the 0.1.2 module surface. They use a fake HTTP session.

Discovery tests feed a sanitized `support-pins` body with the same keys as the public device object (`nextCursor`, `id`, `type`, `deviceId`, `deviceName`, `batteryLevel`, `pairedAt`, `homeId`, `linkedDevices`). The fixture device id is `IGB4test` and the fixture name is `42battery`. Those tests call `GET /home/devices/support-pins` only, plus the token request. They do not call an AlgoPIN or jobs URL. `id`, `batteryLevel`, `pairedAt`, and `homeId` are not copied onto the device.

Full Home Assistant config-flow tests were not run. Home Assistant is not installed in the environment that ran the unit tests, and those tests were not marked passed. The flow steps in source are the client-credentials step, then a multi-select built from `list_algopin_devices()`. `manifest.json` requires `igloohome-api==0.1.2` and sets `config_flow` to true.
