# Operational Notes & Compatibility

[← Back to README](../README.md)

- compatible with modern Home Assistant versions that support External Statistics
- behavior also depends on recorder policy and purge configuration
- a ["not excluded from recorder" Repair](repairs.md#recorder_not_excluded) is a useful reminder, not a functional blocker

## Home Assistant Down or Restarting at Cycle End

If Home Assistant is down or restarting exactly around cycle rollover (for example around midnight for a daily cycle), the source sensor may already have reset to `0` when Lean performs the final snapshot.

For absolute-value/non-monotonic sources (such as self-sufficiency percentages), this integration includes a boundary safeguard:

- during runtime, it tracks the previous valid value
- on final cycle snapshot (`is_final`), if current value is `0` within the first minutes of the new cycle, it uses the previous valid pre-rollover value instead of persisting `0`

Practical outcome: restart timing near cycle boundary is much less likely to produce a false `0` close point.

Recommended operational practice remains:

- avoid planned restarts in the last/first 5 minutes around cycle rollover when possible
- keep source entities stable and available near cycle boundaries
