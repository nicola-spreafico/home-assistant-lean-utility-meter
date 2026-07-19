# Operational Notes & Compatibility

[← Back to README](../README.md)

A few compatibility requirements and behavioral quirks worth knowing before relying on this integration in production:

- compatible with modern Home Assistant versions that support External Statistics
- behavior also depends on recorder policy and purge configuration
- a ["not excluded from recorder" Repair](repairs.md#recorder_not_excluded) is a useful reminder, not a functional blocker

## Crash and Restart Recovery

On startup, Home Assistant restores every meter from `core.restore_state` — a file dumped every 15 minutes that does not survive what happens after its last write. After a hard crash (power loss, kernel freeze, OS fault) this leaves a classic utility meter with two failure modes:

1. **Lost rollover reset** — if the crash swallows the cycle reset (or HA is simply down across the boundary), the meter comes back still holding the *previous* cycle's total. Core only schedules the *next* reset, so the missed one is never recovered and the whole previous cycle leaks into the new one — the typical symptom is a day in the Energy Dashboard showing roughly *double (or more) the normal value*.
2. **Stale value** — the restored value can be up to 15 minutes behind what the meter had actually reached before the crash.

Lean meters recover from both at startup, because the statistics row of the running cycle — upserted every 5 minutes inside the recorder database — *does* survive a crash and acts as the authority for reconciliation:

- **Missed reset recovery** — if the restored `last_reset` predates the running cycle, the reset is applied immediately: the meter restarts from `0` and the previous value is archived in the `last_period` attribute. Whatever staleness or catch-up garbage the restore file carried is discarded with it.
- **Fresher value adoption** — if the running cycle already has a statistics row whose state is ahead of the restored value, the row's state is adopted, bounding the loss to the live update interval (5 minutes) instead of the restore dump interval (15).

Both actions are logged at `WARNING` level, so a recovered crash always leaves a trace in the log.

Safety guards: recovery is skipped for `absolute_values` meters (they mirror the source instantaneously) and cron-based cycles; value adoption is additionally skipped for `net_consumption` / `delta_values` meters (non-monotonic, "higher = fresher" does not hold) and during the first 10 minutes of a cycle.

One caveat: recovery protects each **Lean** meter individually. If a Lean meter's *source* is a plain core `utility_meter` (a typical never-resetting "lifetime" stage in a meter chain), that stage still restores from the 15-minute snapshot and can replay a stale value after a crash — and its catch-up jump is indistinguishable from real consumption for every meter downstream. To close the chain, define the lifetime stage as a Lean meter without a `cycle` (see [Configuration](configuration.md#options)): it never resets and its single, 5-minute-fresh LTS row gives it the same crash recovery.

Related detail: the final snapshot taken at rollover is anchored to the row of the cycle that just **ended**. Each cycle's row therefore closes with its exact final total (not the last live upsert, which could undercount by up to 5 minutes), and the new cycle's row never transiently holds the previous cycle's total.

## Home Assistant Down or Restarting at Cycle End

If Home Assistant is down or restarting exactly around cycle rollover (for example around midnight for a daily cycle), the source sensor may already have reset to `0` when Lean performs the final snapshot.

For absolute-value/non-monotonic sources (such as self-sufficiency percentages), this integration includes a boundary safeguard:

- during runtime, it tracks the previous valid value
- on final cycle snapshot (`is_final`), if current value is `0` within the first minutes of the new cycle, it uses the previous valid pre-rollover value instead of persisting `0`

Practical outcome: restart timing near cycle boundary is much less likely to produce a false `0` close point.

Recommended operational practice remains:

- avoid planned restarts in the last/first 5 minutes around cycle rollover when possible
- keep source entities stable and available near cycle boundaries
