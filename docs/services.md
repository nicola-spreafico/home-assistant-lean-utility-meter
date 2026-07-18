# Services & Actions

[← Back to README](../README.md)

Each service has a different **scope**: some only touch already-closed cycles, some only the currently active cycle, and some rebuild both.

### Cron limitation

`cron` (custom reset schedule) only drives *live* behavior — when the entity resets and starts a new in-memory cycle, inherited straight from core `utility_meter`. It is **not** understood by `thin_history` or `import_history`: both consolidate history using the fixed named `cycle` boundaries (`hourly`, `daily`, `monthly`, …) via the same period calculation the live writer uses for `cycle`, and have no equivalent logic for arbitrary cron expressions. If a meter is configured with `cron` and no `cycle`, these two services silently fall back to monthly boundaries — which will not match your actual reset schedule. If you need history consolidated on a custom schedule, either accept the nearest named `cycle` for these two services, or treat the meter as `hourly`/`daily` for history purposes and let the live `cron` reset handle the display-side cadence separately.

| Service | Purpose | Scope |
| --- | --- | --- |
| `lean_utility_meter.thin_history` | Retro-consolidate one or more Lean meters | Closed cycles **and** current cycle |
| `lean_utility_meter.import_history` | Import historical statistics from a source | Closed cycles only |
| `lean_utility_meter.clear_history` | Permanently delete historical LTS data | Closed cycles **and** current cycle |
| `lean_utility_meter.calibrate` | Calibrate the current meter value | Current cycle only |
| `utility_meter.reset` | Native reset (supported) | Current cycle only (closes it) |

## Closed cycles only

**`lean_utility_meter.import_history`** — imports historical statistics from a source into the Lean target.

- Input: target Lean `entity_id`, `source_entity`
- Notes: current active cycle is explicitly excluded from the search window; import is blocked if target already has past-cycle data, for safety
- Output: `status`, `imported_points`, `source_entity`, `target_entity`
- **When to use it**: during a [parallel migration](migration.md#scenario-2-smooth-parallel-migration-with-import), to backfill a brand-new Lean entity with the past history of the old meter so dashboards don't show a gap or a discontinuity when you cut over.

## Current cycle only

**`lean_utility_meter.calibrate`** — calibrates the current meter value.

- Input: `entity_id`, `value`
- Note: only updates the live in-memory value (and the source-delta baseline); it does not write to LTS directly — the next live update will persist the corrected value into the current cycle's statistic row. Calibrating to `0` gives you a silent display reset without immediate consolidation.
- **When to use it**: to correct drift against a physical meter reading (e.g. align the sensor to what the utility company's meter actually shows), or to silently re-baseline a counter without closing/consolidating the current cycle — unlike `utility_meter.reset`, which does consolidate.

**`utility_meter.reset` (native service)** — supported: consolidates the current cycle as final (`is_final`) and then starts a fresh current cycle. It does not touch any previously closed cycle.

- **When to use it**: to force-close the current cycle on demand (e.g. manual billing period boundary, testing cycle rollover behavior) instead of waiting for the automatic `cycle`/`cron` schedule.

## Closed cycles and current cycle together

Both services below query **all** statistics rows for the entity (from the beginning of history through now), so whatever is currently stored for the in-progress cycle is included and rewritten/deleted along with every closed cycle.

**`lean_utility_meter.thin_history`** — retro-consolidates one or more Lean meters, keeping period-final points.

- Input: `entity_id` (entity list)
- Output: per-entity summary — rows found, kept, deleted (hourly and short-term)
- **When to use it**: right after [converting an existing utility meter](migration.md#scenario-1-convert-an-existing-utility-meter), to clean up the intermediate noise inherited from the old, non-Lean history. Also useful any time you suspect a source misbehaved (e.g. flooded updates) and left extra points you want consolidated down to one per period — current cycle included.

**`lean_utility_meter.clear_history`** — permanently deletes historical LTS data for the target.

- Input: `entity_id`, `confirm_deletion: DELETE`
- Output: deleted row counters (`hourly_deleted`, `short_term_deleted`)
- **When to use it**: to wipe a bad import, a test/throwaway entity, or a meter you're decommissioning entirely. Since it also erases the current cycle's row, the meter effectively starts from a blank slate. Destructive and irreversible — see [Safety and Disclaimer](../README.md#safety-and-disclaimer) before running it on production data.
