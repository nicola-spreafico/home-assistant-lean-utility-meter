# Configuration

[← Back to README](../README.md)

`lean_utility_meter` extends `utility_meter` behavior, so it inherits standard utility meter semantics (source, cycle, cron, tariffs, and so on) and adds Lean behavior.

## Options

**Inherited main options:**

- `source` (required): source entity
- `cycle`: `hourly`, `daily`, `weekly`, `monthly`, `bimonthly`, `quarterly`, `yearly`
- `cron`: custom reset schedule
- `delta_values`: treat source as deltas instead of cumulative total
- `net_consumption`: allows up/down behavior (useful for percentages)
- `tariffs`: create tariff meters
- `always_available`: entity availability policy

**Lean-specific option:**

- `live_update_interval` (default `00:05:00`): throttling for current-cycle live updates
  - fast source: limits write frequency
  - slow source: writes only when new values arrive
  - `00:00:00`: write on every state change (usually discouraged)

## Examples

**Basic monthly meter:**

```yaml
lean_utility_meter:
  monthly_electricity:
    source: sensor.energy_total
    cycle: monthly
```

**Daily meter with slower live update:**

```yaml
lean_utility_meter:
  daily_energy:
    source: sensor.energy_total
    cycle: daily
    live_update_interval: "00:30:00"
```

**Recommended recorder exclusion** (see [Why Recorder Should Be Excluded](how-it-works.md#why-recorder-should-be-excluded)):

```yaml
recorder:
  exclude:
    entities:
      - sensor.monthly_electricity
      - sensor.daily_energy
```
