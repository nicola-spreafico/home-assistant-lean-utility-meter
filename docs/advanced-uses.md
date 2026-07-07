# Advanced Uses Beyond Utilities

[← Back to README](../README.md)

Although the primary purpose of Lean Utility Meter is utility-like counters, the same cycle-based persistence model can also be used for generic periodic dimensions.

Even when some measures are not increasing (for example photovoltaic self-sufficiency percentage, usually very high during the day and gradually decreasing during the evening), and even when they are not strict "utilities" and not true "meter" counters, this integration can still be a practical way to record generic signals on periodic cycles.

**Non-monotonic percentage metrics** — use absolute-value tracking rather than delta accumulation:

```yaml
lean_utility_meter:
  self_consumption_ratio:
    source: sensor.self_consumption_percent
    cycle: daily
    net_consumption: true
    absolute_values: true
```

For how the integration protects these non-monotonic sources from false `0` values at cycle rollover, see [Home Assistant Down or Restarting at Cycle End](operational-notes.md#home-assistant-down-or-restarting-at-cycle-end).
