# 3D Print Log for Home Assistant

A Home Assistant custom integration for [3D Print Log](https://www.3dprintlog.com). It brings your printers, prints, loaded filament, maintenance and account totals into Home Assistant, so you can build automations on them. Install it through HACS.

It reads your 3D Print Log account over its API. It works on a LAN-only Home Assistant with nothing exposed to the internet, because Home Assistant only makes outgoing requests.

Live printer state (temperatures, progress, camera) stays with your printer's own integration (Moonraker, OctoPrint, Bambu Lab, PrusaLink and so on). This integration adds what those don't have: the records in 3D Print Log.

## Use this or the Spoolman integration?

| You want | Use |
|---|---|
| One sensor per spool (weight, length, location, and so on) | The community [Spoolman integration](https://github.com/Disane87/spoolman-homeassistant), pointed at 3D Print Log's `/spoolman` API |
| Printers, prints, what is loaded in each slot, maintenance, account totals, print events and actions | This integration |

This integration deliberately creates no per-spool entities, so the two can run side by side without duplicates.

## Requirements

- Home Assistant 2026.10 or newer.
- A 3D Print Log account.
- The extended entities (print time, last maintenance, maintenance due, "not enough filament", and the source and URL attributes) need the next 3D Print Log API release. On an older API the integration still works and creates fewer entities. The missing ones appear on their own once the API is updated.

## Installation

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/HoffmanEngineering/3d-print-log-homeassistant` with the category **Integration**.
3. Download **3D Print Log** and restart Home Assistant.
4. In 3D Print Log, open **Settings** and create an API key named **Home Assistant**. Copy it.
5. In Home Assistant, go to **Settings > Devices & services > Add integration**, choose **3D Print Log** and paste the key. Leave the API URL at its default unless you run your own API.

Use a dedicated key. You can revoke it in 3D Print Log at any time without affecting anything else. If the key is rejected later, Home Assistant starts a reauthentication flow.

### Options

**Settings > Devices & services > 3D Print Log > Configure** sets the update interval: 15 to 300 seconds, default 30. Each check is a small request that returns nothing new when your data has not changed.

## Entities

Each printer is a device. The account is a separate service device. Entity ids follow the device name, for example `sensor.snapmaker_u1_current_print`.

### Printer

| Entity | Type | State | Attributes |
|---|---|---|---|
| Connection | binary sensor (connectivity) | On when the printer's connector reports online. Off when stale. Unknown when the printer has no connector. | `kind` |
| Printing | binary sensor (running) | On when a print is in progress | none |
| Maintenance due | binary sensor (problem) | On when an open maintenance item is due | `open_count`, `next_date` |
| Slot N not enough filament | binary sensor (problem) | On when the running print's estimate for slot N is more than the loaded spool has left. Unknown without a print, an estimate or a tracked spool. | `estimated_grams`, `remaining_grams` |
| Current print | sensor | The print's title ("Untitled" if it has none), or nothing | `print_id`, `source`, `url`, `connection_stale` |
| Print started | sensor (timestamp) | Start time of the current print | none |
| Print estimated end | sensor (timestamp) | Estimated end of the current print | none |
| Last print result | sensor (enum) | `success`, `failed`, `canceled` or `partial_success` | `print_id`, `title`, `finished_at`, `source`, `url` |
| Slot N filament | sensor | Name, material or "Spool #number" of the spool loaded in slot N | `material`, `color_hex`, `colors`, `spool_id`, `spool_number`, `slot_label` |
| Slot N remaining | sensor (weight, g) | Grams left on the spool in slot N. Unknown for an untracked spool or an empty slot. | `spool_id` |
| Print time | sensor (duration, h) | All-time print time on this printer | none |
| Last maintenance | sensor (timestamp) | Date of the newest completed maintenance | `category`, `description` |
| Print | event | See [Events](#events) | the event payload |

A printer has one set of slot entities per slot (a single-extruder printer has one). A spool loaded without a slot counts as slot 1 on a single-slot printer. Elsewhere it is listed in the `unslotted` attribute of Slot 1 filament.

### Account

| Entity | Type | State | Attributes |
|---|---|---|---|
| Prints this month | sensor (total) | Prints this calendar month, in your account's time zone | `month`, `time_zone`, `success_count`, `failed_count` |
| Grams this month | sensor (weight, g) | Filament used this month | `month` |
| Unread notifications | sensor | Unread notification count | `unread_achievements` |

## Events

The integration detects print events by comparing each update with the one before it. There are two ways to use them: the bus events and each printer's **Print** event entity. Both fire in the same pass.

| Status | Bus event | Event entity `event_type` |
|---|---|---|
| Started | `printlog_print_started` | `started` |
| Succeeded | `printlog_print_finished` | `success` |
| Failed, canceled or partly succeeded | `printlog_print_failed` | `failed`, `canceled`, `partial_success` |

The bus event payload (version 1; new keys may be added later, existing ones will not change):

```json
{
  "version": 1,
  "entry_id": "01J9ZXAMPLEENTRYID",
  "device_id": "3f6c1d0a5b7e4c0f9a1d2e3f4a5b6c7d",
  "printer_id": 101,
  "printer_name": "Snapmaker U1",
  "print_id": 9001,
  "status": "success",
  "title": "Benchy",
  "started_at": "2026-10-17T14:00:00+00:00",
  "finished_at": "2026-10-17T16:05:00+00:00",
  "source": "moonraker",
  "url": "https://www.3dprintlog.com/prints/9001"
}
```

`finished_at` is null for `started`. `source` and `url` are null on an older API. The event entity carries the same payload without `device_id`.

Automation on the event entity:

```yaml
triggers:
  - trigger: state
    entity_id: event.snapmaker_u1_print
    attribute: event_type
    to: failed
actions:
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.to_state.attributes.title }} failed on {{ trigger.to_state.attributes.printer_name }}"
```

Automation on the bus event:

```yaml
triggers:
  - trigger: event
    event_type: printlog_print_finished
    event_data:
      printer_id: 101
actions:
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.event.data.title }} is done"
```

## Actions

Each action takes an optional `config_entry_id`. It is required only when you have more than one 3D Print Log account set up.

### `printlog.adjust_remaining`

Adds to or removes from a spool's remaining amount. Choose the spool with exactly one of `entity_id` (a slot filament or slot remaining sensor), `spool_id` or `spool_number`. A positive `amount` adds and a negative one removes. `unit` is `g` (default), `mm` or `ml`. `notes` is optional. It returns `before_grams` and `after_grams`.

```yaml
action: printlog.adjust_remaining
data:
  entity_id: sensor.snapmaker_u1_slot_1_remaining
  amount: -12.5
  unit: g
  notes: Purge before the print
response_variable: result
```

### `printlog.set_spool_active`

Marks a spool active or inactive.

```yaml
action: printlog.set_spool_active
data:
  spool_number: 14
  active: false
```

### `printlog.log_maintenance`

Records a completed maintenance entry on a printer. `device_id` and `description` are required. `category`, `notes` and `date` (default: now) are optional. It returns `maintenance_id`.

```yaml
action: printlog.log_maintenance
data:
  device_id: 3f6c1d0a5b7e4c0f9a1d2e3f4a5b6c7d
  category: Lubrication
  description: Greased the Z rods
response_variable: result
```

## Limits

Events come from comparing snapshots, so they have limits. Webhooks for instant events are planned for a later release and will remove most of them.

- If two prints on the same printer finish between two updates, only the one that started last is seen.
- A print shorter than the update interval may fire only its finish event, without a start.
- A start is announced only if Home Assistant sees it within 60 minutes of its start time.
- A print that finished while Home Assistant was down fires on restart, once, if it was the current print or finished within the last 60 minutes. Older history is never announced, and neither is anything when you first add the integration or a new printer.
- Home Assistant restores an event entity's last event after a restart, so the entity looks as if that event just happened. Trigger on `event_type` changes or on the bus events, not on a generic state change.
- "Not enough filament" needs the running print's per-slot estimates, so it stays unknown for prints that have none.

## Privacy

- The API key is sent only in a request header to the API URL you configured. It is never put in a URL, an entity id or a log line.
- Diagnostics redact the API key, your display name, and all printer names, print titles, spool names, descriptions, notes, storage locations and URLs.

## Support

Report problems at [github.com/HoffmanEngineering/3d-print-log-homeassistant/issues](https://github.com/HoffmanEngineering/3d-print-log-homeassistant/issues). Download the diagnostics from the integration's page and attach them. They are safe to share.

## License

MIT
