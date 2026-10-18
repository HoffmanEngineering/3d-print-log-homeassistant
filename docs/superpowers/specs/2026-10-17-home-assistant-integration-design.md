# 3D Print Log Home Assistant integration, v1: design

- **Date:** 2026-10-17
- **Tracking issue:** HoffmanEngineering/3d-print-log-api#236
- **Status:** approved in chat on 2026-10-17, including the changes from the independent design review.

## 1. Purpose

Bring 3D Print Log's records into Home Assistant: printers, prints, loaded filament, maintenance and account totals. These are things HA's live printer integrations (Moonraker, OctoPrint, Bambu, PrusaLink) don't have.

v1 must:
- work on a LAN-only HA, with nothing exposed to the internet;
- not duplicate or conflict with the community Spoolman integration (`Disane87/spoolman-homeassistant`), which keeps providing per-spool sensors through our `/spoolman` API.

Live printer state (temperatures, progress, camera) stays with the printer integrations.

**Success looks like this:** someone installs the integration through HACS and pastes an API key. They get a device per printer that shows what's printing, what's loaded and how much is left, plus a maintenance status. They can build an automation for "print finished" or "print failed", or "not enough filament for this print", without writing YAML.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Auth: API key** (`X-Api-Key` header) in v1. Device pairing (#148) is added later as a second `auth_type`. | #148 isn't built. The entry stores `auth_type` from day one, so pairing doesn't need a migration. |
| D2 | **Events are derived from snapshot changes** in v1. Webhooks come in v1.1. | API keys can't register webhooks (`WebhooksController` is InteractiveUserOnly), production refuses LAN targets, and the settings page (ui#339) isn't built. |
| D3 | **Repo** `HoffmanEngineering/3d-print-log-homeassistant`, public, MIT, HACS. **Domain `printlog`.** | A domain starting with a digit (`3d_print_log`) has no working Python precedent in core (`3_day_blinds` is virtual), and tests can't `import` it. |
| D4 | **No per-spool entities** in v1. The Spoolman integration keeps doing that. | Avoids duplicate entities. The id namespace `…:spool:<guid>` is reserved, so they can be added later. |
| D5 | **Event detection uses a seen-set** per printer, not "the last id seen". | `lastFinishedPrint` is the newest by **StartDate** (`SyncSnapshotService.cs:337`). Deleting a print or adding a back-dated one changes it without any print finishing. |
| D6 | **Everything versioned from v1:** config entry version, store version, event payload `version`. Ids are scoped by environment. | Later additions shouldn't need breaking migrations (see §9). |

## 3. API changes (3d-print-log-api, one PR, all additive)

`schemaVersion` stays `1`, because these fields are additive. Every new field is computed in the existing snapshot query and its caches. JSON is camelCase.

### 3.1 `SyncPrinter` additions

| Field | Type | Meaning |
|---|---|---|
| `printTimeSeconds` | `long` | All-time print time on this printer: the same figure as `GET /api/analytics/printers` `printTimeSeconds` with no date filter. |
| `lastMaintenance` | `{ id: Guid, date: DateTimeOffset, category: string?, description: string? }` or null | The newest `PrinterMaintenance` with `Done = true`, by `Date`, then by `Id`. |
| `openMaintenance` | `{ count: int, nextDate: DateTimeOffset? }` | `Done = false` rows. `nextDate` is the earliest `Date` among them. `count` is 0 and `nextDate` is null when there are none. Never null itself. |

### 3.2 Print record additions

**`SyncCurrentPrint` and `SyncFinishedPrint`** both gain:

| Field | Type | Meaning |
|---|---|---|
| `source` | `string` | `Print.Source` as snake_case: `unknown`, `web`, `slicer_plugin`, `octoprint`, `moonraker`, `mcp`, `api_key`. |
| `url` | `string?` | `{Email:WebBaseUrl}/prints/{id}`, the rule the webhooks use (`WebhookDeliveryProcessor.cs:285-295`). Null when `WebBaseUrl` is unset. |

**`SyncCurrentPrint`** also gains `filament`. It is an array of `{ slot: int?, spoolId: Guid?, estimatedGrams: double? }`: the current print's `PrintFilament` rows, with `EstimatedAmountMg / 1000` rounded to 0.1 g. It is in slot order, with no-slot rows last, and is empty when the print has none.

### 3.3 `SyncAccountTotals` additions

| Field | Type | Meaning |
|---|---|---|
| `successCount` | `int` | Prints started this month with status Success. |
| `failedCount` | `int` | Prints started this month with status Failed. Canceled and partial prints are counted in neither. |

### 3.4 New filament endpoints

Both are plain `[Authorize]`, so API keys work, and both count against the connector write budget automatically. They sit on `FilamentsController`.

**`POST /api/Filaments/{id:guid}/adjustments`**
- **Body:** `{ "source": "weight" | "length" | "volume", "delta": number, "notes": string? }`. `notes` is at most 1000 characters.
- **Logic:** wraps `FilamentService.AdjustMaterialRemainingForMcp`, with the same rules. A positive delta adds and a negative one removes. The unit is grams for weight, mm for length and ml for volume.
- **200 response:** `{ "beforeGrams": number, "afterGrams": number }`.
- **400 (problem details):**
  - for an untracked material (no initial weight);
  - for a result below 0 or above capacity;
  - for an invalid source;
  - for a delta of 0 or one that isn't finite.
- **404:** when the material isn't the caller's.
- **Cache:** bumps the cache version, as the MCP path does.

**`PUT /api/Filaments/{id:guid}/active`**
- **Body:** `{ "isActive": bool }`.
- **Logic:** wraps `SetMaterialActiveForMcp`.
- **Responses:** 204 on success, 404 when the material isn't the caller's.

### 3.5 Not changed

- Webhook registration stays InteractiveUserOnly.
- Unread notifications stay out of the snapshot, because they don't bump the version.

The integration must keep working against an API **without** these fields (v1.17.0 in prod today). Every new field is optional to the parser, and the entities that need a missing field aren't created (§5.4).

## 4. Integration architecture

### 4.1 Repo layout

```
custom_components/printlog/
  __init__.py        setup/unload entry, migrate stub, register services in async_setup
  manifest.json      domain printlog, config_flow, iot_class cloud_polling, integration_type hub,
                     dependencies [], requirements [], version == release tag
  const.py
  api.py             PrintLogClient: aiohttp, no HA imports; typed errors
  models.py          dataclasses parsed from snapshot JSON (frozen, __eq__), unknown fields ignored
  coordinator.py     SnapshotCoordinator, SlowCoordinator
  events.py          pure diff: (previous state, snapshot, now) -> (events, new state)
  config_flow.py     user, reauth, reconfigure, options
  entity.py          PrintLogEntity base: device info, unique ids, availability
  sensor.py
  binary_sensor.py
  event.py           per-printer "Print" event entity
  services.py        adjust_remaining, set_spool_active, log_maintenance
  services.yaml
  diagnostics.py     async_redact_data
  repairs.py         (issue creation helpers only; no fixable flows in v1)
  icons.json
  translations/en.json
  brand/icon.png, icon@2x.png, logo.png, logo@2x.png (+ dark_ variants if needed)
hacs.json            {"name": "3D Print Log", "homeassistant": "2026.10.0", "render_readme": true}
tests/               pytest-homeassistant-custom-component
.github/workflows/   validate.yml (hacs/action + hassfest), tests.yml, release.yml
README.md, CHANGELOG.md, LICENSE (MIT), pyproject.toml (pytest/ruff config), requirements_test.txt
```

Every module has one job. `api.py`, `models.py` and `events.py` never import `homeassistant`, so they get plain unit tests.

### 4.2 Config entry

- **`VERSION = 1`, `MINOR_VERSION = 1`.** `async_migrate_entry` exists from v1, with a test, and rejects versions it doesn't know.
- **`data`:** `{ "base_url": str, "auth_type": "api_key", "api_key": str }`.
- **`options`:** `{ "scan_interval": int }`, in seconds, default 30, range 15–300.
- **`unique_id`:** `f"{host}:{user_id}"`.
  - `host` is the lower-cased host of `base_url`, plus the port if it isn't the default.
  - `user_id` comes from `GET /api/Users/me` `id`.
  - Scoping by host keeps a local dev API and prod from colliding.
- **Title:** `DisplayName` from `/api/Users/me`, or "3D Print Log" when that is empty.

### 4.3 Config flow

- **`user` step:** asks for the API key, plus the API URL with default `https://api.3dprintlog.com`.
  - The description tells the user to create a dedicated key named "Home Assistant" under Settings → API keys.
  - The step validates with `GET /api/Users/me`. Errors: `invalid_auth` (401), `cannot_connect`, `unknown`.
  - It aborts with `already_configured` on a duplicate unique id.
- **`reauth` / `reauth_confirm`:** a new key for the same account. A different account aborts with `wrong_account`.
- **`reconfigure`:** change the URL and/or key. The new values must resolve to the same unique id.
- **Options flow:** `scan_interval`.
- **Never shown:** the API key appears nowhere in a URL, `configuration_url`, device identifiers, unique ids, titles, logs or diagnostics. `configuration_url` is `https://www.3dprintlog.com/printers/{id}` when `base_url` is the production API (`api.3dprintlog.com`), and is left unset for any other API.

### 4.4 Client (`api.py`)

- `PrintLogClient(session, base_url, api_key)` uses HA's shared aiohttp session, with a timeout of 15 s per request.
- **Methods:**
  - `get_me()`
  - `get_version(etag)`, which returns `(version, etag)` or `NOT_MODIFIED`
  - `get_snapshot(time_zone, etag)`, which returns `(json, etag)` or `NOT_MODIFIED`
  - `get_unread_count()`
  - `adjust_remaining(spool_id, source, delta, notes)`
  - `set_spool_active(spool_id, is_active)`
  - `create_maintenance(printer_id, date, category, description, notes)`, which POSTs `/api/PrinterMaintenance` with `done: true`
- **Errors:**
  - `PrintLogAuthError` for 401 and 403.
  - `PrintLogRateLimitError(retry_after)` for 429.
  - `PrintLogNotFoundError` for 404.
  - `PrintLogValidationError(message)` for 400, carrying the problem `detail` or `title`.
  - `PrintLogConnectionError` for timeouts, connection failures and 5xx.
  - `PrintLogUnsupportedError` for a 404 on a new endpoint against an older API. Only the two filament endpoints raise it.

### 4.5 Coordinators

`entry.runtime_data` is a `PrintLogData` dataclass: `{ client, snapshot: SnapshotCoordinator, slow: SlowCoordinator, store }`. More coordinators can be added later without touching the existing entities.

**`SnapshotCoordinator`** (`DataUpdateCoordinator[Snapshot]`, `always_update=False`, interval = `scan_interval`). Each tick:
1. `GET /api/sync/version` with `If-None-Match`. A 304 means the version hasn't changed.
2. Fetch the snapshot (with `If-None-Match` set to the last snapshot ETag, and `timeZone` set to `hass.config.time_zone`) when any of these holds:
   - the version changed;
   - this is the first refresh;
   - five minutes have passed since the last snapshot fetch. The version doesn't change when a connector goes online → stale or when the month rolls over; only the snapshot ETag does.
3. A 304 on the snapshot returns the previous data. A 200 is parsed into `models.Snapshot`.
4. **Schema check:** if `schemaVersion > 1`, create the repair issue `unsupported_schema` ("update the integration") and raise `UpdateFailed`. The issue is cleared when a supported version is seen.
5. Run `events.diff()` (§6) and fire whatever it returns, then save the store.

**Errors:**
- An auth error raises `ConfigEntryAuthFailed`, which starts reauth.
- A rate-limit error raises `UpdateFailed` and pushes the next refresh back by `retry_after` (at least the interval).
- A connection error raises `UpdateFailed`. Entities become unavailable, and the coordinator logs once when they do and once when they recover.

**`SlowCoordinator`** (interval 300 s): `GET /api/Notifications/unread-count`. Its failures make only the unread-notifications sensor unavailable, never the printer entities.

**Stale devices:** after each successful snapshot, any device of this entry whose printer id is no longer in `printers[]` is removed from the device registry. The account device is never removed.

### 4.6 Ids

| Thing | Format |
|---|---|
| Config entry unique id | `{host}:{user_id}` |
| Printer device identifier | `("printlog", f"{entry_uid}:printer:{printer_id}")` |
| Account device identifier | `("printlog", f"{entry_uid}:account")` |
| Reserved (later) | `…:spool:{guid}` |
| Printer entity unique id | `f"{entry_uid}:printer:{printer_id}:{key}"` |
| Slot entity unique id | `f"{entry_uid}:printer:{printer_id}:slot:{n}:{key}"` |
| Account entity unique id | `f"{entry_uid}:account:{key}"` |

Slot entities are keyed on the slot number, never on the spool, so swapping a spool keeps the entity and its history. Every `key` is specific (`last_maintenance`, never `maintenance`).

## 5. Entities

Every entity sets `has_entity_name = True` and a `translation_key`, has its icon in `icons.json`, and declares `PARALLEL_UPDATES = 0`.

### 5.1 Printer device

**Device info:**
- **Name:** the printer's name, or "Printer {id}" when it has none.
- **Manufacturer and model:** `make` and `model`.
- **`configuration_url`:** see §4.3.
- An inactive printer still gets a device; its entities report normally.

**Binary sensors**

| Key | Platform / class | State | Attributes |
|---|---|---|---|
| `connection` | binary_sensor / connectivity | on = `connection.status == "online"`. Off when stale. Unknown (None) when there is no connector. | `kind` |
| `printing` | binary_sensor / running | on when `currentPrint` is not null | — |
| `maintenance_due` | binary_sensor / problem | on when `openMaintenance.nextDate` is at or before now | `open_count`, `next_date` |
| `slot_{n}_filament_short` ("Not enough filament") | binary_sensor / problem | See below. | `estimated_grams`, `remaining_grams` |

`slot_{n}_filament_short` is on when a current print is running, its `filament` row for slot n has an `estimatedGrams`, and the loaded spool's `remainingGrams` is below it. It is off when remaining is at least the estimate. It is unknown when there's no print, no estimate or an untracked spool.

**Sensors**

| Key | Platform / class | State | Attributes |
|---|---|---|---|
| `current_print` | sensor | the print's title, or "Untitled" when it has none; None when nothing is printing | `print_id`, `source`, `url`, `connection_stale` (bool) |
| `print_started` | sensor / timestamp | `currentPrint.startedAt` | — |
| `print_estimated_end` | sensor / timestamp | `currentPrint.estimatedEndAt` | — |
| `last_print_result` | sensor / enum | `success`, `failed`, `canceled` or `partial_success` | `print_id`, `title`, `finished_at`, `source`, `url` |
| `slot_{n}_filament` | sensor | the spool's name, or `material`, or "Spool #{spoolNumber}" | `material`, `color_hex`, `colors`, `spool_id`, `spool_number`, `slot_label` |
| `slot_{n}_remaining` | sensor / weight, g, measurement | `remainingGrams`; None when the spool is untracked or the slot is empty | `spool_id` |
| `print_time` | sensor / duration, h, total_increasing | `printTimeSeconds / 3600`, rounded to 0.1 | — |
| `last_maintenance` | sensor / timestamp | `lastMaintenance.date` | `category`, `description` |

**Slots.**
- **How many:** slots run 1 to `max(slotCount, 1)`. Slot sensors are created for every slot number at setup, and again when `slotCount` grows (new entities are added on refresh).
- **Unslotted spools:** a loaded spool with `slot = null` counts as slot 1 when `slotCount <= 1` and slot 1 is otherwise empty. In every other case, unslotted spools are listed in the `unslotted` attribute of `slot_1_filament`.

### 5.2 Per-printer event entity

| Item | Value |
|---|---|
| Key | `print` |
| Event types | `started`, `success`, `failed`, `canceled`, `partial_success` |
| Data | the bus payload (§6.3) without `device_id` |

It fires in the same pass as the bus events.

### 5.3 Account device (`DeviceEntryType.SERVICE`)

| Key | Platform / class | State | Attributes |
|---|---|---|---|
| `prints_this_month` | sensor, total, `last_reset` = start of `month` in `time_zone` | `account.printCount` | `month`, `time_zone`, `success_count`, `failed_count` |
| `grams_this_month` | sensor / weight, g, total, same `last_reset` | `account.grams` | `month` |
| `unread_notifications` | sensor, measurement (from SlowCoordinator) | `unreadCount` | `unread_achievements` |

### 5.4 Older APIs

If a printer's snapshot has none of the new fields (§3), these entities aren't created for it at setup, and they are added once the fields appear:
- `print_time`, `last_maintenance` and `maintenance_due`;
- the slot "Not enough filament" binary sensors.

The `source` and `url` attributes are None, and the event payload carries them as null.

## 6. Events

### 6.1 Persisted state

The store is `Store(hass, STORAGE_VERSION=1, f"printlog.{entry_id}.events", minor_version=1)`, with a migrate function. Per printer it holds:

```
{ "seen": [print_id, ...]   # bounded to the 50 most recently added
  "current": print_id | null }
```

The store is loaded before the first refresh, saved after each diff that changes it (with a 10 s delay), and removed in `async_remove_entry`.

### 6.2 `events.diff(prev, snapshot, now) -> (events, state)` (pure function)

For each printer:
1. **First time this printer is seen** (no state): seed `seen` with the `lastFinishedPrint.id`, if there is one, and set `current` to the `currentPrint.id`. Fire nothing. A fresh install or a new printer never announces history.
2. **Started:** if `currentPrint` is set, its id differs from `prev.current`, and the id isn't in `seen`, fire `started`.
3. **Finished:** let `f = lastFinishedPrint`. Fire `f.status` (`success`, `failed`, `canceled` or `partial_success`) only if all of these hold:
   - `f` is set and `f.id` isn't in `seen`;
   - either `f.id == prev.current`, or the reference time is within the last 60 minutes of `now`. The reference time is `f.finishedAt`, or `f.startedAt` when there is no finish time.

   Add `f.id` to `seen` whether or not anything fired.
4. Set `current` to `currentPrint.id`, or null. An id that was current and is now neither current nor the last finished print (deleted, or not the newest by start date) fires nothing.

The diff runs only on a successful 200 snapshot, never on errors or 304s. A printer that disappears from the snapshot has its state dropped.

**Accepted limits** (documented in the README; v1.1 webhooks fix them):
- If two prints on the same printer finish between polls, only the one that is newest by start date is seen.
- A print shorter than the poll interval may fire only its finish, without a start.

### 6.3 Bus events

| Status | Event name |
|---|---|
| `started` | `printlog_print_started` |
| `success` | `printlog_print_finished` |
| `failed`, `canceled`, `partial_success` | `printlog_print_failed` |

The names are permanent. Payload (additive only, documented):

```
{ "version": 1, "entry_id", "device_id", "printer_id", "printer_name",
  "print_id", "status", "title", "started_at", "finished_at", "source", "url" }
```

`finished_at` is null for `started`. Timestamps are ISO 8601 strings.

## 7. Actions (services)

Services are registered once in `async_setup`, described in `services.yaml`, and named and described in translations. All three are registered with `SupportsResponse.OPTIONAL`.

**Common field:** `config_entry_id`. It is optional and uses a config entry selector filtered to `printlog`. It is required when more than one entry is loaded; otherwise the single loaded entry is used.

### 7.1 `printlog.adjust_remaining` (returns `{before_grams, after_grams}`)

**Choosing the spool:** exactly one of these is required.
- `entity_id`: a `printlog` `slot_{n}_filament` or `slot_{n}_remaining` sensor. It resolves to the spool loaded in that slot from the current snapshot, and to that sensor's entry.
- `spool_id`: a GUID.
- `spool_number`: an int, resolved through `snapshot.spools`.

**Other fields:**
- `amount`: a required float, not zero. Positive adds and negative removes.
- `unit`: `g` (default), `mm` or `ml`. These map to weight, length and volume.
- `notes`: optional, at most 1000 characters.

**Errors** (`ServiceValidationError` with a translation key):
- an empty slot;
- an unknown spool;
- an untracked spool (checked locally when `remainingGrams` is null and the spool is in the snapshot);
- an API validation message;
- an API too old for this action (`HomeAssistantError`).

After success, the integration requests a coordinator refresh.

### 7.2 `printlog.set_spool_active`

Takes the same spool choice as 7.1, plus `active: bool` (required). It returns nothing useful, so the response is `{}`.

### 7.3 `printlog.log_maintenance` (returns `{maintenance_id}`)

**Fields:**
- `device_id`: required, a `printlog` printer device.
- `category`: optional, at most 256 characters.
- `description`: required, at most 1000 characters.
- `notes`: optional, at most 1000 characters.
- `date`: an optional datetime; defaults to now.
- `maintenance_id`: optional, **reserved**. In v1 it is rejected with "not supported yet" if given, so the schema doesn't change when closing open items is added later.

**What it does:** creates a `PrinterMaintenance` record with `done: true` (it works on API v1.17.0), then refreshes.

## 8. Diagnostics, repairs, quality

- **Diagnostics:**
  - Entry data and options, with `api_key` redacted.
  - The last snapshot, with titles, names, descriptions, notes and storage locations redacted, plus `url`.
  - Coordinator state (last ETags, last fetch, last error) and the event store.
- **Repairs:** `unsupported_schema` (§4.5).
- **Quality scale:** aim for Silver: config flow tests, unique ids, `has_entity_name`, `runtime_data`, test-before-configure and test-before-setup, unique config entry, action setup, unloading, entity-unavailable, log-when-unavailable, parallel updates, reauth, and test coverage of at least 95%. From Gold: diagnostics, reconfigure, entity translations, exception translations, icon translations, a repair issue, devices and stale devices.

## 9. Extensibility (later work that this design leaves room for)

| Later item | How it fits |
|---|---|
| Device pairing (#148) | A new `auth_type` value plus a config-flow step. The client takes a credential provider. |
| Webhooks (v1.1) | Store the `webhook_id` and secret in entry data (a `MINOR_VERSION` bump). Dedupe against the seen-sets by (print id, status). A delivery requests a refresh. It needs either the user to paste the URL into the web app (ui#339) or a new API path for connectors to register their own webhook. |
| Per-spool entities, dry-box humidity | A device namespace `…:spool:{guid}`, keyed on the GUID, never the spool number. |
| Maintenance intervals and "due", a to-do entity | New specific keys. `log_maintenance.maintenance_id` is already reserved. |
| Energy per print | A new key in the event payload; `version` stays 1. |
| Connector diagnostics, bridge `update` entity | `SlowCoordinator` polls `GET /api/Connections`, which accepts API keys. |
| Analytics sensors (cost, runway) | A third coordinator in `PrintLogData`. |

## 10. Testing

- **Tests:** `pytest-homeassistant-custom-component==0.13.373` (HA 2026.10.2, Python 3.14), `pytest-asyncio`, syrupy snapshots, `aioclient_mock`.
- **Where they run:**
  - Locally in Docker (`python:3.14-slim`, with the repo mounted and the pip cache on D:), because the dev machine is Windows and HA's test package doesn't run there.
  - In CI on `ubuntu-latest` with Python 3.14.
- **Pure units (no HA):**
  - `models` parsing, including old-API payloads with fields missing.
  - `events.diff`: seed, start, finish, delete, back-dated print, restart window, a finish with no `finishedAt`, the seen-set bound and printer removal.
  - `api` status mapping, using `aioclient_mock`.
- **HA tests:**
  - Every config flow path.
  - Setup and unload.
  - The polling algorithm: a 304 on the version, the forced snapshot after 5 minutes, schemaVersion handling, auth failure → reauth, 429.
  - Entity states, with syrupy snapshots for a fixture snapshot and an old-API snapshot.
  - Stale device removal.
  - Event entity and bus events.
  - Each service, including errors and slot-entity resolution.
  - Diagnostics redaction.
  - Migration.
- **Coverage:** at least 95%, enforced in CI.
- **CI:** `validate.yml` (`hacs/action` with category integration, plus `home-assistant/actions/hassfest`) on push, PRs and nightly; `tests.yml` (ruff + pytest + coverage); `release.yml` (on a published release, fail if the manifest `version` doesn't equal the tag without its `v`).
- **API:** xUnit integration tests next to `PrintLogApi.IntegrationTests/Sync/` and the filament controller tests, covering every new field, every endpoint path, and the ETag/version behavior when maintenance or adjustments change.
- **By hand, before release:**
  - Install into `D:\Development\homeassistant-dev` (copy into `config/custom_components/printlog`) against a local API worktree on port 5101 (`http://host.docker.internal:5101`).
  - Screenshot the device pages and check events in Developer Tools.
  - Nothing is installed on the NAS.

## 11. Delivery

1. **API PR** (3d-print-log-api): §3. Linked to #236.
2. **Integration PR** (`feat/v0.1.0`): this spec, the plan, the scaffold, CI, and every module in §4–§8, with README and CHANGELOG. Release `v0.1.0` (a GitHub release) after both PRs merge and the API deploys. Tags and releases are made only when Chris asks.
3. **UI docs PR** (3d-print-log-ui, draft until v0.1.0 is released): a "Home Assistant integration" docs page, cross-linked with the Spoolman guide (which to use for what).

## 12. Out of scope for v1

Webhooks; device pairing; per-spool entities; closing open maintenance items; progress % (the printer integrations do it better); maintenance to-do lists; calendar, image and camera entities; cost, inventory value and runway; achievement events; device triggers (the event entity covers them); connector diagnostics.
