# Changelog

## 0.1.0 (unreleased)

- API key setup with reauthentication and reconfiguration. One entry per account and API URL.
- A device per printer: connection, printing, maintenance due, current print, print start and estimated end, last print result, all-time print time and last maintenance.
- Per-slot sensors for the loaded filament and its remaining grams, plus a "not enough filament" binary sensor per slot.
- An account device with prints and grams this month, and unread notifications.
- Print events (started, succeeded, failed) as a per-printer event entity and as bus events `printlog_print_started`, `printlog_print_finished` and `printlog_print_failed`.
- Actions `printlog.adjust_remaining`, `printlog.set_spool_active` and `printlog.log_maintenance`.
- A repair issue when the API sends a schema version this release does not read.
- Diagnostics with the API key, names, titles, notes, storage locations and URLs redacted.
- Works with older API versions, with fewer entities.
