# How It Works

[← Back to README](../README.md)

## Operational Model

**Data model** — the integration uses `Recorder External Statistics` APIs to write directly into LTS.

**Closed cycles** — at cycle end (reset), the final value of the completed period is consolidated.

**Current cycle** — during the active cycle the sensor keeps updating in the UI. On LTS, the same slot for the current period is updated (live update logic), preventing uncontrolled point growth inside the same cycle.

**Existing historical data (retro-thinning)** — if you start from classic utility meter history with many intermediate points, use `thin_history` service to keep only consolidated period peaks and remove historical noise.

## Why Recorder Should Be Excluded

These meters are **deliberately** excluded from the native recorder because they manage their own Long-Term Statistics in a custom way, outside the recorder's normal pipeline. Lean writes directly into the `statistics` LTS tables through the External Statistics APIs, so it does **not** need the recorder to persist the entity's state history at all.

In other words, the `states` table is bypassed entirely by design: it would only produce duplicated, high-frequency rows that Lean has no use for, while the meaningful consolidated history already lives in LTS under Lean's own control.

If, on the contrary, recorder still includes a Lean entity:

1. the `states` table keeps growing with source update frequency
2. you lose most short-term space savings
3. a [Repair warning](repairs.md#recorder_not_excluded) is created to remind you

Recommended configuration:

```yaml
recorder:
  exclude:
    entities:
      - sensor.your_lean_entity
```
