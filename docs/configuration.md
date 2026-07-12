# Configuration

[← Back to README](../README.md)

`lean_utility_meter` extends `utility_meter` behavior, so it inherits standard utility meter semantics (source, cycle, cron, tariffs, and so on) and adds Lean behavior.

## Options

**Inherited main options:**

- `source` (required): source entity
- `cycle`: `hourly`, `daily`, `weekly`, `monthly`, `bimonthly`, `quarterly`, `yearly`
- `cron`: custom reset schedule — ⚠️ only drives the *live* reset timing (inherited from core `utility_meter`); `thin_history` and `import_history` don't understand cron and only consolidate against the named `cycle` boundaries (see [Services & Actions](services.md#cron-limitation))
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

## Complete Example: A Full Meter Chain

This is a realistic, end-to-end configuration for a cumulative energy source (e.g. solar production, water, gas — anything with a monotonically increasing counter), showing the three-layer pattern used throughout this integration's own examples and putting into practice both [Why This Also Makes Dashboards Faster](concept.md#why-this-also-makes-dashboards-faster) and [Why Defining Multiple Cycles Per Sensor Is the Right Pattern](concept.md#why-defining-multiple-cycles-per-sensor-is-the-right-pattern).

```yaml
# 1. Transient: a plain template sensor that produces the source the meter reads from.
#    Here it's a simple passthrough, but this layer is also where you would combine multiple raw sources (e.g. summing several inverters) before feeding the meter.
template:
  - sensor:
      - name: my_source_energy_transient
        unique_id: my_source_energy_transient
        unit_of_measurement: kWh
        device_class: energy
        state_class: total_increasing
        availability: >
          {{ states('sensor.my_raw_source') | is_number }}
        state: >
          {{ states('sensor.my_raw_source') | float }}

# ----------------------------------------------------------------------------

# 2. Lifetime: a plain core `utility_meter` (not Lean) with no `cycle`, so it never resets — it just mirrors the transient's running total. 
#    It is kept OUT of the recorder: its only job is to hold "the all-time number" as a live entity state, not to keep history, so it writes exactly 0 rows/year either way.
#    Just as important: this makes the all-time total YOUR system's own value, decoupled from the raw sensor. 
#    If the raw sensor gets replaced, glitches, or its own internal lifetime counter resets (firmware update, device swap, integration re-auth), only the transient briefly reflects that 
#    this `utility_meter` keeps accumulating from where it was and calibrate can patch the gap. 
#    Reading the raw sensor's lifetime attribute directly, with no local counter of your own, means every one of those upstream incidents becomes permanent, unrecoverable data loss in your own history.
utility_meter:
  my_source_energy_lifetime:
    unique_id: my_source_energy_lifetime
    source: sensor.my_source_energy_transient
    always_available: true

# ----------------------------------------------------------------------------

# 3. Lean meters: four independent, minimal-footprint views of the same lifetime source, one per cycle. 
#    `hourly` is the one resolution the Energy Dashboard actually needs; 
#    `daily`/`monthly`/`yearly` exist purely to feed custom
#    History/Statistics graphs at their native resolution, with no downsampling needed at query time (see linked concept sections above).
lean_utility_meter:
  # Feeds the built-in Energy Dashboard
  my_source_energy_hourly:
    unique_id: my_source_energy_hourly
    source: sensor.my_source_energy_lifetime
    cycle: hourly

  # Feeds custom daily/monthly/yearly graphs (e.g. History/Statistics cards)
  my_source_energy_daily:
    unique_id: my_source_energy_daily
    source: sensor.my_source_energy_lifetime
    cycle: daily

  my_source_energy_monthly:
    unique_id: my_source_energy_monthly
    source: sensor.my_source_energy_lifetime
    cycle: monthly

  my_source_energy_yearly:
    unique_id: my_source_energy_yearly
    source: sensor.my_source_energy_lifetime
    cycle: yearly

# ----------------------------------------------------------------------------

# All five entities are excluded from recorder: the transient and the lifetime because they only exist to feed the meters below them, and the four Lean meters per the recommendation above.
recorder:
  exclude:
    entities:
      - sensor.my_source_energy_transient
      - sensor.my_source_energy_lifetime
      # --------
      - sensor.my_source_energy_hourly
      - sensor.my_source_energy_daily
      - sensor.my_source_energy_monthly
      - sensor.my_source_energy_yearly
```

With this chain, the yearly database footprint for this single source is `~365 + 12 + 1` LTS rows from the three coarser Lean meters plus `~8,760` from the hourly one feeding the Energy Dashboard — and `0` rows anywhere else, regardless of how often `sensor.my_raw_source` itself updates.
