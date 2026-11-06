# Igloo AlgoPIN

Home Assistant custom integration that creates hourly and daily [AlgoPIN](https://docs.igloohome.co/home/algopin-codes) guest codes for an igloohome account.

This is an integration. It is not a Home Assistant add-on. An add-on runs in its own container and cannot install files into the host `/config/custom_components` directory. Nothing in this repository registers the integration as an add-on, and nothing writes it into Home Assistant for you.

## Install

Home Assistant 2025.1 or newer.

1. Copy the folder `custom_components/igloohome_algopin` to `/config/custom_components/igloohome_algopin`. The path has to be exactly that. Restarting Home Assistant does not create it.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Igloo AlgoPIN**.
4. Enter the client ID and client secret from an [individual igloohome API](https://docs.igloohome.co/home/individual-account) client (OAuth client credentials).
5. Choose one or more locks from the list the account returns. The list is `GET https://api.igloohome.co/home/devices/support-pins`. There is no built-in or sample lock. If that call returns nothing, setup stops.

A zip of this folder, with `custom_components/igloohome_algopin` at the zip root, is the manual install artifact. Unzip it so Home Assistant sees `/config/custom_components/igloohome_algopin/manifest.json`.

Credentials are stored in the config entry, which is the normal Home Assistant store for integration secrets. Generated PINs are not stored there.

## Not a HACS install from this repository

`evski/home-assistant-addons` is a Home Assistant **add-on** repository (`repository.json`). Adding that URL in HACS, or in the add-on store, does not install this integration.

HACS installs a custom integration only from a repository whose root is an integration repository (its own `hacs.json`, and `custom_components/<domain>` on the default branch or in a release zip). This add-on repository does not have a root `hacs.json` on purpose. `igloohome-algopin-install/hacs.json` is a starting file for a **separate** integration repository. It does nothing while it sits in this folder.

Do not describe this as a one-click HACS install.

## What you can do

- Create an hourly AlgoPIN (1 to 672 hours, variance 1–3).
- Create a daily AlgoPIN (29 to 367 whole days, variance 1–3).
- Ask a Bridge that the account device list already linked to the lock to delete a PIN.
- See API billing status and the `endsAt` time from `GET /home/server-health`. Those sensors do not contain a PIN.

The PIN comes back only as the service response (`SupportsResponse.ONLY`):

| Service | Response |
| --- | --- |
| `igloohome_algopin.create_hourly_algopin` | `pin`, `pin_id` |
| `igloohome_algopin.create_daily_algopin` | `pin`, `pin_id` |
| `igloohome_algopin.revoke_algopin` | `job_id`, `completed`, `job_status` |

The integration does not write the PIN to an entity state, the logbook, a Home Assistant event, or its own Store. It does not email the PIN.

Home Assistant still keeps the service response in the trace of the script or automation that called the service. Treat that trace as sensitive. This integration cannot stop core from recording the caller's response.

## Times

Enter start and end in the Home Assistant timezone. Naive values are read in that timezone. The integration converts the instant to UTC and sends `YYYY-MM-DDTHH:00:00+00:00`.

The [hourly](https://docs.igloohome.co/home/api_access) and [daily](https://docs.igloohome.co/home/api_access) endpoints accept hour resolution only. A converted time that is not on a UTC hour is rejected, not rounded. Hourly length must be 1 to 672 hours, and the start may be at most 1 hour in the past. Daily length must be 29 to 367 whole UTC days, and the UTC hour of the start and the end must match (the daily reference requires those hours to match).

A window that crosses a daylight-saving change can fail the daily check even when both local clock hours match, because the UTC hours then differ. Zones whose offset is not a whole hour (for example Asia/Kolkata, UTC+05:30) need a local time that lands on a UTC hour. `14:00` in Kolkata is `08:30Z` and is rejected. `14:30` is `09:00Z` and is a valid hourly boundary.

Example, Europe/London in July (BST, UTC+1): local `2026-07-01 15:00` through `2026-07-30 15:00` is sent as `2026-07-01T14:00:00+00:00` through `2026-07-30T14:00:00+00:00` (29 days).

## Cloud generate versus Bridge delete

Creating an hourly or daily AlgoPIN is a cloud request. The lock does not need Wi-Fi, and a Bridge is not required. The guest types the PIN on the keypad. The lock checks it offline. That is the behaviour described in [AlgoPIN codes](https://docs.igloohome.co/home/algopin-codes).

Deleting a PIN is not part of that cloud call, and it does not happen by itself.

The home [Jobs API](https://docs.igloohome.co/home/api_jobs) deletes a PIN with a Bridge-proxied job:

`POST https://api.igloohome.co/home/devices/{deviceId}/jobs/bridges/{bridgeId}`

```json
{ "jobType": 5, "jobData": { "pin": "..." } }
```

Job type 5 is "Delete PIN code". The Bridge delivers it to the lock over Bluetooth. `igloohome-api` 0.1.2 names that constant `BRIDGE_JOB_DELETE_CUSTOM_PIN` and its value is 5. This integration sends that job through `Auth.request` to the home API URL above.

If the Bridge is offline the API returns HTTP 406 (`Unable to contact bridge as it appears to be offline`) and the lock is unchanged. There is no documented home-API call that removes an AlgoPIN from a lock the Bridge cannot reach. The mobile SDK `deletePin` also deletes over Bluetooth from a phone in range. This integration cannot do that.

An unactivated AlgoPIN whose start is more than 256 hours after it was created cannot be deleted (the documented 256 rule). The lock's whitelist and blacklist hold at most 199 PIN codes (the 199 rule). Codes valid for more than 24 hours must be used once on the lock within the first 24 hours of validity or they do not activate.

`GET /home/jobs/{jobId}` reports the bridge job (`completed`, `jobStatus`). The separate webhook payload uses job status 3 for "PIN not found". This integration returns the job id and those status fields. It does not treat "job accepted" as "PIN removed from the lock", and it does not poll until the lock confirms deletion.

`DELETE /home/pass/{id}` revokes an iglooKey pass, not an AlgoPIN. This integration does not call it.

The AlgoPIN create endpoints do not document an `Idempotency-Key`, and `igloohome-api` 0.1.2 has no idempotent AlgoPIN method. A repeat create is a new POST. Variance 1, 2, and 3 are the extra codes the hourly and daily endpoints allow for one window. This integration does not invent an idempotency key.

## Lock model

The official supported-devices table (home API, last updated 15 October 2026) is the model list: <https://docs.igloohome.co/home/supported-devices>.

| Prefix | Official name | AlgoPIN |
| --- | --- | --- |
| IGB4 | Deadbolt 2S Metal Grey | Yes |
| OE1X | Retrofit Lock | No |

There is no row named "Smart Deadbolt 4". The Metal Grey deadbolt prefix on that table is **IGB4**, officially **Deadbolt 2S Metal Grey**, and AlgoPIN is yes. The example device id `IGB403fe3b9d` is labelled "Deadbolt 2S" on the same page. **OE1X Retrofit Lock does not support AlgoPIN.** A device id is only given that model name after it has been read from the account and you choose it. Unknown prefixes stay blank rather than being guessed. The brand shown is igloohome.

The built-in [igloohome integration](https://www.home-assistant.io/integrations/igloohome/) documents Bridge lock/unlock and battery updates. It does not document AlgoPIN. This integration does not add lock, unlock, or battery entities, so it does not shadow that one.

## Python package

`igloohome-api` **0.1.2** (PyPI, 19 July 2026) is the pinned release. Verified from that sdist:

- `Auth` is async and posts client credentials to `https://auth.igloohome.co/oauth2/token`. The default scope includes `igloohomeapi/algopin-hourly` and `igloohomeapi/algopin-daily`.
- Public `Api` methods are `get_devices`, `get_device_info`, `create_bridge_proxied_job`, and `get_job_status`. There is no hourly or daily AlgoPIN method.
- Those `Api` methods still call `https://api.igloodeveloper.co/igloohome/...`. The current home reference uses `https://api.igloohome.co/home/...`.
- `is_access_token_valid` does not return true, so this integration reads the JWT `exp` claim and refreshes about a minute early. Documented client-credentials tokens last 86400 seconds and have no refresh token.

HTTP calls are awaited. They are not run in an executor.

## API account, price, and what this change did not do

The published individual flow is self-serve client credentials, with a 30-day trial. After the trial, igloohome's pricing page and support article charge **USD $2 per active smart lock per month**, billed in arrears. HTTP 402 is the billing failure (`30 days igloohome API trial has ended` is the documented example).

This repository change did not create an iglooaccess client, did not start a trial, did not enter a card, and did not submit an approval request. No live lock and no live API account were called while building it. If a particular account is still waiting on igloohome to enable API access, that step is still outstanding.

## Tests

Unit tests cover UTC conversion, hourly and daily limits, mocked hourly/daily/auth/billing errors, token reuse, and the 0.1.2 package surface. They use a fake HTTP session. They do not call igloohome and they do not contain real credentials.

Full Home Assistant config-flow tests were not part of this package. They need a Home Assistant install and were not given a fake pass.
