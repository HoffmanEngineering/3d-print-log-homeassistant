# 3D Print Log Home Assistant integration v1: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the additive API fields and endpoints (spec §3), and a HACS integration `printlog` v0.1.0 that implements spec §4–§8.

**Architecture:** There are two tracks, which don't depend on each other.
- **Track A (API, C#, Tasks 1–4):** extends the sync snapshot and adds two filament endpoints. Worktree `D:\Development\github\3d-print-log-api-worktrees\236-ha-sync`, branch `feat/236-ha-sync-fields`.
- **Track B (integration, Python, Tasks 5–16):** worktree `D:\Development\github\3d-print-log-homeassistant-worktrees\v0.1`, branch `feat/v0.1.0`.
  - It is built bottom-up: pure modules first (`models`, `api`, `events`), then the HA glue.
  - It parses every field from Track A as optional, so it runs against today's API.

**Tech Stack:**
- **API:** .NET 10, EF Core, xUnit, the integration tests in `PrintLogApi.IntegrationTests`.
- **Integration:** Python 3.14, Home Assistant 2026.10, `pytest-homeassistant-custom-component==0.13.373`, syrupy, ruff.

**Spec:** `docs/superpowers/specs/2026-10-17-home-assistant-integration-design.md` (integration repo). Every task implicitly includes the spec sections it cites.

## Global Constraints

- **Domain** `printlog`. **Event names** `printlog_print_started`, `printlog_print_finished` and `printlog_print_failed` are permanent.
- **API key:** sent only as the `X-Api-Key` header. Never put it in a URL, unique id, device identifier, title, log line or diagnostics.
- **Id formats** are exactly as in spec §4.6. Entry unique id `{host}:{user_id}`; host is lower-cased, with the port only if it isn't the default.
- **Config entry** `VERSION=1, MINOR_VERSION=1`. Data `{base_url, auth_type: "api_key", api_key}`; options `{scan_interval}` (default 30, range 15–300).
- **Default `base_url`:** `https://api.3dprintlog.com`.
- **Store:** `STORAGE_VERSION=1`, minor 1, key `printlog.{entry_id}.events`. The seen-set holds at most 50 ids per printer.
- **Polling:** the snapshot is forced every 300 s; `SlowCoordinator` runs every 300 s; HTTP timeout 15 s.
- **Events:** the restart window is 60 minutes, and the event payload has `version: 1`.
- **Spelling:** American English in code, UI text, docs and commits.
- **Integration tests** run in Docker: `scripts/test.sh` (Task 5). CI uses ubuntu-latest with Python 3.14. Coverage is at least 95%.
- **API tests:** `dotnet test --verbosity quiet` from the API worktree root. Use the SQLite-backed integration test patterns already in `PrintLogApi.IntegrationTests/Sync/`.
- **Commits:** never add a `Claude-Session:` trailer or a session link. "Co-Authored-By" is fine.

## Review Focus

1. **A user's account has a printer with no name, no make and no connector.** The device is "Printer {id}", the connection sensor is unknown (not off), and setup doesn't crash. *Test owned by Tasks 6 and 11.*
2. **An API key is revoked while HA is running.** The next poll raises `ConfigEntryAuthFailed` and reauth starts, and entities don't flap between unavailable and available. *Task 10.*
3. **A print is deleted, or an older print is added by hand.** No `finished` or `failed` event fires. *Task 8.*
4. **Untracked spools (`remainingGrams: null`).** The remaining sensor is unknown, not 0; filament-short is unknown; `adjust_remaining` raises a validation error locally without calling the API. *Tasks 11 and 13.*
5. **HA restarts mid-print.** No duplicate `started` event; a finish that happened while HA was down fires once if it was within 60 minutes, and never otherwise. *Tasks 8 and 12.*

---

## Track A: API (3d-print-log-api)

Setup, done once by the Task 1 implementer:

```bash
cd /d/Development/github/3d-print-log-api && git fetch origin && git worktree add -b feat/236-ha-sync-fields ../3d-print-log-api-worktrees/236-ha-sync origin/main
```

Read `AGENTS.md` first, in particular "Integration polling" and the caching rules. The new fields must go into the cached core that `coreHash` covers, so the ETag follows them.

### Task 1: Printer totals and maintenance in the snapshot

**Files:**
- Modify: `PrintLogApi/Models/DTOs/Sync/SyncSnapshot.cs`
- Modify: `PrintLogApi/Services/Sync/SyncSnapshotService.cs`
- Modify: the JSON source-generation context, if the snapshot types are registered there (see `JsonSourceGenerationTests`)
- Test: `PrintLogApi.IntegrationTests/Sync/SyncSnapshotTests.cs` (add tests) and `SyncSnapshotCachingTests.cs`

**Interfaces:**
- Produces:
  - `SyncPrinter.PrintTimeSeconds` (`long`)
  - `SyncPrinter.LastMaintenance` (`SyncMaintenance? (Guid Id, DateTimeOffset Date, string? Category, string? Description)`)
  - `SyncPrinter.OpenMaintenance` (`SyncOpenMaintenance (int Count, DateTimeOffset? NextDate)`)
  - New positional parameters go **last** in `SyncPrinter`.

- [ ] **Step 1: Write failing tests**
  - `Snapshot_PrintTimeSeconds_MatchesAnalyticsAllTime`: seed a printer with prints of 3600 s (Success) and 1800 s (Failed), then assert the figure equals what `GET /api/analytics/printers` returns for that printer with no dates. Read `AnalyticsController`/service to see which statuses count, and assert equality with it, not a hardcoded rule.
  - `Snapshot_LastMaintenance_IsNewestDone`: two done items (2026-01-01, 2026-02-01) and one not done (2026-03-01) → the 2026-02-01 item.
  - `Snapshot_OpenMaintenance_CountsNotDone`: two not-done items (2026-05-01, 2026-04-01) → `{count: 2, nextDate: 2026-04-01}`. With none → `{count: 0, nextDate: null}`.
  - `Snapshot_ETag_ChangesWhenMaintenanceAdded`: GET the snapshot, POST `/api/PrinterMaintenance`, GET with `If-None-Match` → 200 with a new ETag.
- [ ] **Step 2:** Run `dotnet test --verbosity quiet --filter "FullyQualifiedName~Sync"`. Expected: the new tests fail.
- [ ] **Step 3: Implement** in the printer query of `SyncSnapshotService`. Use grouped subqueries per printer, not N+1. `SyncSnapshotQueryShapeTests` pins the query count, so update it deliberately if the shape changes, and say why in the commit. Document each field with `<param>` XML docs in the same style.
- [ ] **Step 4:** Run the whole suite with `dotnet test --verbosity quiet`. Expected: all pass.
- [ ] **Step 5:** Commit: `feat(sync): print time and maintenance per printer in the snapshot (#236)`.

### Task 2: Print source, url, per-slot estimates and month outcomes

**Files:** same DTO and service files as Task 1, plus their tests.

**Interfaces:**
- Produces:
  - `SyncCurrentPrint` gains `Source` (string), `Url` (string?) and `Filament` (`IReadOnlyList<SyncPrintFilament(int? Slot, Guid? SpoolId, double? EstimatedGrams)>`).
  - `SyncFinishedPrint` gains `Source` and `Url`.
  - `SyncAccountTotals` gains `SuccessCount` and `FailedCount` (int).
  - All of them are appended last.

- [ ] **Step 1: Write failing tests**
  - `Snapshot_Source_IsSnakeCase`:
    - `PrintSource.SlicerPlugin` → `"slicer_plugin"`
    - `ApiKey` → `"api_key"`
    - `OctoPrint` → `"octoprint"`
    - `Unknown` → `"unknown"`
  - `Snapshot_Url_UsesWebBaseUrl`: with `Email:WebBaseUrl = https://www.example.test/`, `url` is `https://www.example.test/prints/{id}`. With it unset, `url` is null.
  - `Snapshot_CurrentPrintFilament_SlotOrderGrams`: rows (slot 2, 12_345 mg), (slot null, 500 mg) and (slot 1, null) give `[{1, …, null}, {2, …, 12.3}, {null, …, 0.5}]`.
  - `Snapshot_Account_SuccessAndFailedCounts`: Success ×2, Failed ×1, Cancelled ×1 and PartialSuccess ×1 this month, plus one Success last month, give `successCount 2, failedCount 1, printCount 5`.
- [ ] **Step 2:** Run the Sync tests. Expected: the new ones fail.
- [ ] **Step 3: Implement.**
  - The web base comes from the same options the webhook uses (`emailOptions.Value.WebBaseUrl?.TrimEnd('/')`, `WebhookDeliveryProcessor.cs:285`).
  - Put the snake_case mapping in one static helper in the Sync folder.
  - The account counts join the month-totals cache entry.
- [ ] **Step 4:** Run the full suite. Expected: all pass.
- [ ] **Step 5:** Commit: `feat(sync): print source, url, slot estimates and month outcomes (#236)`.

### Task 3: Filament adjustment and active endpoints

**Files:**
- Modify: `PrintLogApi/Controllers/FilamentsController.cs`
- Create: `PrintLogApi/Models/DTOs/Filament/AdjustFilamentRemainingDto.cs` and `SetFilamentActiveDto.cs` (match the folder's naming)
- Test: the existing filament controller integration tests file (find it with `grep -rl "api/Filaments" PrintLogApi.IntegrationTests`)

**Interfaces:**
- Produces:
  - `POST /api/Filaments/{id:guid}/adjustments`: body `{source: "weight"|"length"|"volume", delta: number, notes?: string}`, returns 200 `{beforeGrams, afterGrams}`.
  - `PUT /api/Filaments/{id:guid}/active`: body `{isActive: bool}`, returns 204.
  - Both are plain `[Authorize]`.

- [ ] **Step 1: Write failing tests**
  - `Adjust_Weight_ReturnsBeforeAfter`: a 1000 g spool with 400 g used, delta −50 → `{beforeGrams: 600, afterGrams: 550}`. Then the snapshot's `remainingGrams` is 550 and its ETag changed.
  - `Adjust_Untracked_Returns400`
  - `Adjust_BelowZero_Returns400`
  - `Adjust_ZeroDelta_Returns400`
  - `Adjust_UnknownSource_Returns400`
  - `Adjust_ForeignSpool_Returns404`
  - `Adjust_WithApiKey_Succeeds`: authenticates with `X-Api-Key`. Find the API-key test helper in the integration tests.
  - `SetActive_False_Returns204_AndSnapshotShowsInactive`
  - `SetActive_ForeignSpool_Returns404`
- [ ] **Step 2:** Run them. Expected: they fail (404 route).
- [ ] **Step 3: Implement.**
  - Wrap `FilamentService.AdjustMaterialRemainingForMcp` and `SetMaterialActiveForMcp`.
  - Map the MCP source enum from the string (case-insensitive).
  - Map the service's not-found and invalid-argument outcomes to 404/400 problem details, following how that controller already maps errors.
  - Validate `notes` at most 1000 characters, and a delta that is finite and not 0.
- [ ] **Step 4:** Run the full suite. Expected: all pass.
- [ ] **Step 5:** Commit: `feat(filaments): adjustment and active endpoints for integrations (#236)`.

### Task 4: Docs and PR

- [ ] **Step 1:** Update `AGENTS.md` "Integration polling" (new fields, and the cache bump on the new endpoints), plus the OpenAPI annotations if the controller uses them.
- [ ] **Step 2:** Run `dotnet build` and `dotnet test --verbosity quiet`. Expected: both green.
- [ ] **Step 3:** Push, then `gh pr create`. The PR is titled "feat(sync): Home Assistant integration fields and filament endpoints (#236)" and its body links #236 and the integration PR.

---

## Track B: integration (3d-print-log-homeassistant, worktree `v0.1`)

### Task 5: Scaffold, test runner, CI, brand images

**Files:**
- Create: `custom_components/printlog/{__init__.py, manifest.json, const.py}`
- Create: `hacs.json`, `pyproject.toml`, `requirements_test.txt`, `Dockerfile.test`, `scripts/test.sh`
- Create: `.github/workflows/{validate.yml, tests.yml, release.yml}`
- Create: `tests/__init__.py`, `tests/conftest.py`, `tests/test_init.py`
- Create: `custom_components/printlog/brand/{icon.png, icon@2x.png, logo.png, logo@2x.png}`
- Create: `README.md` (stub), `CHANGELOG.md`, `.gitignore`

**Interfaces:**
- Produces:
  - `const.py`: `DOMAIN = "printlog"`, `DEFAULT_BASE_URL`, `CONF_BASE_URL = "base_url"`, `CONF_AUTH_TYPE = "auth_type"`, `AUTH_TYPE_API_KEY = "api_key"`, `CONF_SCAN_INTERVAL = "scan_interval"`, `DEFAULT_SCAN_INTERVAL = 30`, `MIN_SCAN_INTERVAL = 15`, `MAX_SCAN_INTERVAL = 300`, `FORCED_SNAPSHOT_SECONDS = 300`, `SLOW_INTERVAL_SECONDS = 300`, `REQUEST_TIMEOUT = 15`, `EVENT_WINDOW_MINUTES = 60`, `SEEN_LIMIT = 50`, `PROD_API_HOST = "api.3dprintlog.com"`, `PROD_WEB_BASE = "https://www.3dprintlog.com"`.
  - `scripts/test.sh [pytest args]` runs the suite in Docker.

- [ ] **Step 1: Write the manifest and HACS files.**
  - `manifest.json`: `domain`, `name` "3D Print Log", `codeowners` ["@ChristopherHoffman"], `config_flow` true, `documentation` (the repo URL), `issue_tracker` (repo issues), `iot_class` "cloud_polling", `integration_type` "hub", `requirements` [], `version` "0.1.0".
  - `hacs.json`: `{"name": "3D Print Log", "homeassistant": "2026.10.0", "render_readme": true}`.
- [ ] **Step 2: Set up the test runner.**
  - `requirements_test.txt` pins `pytest-homeassistant-custom-component==0.13.373` and `ruff`.
  - `Dockerfile.test` is FROM `python:3.14-slim` and installs the requirements, so the image is built once and tagged `printlog-ha-test`.
  - `scripts/test.sh` builds the image if it's missing (or when `--rebuild` is passed), then runs `docker run --rm -v "<repo>:/repo" -w /repo printlog-ha-test python -m pytest "$@"`. Use `MSYS_NO_PATHCONV=1` and `pwd -W` so Git Bash passes a Windows path.
  - `pyproject.toml` configures `[tool.pytest.ini_options]` (`asyncio_mode = "auto"`, `testpaths = ["tests"]`), `--cov=custom_components.printlog --cov-fail-under=95` (applied in CI via `tests.yml` args), and ruff (target py314).
- [ ] **Step 3: Write the failing test.** In `tests/test_init.py`, `test_manifest_matches_const`: load `manifest.json` and assert its `domain == DOMAIN` and that `version` parses with `awesomeversion`. `conftest.py` has the autouse `enable_custom_integrations` fixture.
- [ ] **Step 4:** Run `scripts/test.sh`. Expected: the test fails until `const.py` exists, then passes.
- [ ] **Step 5: Add the workflows.**
  - `validate.yml`: `hacs/action@main` (category integration) and `home-assistant/actions/hassfest@master`; on push, pull_request, a nightly cron and workflow_dispatch.
  - `tests.yml`: setup-python 3.14, pip install `requirements_test.txt`, `ruff check .`, `pytest --cov … --cov-fail-under=95`.
  - `release.yml`: on `release: published`, fail unless `manifest.json` `version` equals `${GITHUB_REF_NAME#v}`.
- [ ] **Step 6: Make the brand images.** Render `D:\Development\github\3d-print-log-ui\src\assets\3d_brand_logo_b833b3f20dd.svg`, the square brand mark. If it isn't square, use the one that is, and say which.
  - Sizes: icon 256×256 and 512×512 (`@2x`); logo with the same height and its natural width (and `@2x`).
  - Render in a throwaway container: `python:3.14-slim` + `pip install cairosvg pillow`, writing to the worktree. Trim and keep the transparency.
  - Look at each PNG with the Read tool before committing.
- [ ] **Step 7:** Commit: `chore: scaffold the printlog integration, test runner and CI`.

### Task 6: Snapshot models (`models.py`)

**Files:** Create `custom_components/printlog/models.py`; test `tests/test_models.py`, with fixtures `tests/fixtures/snapshot_full.json` (every §3 field) and `tests/fixtures/snapshot_v1_17.json` (today's shape, without the §3 fields).

**Interfaces:**
- Produces: frozen dataclasses with `slots=True`. Snake-case attribute names map from the camelCase JSON. Timestamps are `datetime | None` (aware).
  - `Snapshot(schema_version: int, version: str, printers: tuple[Printer, ...], spools: tuple[Spool, ...], account: Account)`, with `Snapshot.from_json(data: dict) -> Snapshot` and `spool_by_id(guid) -> Spool | None` / `spool_by_number(n) -> Spool | None`.
  - `Printer(id: int, name: str | None, make, model, active: bool, slot_count: int, connection: Connection | None, current_print: CurrentPrint | None, last_finished_print: FinishedPrint | None, loaded: tuple[LoadedSpool, ...], print_time_seconds: int | None, last_maintenance: Maintenance | None, open_maintenance: OpenMaintenance | None, has_extended_fields: bool)`.
    - `has_extended_fields` is True when the JSON has the `printTimeSeconds` key.
    - Also `display_name` ("Printer {id}" when there is no name) and `loaded_in_slot(n) -> LoadedSpool | None`, which applies spec §5.1's unslotted rule, plus `unslotted() -> tuple[LoadedSpool, ...]` for the rest.
  - `Connection(kind, status)`.
  - `CurrentPrint(id, title, started_at, estimated_seconds, estimated_end_at, source: str | None, url: str | None, filament: tuple[PrintFilament, ...])`.
  - `FinishedPrint(id, title, status, started_at, finished_at, source, url)`.
  - `PrintFilament(slot: int | None, spool_id: str | None, estimated_grams: float | None)`.
  - `LoadedSpool(slot, slot_label, spool_id: str, spool_number, name, material, color_hex, colors: tuple[str, ...], remaining_grams: float | None)`.
  - `Spool(id: str, spool_number, name, brand, material, category, color_name, color_hex, colors, remaining_grams, used_grams: float, initial_grams, storage_location, active: bool)`.
  - `Maintenance(id, date, category, description)`.
  - `OpenMaintenance(count: int, next_date)`.
  - `Account(month, time_zone, print_count, grams, success_count: int | None, failed_count: int | None)`.

- [ ] **Step 1: Write failing tests**
  - `test_parse_full`: spot-check every field.
  - `test_parse_v1_17_missing_fields_are_none`: covers `has_extended_fields is False`, `source is None` and `filament == ()`.
  - `test_unknown_fields_ignored`
  - `test_display_name_fallback`
  - `test_loaded_in_slot_unslotted_single_slot`: slotCount 1 and a null slot → slot 1.
  - `test_loaded_in_slot_unslotted_multi_slot`: slotCount 4 and a null slot → slot 1 is None, and `unslotted()` holds it.
  - `test_equality`: two parses of the same JSON compare equal.
- [ ] **Step 2:** Run `scripts/test.sh tests/test_models.py`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** with `datetime.fromisoformat`. No HA imports.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: snapshot models`.

### Task 7: API client (`api.py`)

**Files:** Create `custom_components/printlog/api.py`; test `tests/test_api.py`.

**Interfaces:**
- Produces:
  - **Sentinel:** `NOT_MODIFIED`.
  - **Exceptions:** `PrintLogError` and its subclasses `PrintLogAuthError`, `PrintLogRateLimitError(retry_after: float | None)`, `PrintLogNotFoundError`, `PrintLogValidationError(message: str)`, `PrintLogConnectionError` and `PrintLogUnsupportedError`.
  - **Client:** `class PrintLogClient(session: aiohttp.ClientSession, base_url: str, api_key: str)`, with these methods:
    - `async get_me() -> dict`
    - `async get_version(etag: str | None) -> tuple[str, str | None] | NOT_MODIFIED`, returning `(version, etag)`
    - `async get_snapshot(time_zone: str, etag: str | None) -> tuple[dict, str | None] | NOT_MODIFIED`
    - `async get_unread_count() -> dict`
    - `async adjust_remaining(spool_id: str, source: str, delta: float, notes: str | None) -> dict`
    - `async set_spool_active(spool_id: str, is_active: bool) -> None`
    - `async create_maintenance(printer_id: int, date: datetime, category: str | None, description: str, notes: str | None) -> dict`
  - **Helper:** `normalized_host(base_url) -> str`.

- [ ] **Step 1: Write failing tests** with `aioclient_mock`.
  - `test_sends_api_key_header_not_query`: assert the header, and that the URL has no `api_key`.
  - `test_version_304_returns_not_modified`: also check the `If-None-Match` header is sent.
  - `test_snapshot_passes_timezone`
  - `test_status_mapping`, parametrized:
    - 401 and 403 → auth error;
    - 429 with `Retry-After: 30` → rate limit, `retry_after == 30`;
    - 400 with problem details `{detail: "x"}` → validation error with "x";
    - 404 → not found;
    - 500 and a timeout → connection error.
  - `test_new_endpoint_404_is_unsupported`: adjust and active map a 404 with no problem body to `PrintLogUnsupportedError`. A 404 problem body counts as not found.
  - `test_create_maintenance_body`: `done: true`, ISO date.
  - `test_normalized_host`:
    - `https://API.3dprintlog.com/` → `api.3dprintlog.com`
    - `http://localhost:5101` → `localhost:5101`
    - `https://x:443` → `x`
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Use `aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)`.
  - Never log headers.
  - Telling the two 404s apart: the API returns problem JSON (`application/problem+json`) for not-owned. ASP.NET's unmatched route returns an empty 404. Treat an empty 404 on the two new endpoints as unsupported.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: API client`.

### Task 8: Event diff (`events.py`)

**Files:** Create `custom_components/printlog/events.py`; test `tests/test_events.py`.

**Interfaces:**
- Consumes: the `models` from Task 6.
- Produces:
  - `PrinterEventState(seen: tuple[int, ...], current: int | None)`, with `to_dict()` and `from_dict()`.
  - `PrintEvent(printer_id: int, printer_name: str, status: str, print_id: int, title: str | None, started_at: datetime | None, finished_at: datetime | None, source: str | None, url: str | None)`.
  - `diff(prev: dict[int, PrinterEventState], snapshot: Snapshot, now: datetime) -> tuple[list[PrintEvent], dict[int, PrinterEventState]]`. It implements spec §6.2 exactly.
  - Constants `BUS_EVENT = {"started": "printlog_print_started", "success": "printlog_print_finished", "failed": "printlog_print_failed", "canceled": "printlog_print_failed", "partial_success": "printlog_print_failed"}`.

- [ ] **Step 1: Write failing tests**, one per rule, with fixed `now`:
  - `test_first_sight_seeds_no_events`
  - `test_started_fires_once`: a second diff with the same snapshot fires nothing.
  - `test_finish_of_previous_current_fires_status`: success, failed, canceled and partial_success, parametrized.
  - `test_restart_window`: an unknown finished id with `finished_at` 30 min ago fires; at 90 min ago it doesn't, but is added to seen.
  - `test_finished_without_finished_at_uses_started_at`
  - `test_deleted_newest_resurfaces_older_no_event`: seen holds 10, last finished becomes 9 (already seen) → nothing. And when 9 isn't seen and is older than 60 min → nothing.
  - `test_backdated_manual_print_no_event`: a new id with `started_at` 3 days ago → nothing.
  - `test_current_deleted_no_event`
  - `test_seen_bounded_to_50`: keeps the 50 most recently added.
  - `test_printer_removed_drops_state`
  - `test_state_roundtrip`
  - `test_short_print_only_finish`: no prev current, a finish 1 min ago → fires the finish only.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement.** A pure function, no HA imports.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: derive print events from snapshot changes`.

### Task 9: Config flow and entry setup skeleton

**Files:**
- Create: `custom_components/printlog/config_flow.py`, `translations/en.json` (the config and options sections)
- Modify: `__init__.py` (add `async_setup_entry`, `async_unload_entry`, `async_migrate_entry`; for this task, setup only creates the client and stores it)
- Test: `tests/test_config_flow.py`, `tests/test_init.py`

**Interfaces:**
- Consumes: `PrintLogClient` and `normalized_host` from Task 7.
- Produces:
  - `PrintLogConfigFlow` (VERSION 1, MINOR_VERSION 1) and `PrintLogOptionsFlow`.
  - `type PrintLogConfigEntry = ConfigEntry[PrintLogData]`, declared in `__init__.py`. `PrintLogData` is defined in Task 10; until then, a placeholder dataclass with `client` lives in `coordinator.py`.

- [ ] **Step 1: Write failing tests.**
  - The user step:
    - Success creates an entry with `unique_id "api.3dprintlog.com:42"`, title equal to the DisplayName (or "3D Print Log" when it's empty), and data `{base_url, auth_type: "api_key", api_key}`.
    - `invalid_auth`, `cannot_connect`, `unknown`.
    - `already_configured`.
  - Reauth: success updates the key and reloads; `wrong_account` when the id differs.
  - Reconfigure: success; `wrong_account`.
  - Options: `scan_interval` 15–300, default 30; out of range is rejected by the selector or schema.
  - `test_migrate_future_version_fails`: an entry with VERSION 2 → migration returns False.
  - Assert that the API key doesn't appear in the entry title or unique id.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement.** Use `async_get_clientsession(hass)`, `_get_reauth_entry()`, `_get_reconfigure_entry()` and `async_update_reload_and_abort`. Use selectors for the inputs: password type for the key, URL type for the base URL.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: config flow with reauth, reconfigure and options`.

### Task 10: Coordinators, runtime data, repair, stale devices

**Files:**
- Create: `custom_components/printlog/coordinator.py`, `repairs.py`
- Modify: `__init__.py` (create both coordinators, load the store, first refresh, forward platforms `[SENSOR, BINARY_SENSOR, EVENT]`, and remove the store in `async_remove_entry`)
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: Task 6 models, Task 7 client, Task 8 `diff`.
- Produces:
  - `@dataclass PrintLogData(client: PrintLogClient, snapshot: SnapshotCoordinator, slow: SlowCoordinator, store: EventStore)`.
  - `SnapshotCoordinator(DataUpdateCoordinator[Snapshot])`, with `always_update=False` and `entry_uid: str`. It also provides:
    - `async_add_event_listener(cb: Callable[[list[PrintEvent]], None]) -> Callable[[], None]`, which Task 12 consumes;
    - `diagnostics() -> dict`.
  - `SlowCoordinator(DataUpdateCoordinator[dict])`.
  - `EventStore`, which wraps `Store` and provides `async_load() -> dict[int, PrinterEventState]`, `async_save_delayed(state)` and `async_remove()`.
  - `repairs.async_raise_unsupported_schema(hass, entry)` and `async_clear_unsupported_schema(hass, entry)`.

- [ ] **Step 1: Write failing tests.** Use `freezer` and `aioclient_mock`; the fixtures come from Task 6.
  - `test_first_refresh_fetches_snapshot`
  - `test_version_304_skips_snapshot`
  - `test_version_change_fetches_snapshot`
  - `test_forced_snapshot_after_300s_even_if_version_304`
  - `test_snapshot_304_keeps_data_no_listener_update`
  - `test_schema_version_2_raises_repair_and_update_failed`: a later v1 clears the repair.
  - `test_401_starts_reauth`: the entry gets the `reauth` flow in progress.
  - `test_429_defers_next_refresh`: the next refresh comes after at least `retry_after`.
  - `test_connection_error_unavailable_logs_once`: caplog shows exactly one warning across two failures, and one info on recovery.
  - `test_time_zone_from_hass_config`
  - `test_stale_printer_device_removed`: the second snapshot has no printer 2, so its device is gone and the account device remains.
  - `test_slow_failure_isolated`: unread-count fails, but snapshot entities stay available.
  - `test_events_dispatched_and_store_saved`
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement** per spec §4.5.
  - The forced fetch uses a monotonic timestamp of the last snapshot fetch.
  - For 429, raise `UpdateFailed` and set `self.update_interval` to the max of the interval and `retry_after` for one cycle, then restore it.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: snapshot and slow coordinators`.

### Task 11: Entities: sensors and binary sensors

**Files:**
- Create: `custom_components/printlog/entity.py`, `sensor.py`, `binary_sensor.py`, `icons.json`
- Modify: `translations/en.json` (the `entity` section)
- Test: `tests/test_sensor.py`, `tests/test_binary_sensor.py`, plus syrupy snapshots in `tests/snapshots/`

**Interfaces:**
- Consumes: `PrintLogData`, `SnapshotCoordinator` and `SlowCoordinator` from Task 10, the models from Task 6.
- Produces:
  - `PrintLogPrinterEntity(CoordinatorEntity[SnapshotCoordinator])`: takes `printer_id` and `key`, gets its unique id from spec §4.6, and has a `printer` property that returns `Printer | None`. It is available only when the coordinator succeeded **and** the printer is present.
  - `PrintLogSlotEntity(PrintLogPrinterEntity)`: takes `slot`.
  - `PrintLogAccountEntity`.
  - `printer_device_info(entry, printer) -> DeviceInfo`.
  - **Dynamic entity adder:** each platform keeps a set of the unique ids it has created and adds new ones on coordinator update (new printer, slot count growth, extended fields appearing).

- [ ] **Step 1: Write failing tests.**
  - `test_entities_full_snapshot`: uses `snapshot_platform`, covering every key in spec §5.1 and §5.3 with its exact state and attributes.
  - `test_entities_v1_17_snapshot_omits_extended`: there is no `print_time`, `last_maintenance`, `maintenance_due` or `filament_short`.
  - `test_extended_entities_added_when_fields_appear`
  - `test_slot_entities_added_when_slot_count_grows`
  - `test_connection_unknown_without_connector`
  - `test_untracked_remaining_unknown`
  - `test_filament_short_states`, parametrized: remaining 10 and estimate 20 → on; 30 and 20 → off; no print, no estimate or untracked → unknown.
  - `test_maintenance_due`: `next_date` in the past → on; in the future → off; no open items → off.
  - `test_month_totals_last_reset`: `last_reset` is the start of the month in the account's zone.
  - `test_unread_notifications_from_slow`
  - `test_device_info`: name fallback; `configuration_url` set only for the prod `base_url`; identifiers match spec §4.6.
  - `test_current_print_connection_stale_attribute`
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement** with `SensorEntityDescription` and `BinarySensorEntityDescription` subclasses that carry `value_fn`, `attrs_fn` and `exists_fn` lambdas. Every key appears in `translations/en.json` and `icons.json`.
- [ ] **Step 4:** Run it. Expected: PASS. Update the syrupy snapshots with `--snapshot-update` once, then review the snapshot file by hand.
- [ ] **Step 5:** Commit: `feat: printer, slot and account sensors`.

### Task 12: Event entity and bus events

**Files:**
- Create: `custom_components/printlog/event.py`
- Modify: `__init__.py` (subscribe a bus-firing listener via `async_add_event_listener`), `translations/en.json`
- Test: `tests/test_events_ha.py`

**Interfaces:**
- Consumes: `SnapshotCoordinator.async_add_event_listener` (Task 10), `PrintEvent` and `BUS_EVENT` (Task 8), the device registry.
- Produces: the event entity key `print`, and bus payloads per spec §6.3.

- [ ] **Step 1: Write failing tests.**
  - `test_bus_event_payload`: started, then success. Payloads match spec §6.3 exactly, including `version: 1`, `device_id` (from the registry) and `entry_id`, with timestamps as ISO strings.
  - `test_failed_statuses_map_to_failed_event`: canceled carries `status: "canceled"`.
  - `test_event_entity_types_and_data`: `event_types` has five entries, the state changes, and the attributes hold the payload without `device_id`.
  - `test_restart_no_duplicate_started`: set up, start a print, unload, set up again from the persisted store → no second started.
  - `test_finish_while_down_within_window_fires_once`
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: print event entity and bus events`.

### Task 13: Actions (services)

**Files:**
- Create: `custom_components/printlog/services.py`, `services.yaml`
- Modify: `__init__.py` (an `async_setup` that registers the services), `translations/en.json` (`services` and `exceptions`)
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `PrintLogData` (Task 10), the client (Task 7), the models (Task 6), and the entity registry, which maps a slot entity unique id to its printer id and slot (parse the §4.6 format).
- Produces: the services `printlog.adjust_remaining`, `printlog.set_spool_active` and `printlog.log_maintenance`, as in spec §7.

- [ ] **Step 1: Write failing tests.**
  - `test_adjust_by_slot_entity`: resolves to the loaded spool id, POSTs `source: "weight"` with `delta -12.5`, returns `{before_grams, after_grams}` and triggers a refresh.
  - `test_adjust_by_spool_number`, `test_adjust_by_spool_id`
  - `test_adjust_requires_exactly_one_target`
  - `test_adjust_zero_amount_rejected`
  - `test_adjust_unit_mapping`: mm → length, ml → volume.
  - `test_adjust_empty_slot_error`
  - `test_adjust_untracked_rejected_locally`: no HTTP call is made.
  - `test_adjust_api_validation_message_surfaces`
  - `test_adjust_unsupported_api_error`
  - `test_set_spool_active`
  - `test_log_maintenance_defaults_now_and_done_true`: freeze the time and check the body.
  - `test_log_maintenance_rejects_maintenance_id`
  - `test_log_maintenance_requires_printlog_device`
  - `test_multiple_entries_require_config_entry_id`, plus the case where one entry needs no id.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement.** Register with `supports_response=SupportsResponse.OPTIONAL`. Errors are `ServiceValidationError` or `HomeAssistantError`, each with a `translation_domain` and `translation_key`.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: adjust remaining, set spool active and log maintenance actions`.

### Task 14: Diagnostics, README, coverage gate

**Files:**
- Create: `custom_components/printlog/diagnostics.py`
- Modify: `README.md`, `CHANGELOG.md`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write a failing test.** `test_diagnostics_redacts`: a syrupy snapshot with `api_key`, titles, names, descriptions, notes, `storage_location` and `url` redacted. Also assert that the raw key string appears nowhere in `json.dumps(result)`.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3: Implement** with `async_redact_data`.
- [ ] **Step 4: Write the README.** It covers:
  - What the integration is, and when to use it versus Spoolman (spec D4).
  - Installation via a HACS custom repository, then creating an API key.
  - Entities, with a table.
  - Events, with a payload example and an automation example.
  - Actions.
  - Limits (spec §6.2 accepted limits; webhooks come later).
  - Privacy (what gets redacted).

  `CHANGELOG.md` gets a 0.1.0 entry.
- [ ] **Step 5:** Run `scripts/test.sh --cov=custom_components.printlog --cov-report=term-missing --cov-fail-under=95` and `ruff check .`. Expected: PASS, with coverage at least 95%.
- [ ] **Step 6:** Commit: `feat: diagnostics; docs: README and changelog`.

### Task 15: Manual check in the dev Home Assistant

- [ ] **Step 1:** Run the API worktree from Track A on port 5101 (`ASPNETCORE_URLS=http://0.0.0.0:5101`, in the background), against the Docker SQL Server on 1434. Create an API key for a test user.
- [ ] **Step 2:** Copy `custom_components/printlog` to `D:\Development\homeassistant-dev\config\custom_components\printlog` and restart the `homeassistant-dev` container.
- [ ] **Step 3:** Add the integration with base URL `http://host.docker.internal:5101`. Then:
  - Screenshot the printer device page and the account device.
  - Start and finish a print through the API, and confirm in Developer Tools → Events that `printlog_print_started` and `printlog_print_finished` fired with the spec payload.
  - Call `printlog.adjust_remaining` on a slot entity.
- [ ] **Step 4:** Look at the screenshots. Check that icons match their meaning and that states read correctly. Stop the API by port when done.
- [ ] **Step 5:** Fix whatever this turns up through the owning task's test-first loop. Record the results in the PR body.

### Task 16: PRs

- [ ] **Step 1:** Push `feat/v0.1.0` and open a PR to `main` titled "feat: printlog Home Assistant integration v0.1.0 (api#236)". The body links the spec, the plan, the API PR and the screenshots.
- [ ] **Step 2:** Watch CI with `gh pr checks <n> --watch` in the background, and fix any failures. hassfest and HACS validation must pass.
- [ ] **Step 3:** Open the UI docs draft PR (spec §11.3) in a `3d-print-log-ui-worktrees/ha-integration-docs` worktree: `src/content/docs/home-assistant.md`, cross-linked from the Spoolman guide, validated with `node scripts/validate-docs.mjs`.
- [ ] **Step 4:** Comment on #236 with the PR links and the manual steps: merge the API PR, deploy, then publish the `v0.1.0` release.
