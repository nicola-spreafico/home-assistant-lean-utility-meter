# Advanced Uses Beyond Utilities

[← Back to README](../README.md)

Although the primary purpose of Lean Utility Meter is utility-like counters, the same cycle-based persistence model works for any periodic signal — not just monotonically increasing ones. The key is the `absolute_values` option: instead of accumulating deltas from the source, the meter tracks the source's own value directly.

## Why this works for non-monotonic signals

A classic utility meter only makes sense for quantities that keep growing (energy, water, gas): the value at cycle end **is** the cumulative total, so consolidating history to one point per cycle loses nothing meaningful.

With `absolute_values: true`, the same one-point-per-cycle model is applied to a signal that goes **up and down** during the cycle instead of accumulating. During the active cycle, the entity mirrors the source in real time — in the UI and in the live-updated current-cycle LTS slot, throttled by `live_update_interval` like any other Lean meter (see [Operational Model](how-it-works.md#operational-model)). But once the cycle closes, only **the value at that exact closing instant** is kept as the permanent historical point for that cycle — the intraday rise and fall is visible live while it happens, but is not part of the consolidated history afterwards.

This is deliberate: it turns the integration into a **periodic snapshot tool** — "what was this value at the end of each day/month?" — rather than a full curve recorder. If you need the full intraday curve preserved forever, this is not the right tool; use the recorder/history for that instead.

## Example: Self-Consumption / Self-Sufficiency Percentage

A photovoltaic self-sufficiency percentage is a typical case for this pattern — but only if the source sensor itself is built correctly.

**Important**: the source must be an **energy-based ratio**, computed from the cumulative energy counted since the start of the day — `self-consumed energy today / total consumption energy today` — not an **instantaneous power ratio** (`current self-consumed power / current total power`). This distinction matters precisely because of how the closing snapshot works: a power-based ratio only reflects the split at one instant, and at the cycle boundary (midnight) that instant is typically near `0%`, since there is no solar production at night — the snapshot would capture a meaningless value. An energy-based ratio, instead, is already a running daily aggregate: it may be noisy or undefined in the very first minutes after midnight (little energy counted yet), then converges through the day as both counters accumulate, and by the time the cycle closes its value **is** the day's real self-sufficiency figure — exactly the number you want the closing snapshot to preserve.

```yaml
lean_utility_meter:
  self_consumption_ratio:
    source: sensor.self_consumption_percent  # must be energy-based (cumulative), not power-based (instantaneous)
    cycle: daily
    net_consumption: true
    absolute_values: true
```

With this configuration, the sensor's live value tracks the source's running daily ratio in real time all day long. But in the historical `statistics` series, each closed day is represented by a **single point**: the ratio captured at midnight when the cycle closes — which, being energy-based, already represents the whole day and not an arbitrary instant — in the same way a utility meter would record a cumulative reading at the end of a billing period.

For how the integration protects this closing snapshot from a false `0` if Home Assistant restarts right around cycle rollover, see [Home Assistant Down or Restarting at Cycle End](operational-notes.md#home-assistant-down-or-restarting-at-cycle-end).

## Other Examples

The same pattern applies to any gauge-like signal where you only care about periodic checkpoints, not the full history of fluctuations:

**Home battery state of charge, sampled daily:**

```yaml
lean_utility_meter:
  battery_soc_daily:
    source: sensor.home_battery_state_of_charge
    cycle: daily
    net_consumption: true
    absolute_values: true
```

Useful to track "how charged was the battery at the end of each day?" without keeping every charge/discharge swing in long-term history.

**Water tank level, sampled monthly:**

```yaml
lean_utility_meter:
  tank_level_monthly:
    source: sensor.water_tank_level_percent
    cycle: monthly
    net_consumption: true
    absolute_values: true
```

Useful for a monthly checkpoint of tank level regardless of how many times it was refilled or drawn down during the month.

**Indoor relative humidity, sampled daily:**

```yaml
lean_utility_meter:
  humidity_daily_close:
    source: sensor.living_room_humidity
    cycle: daily
    net_consumption: true
    absolute_values: true
```

Useful as a lightweight long-term trend (e.g. "humidity at midnight over the past year") without the cost of keeping every reading.

## Rule of Thumb

Use `absolute_values: true` whenever the question you want to answer is **"what was this value at a specific recurring checkpoint?"** rather than **"how much did this accumulate?"**. If the source can go up and down and you only care about periodic snapshots, this pattern fits; if you need the full curve preserved, keep the entity in recorder instead of (or in addition to) wrapping it in a Lean meter.
