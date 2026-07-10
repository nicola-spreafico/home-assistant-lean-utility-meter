# Lean Utility Meter

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/nicola-spreafico/home-assistant-lean-utility-meter?include_prereleases)](https://github.com/nicola-spreafico/home-assistant-lean-utility-meter/releases)
[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey.svg)](LICENSE)
[![GitHub Last Commit](https://img.shields.io/github/last-commit/nicola-spreafico/home-assistant-lean-utility-meter)](https://github.com/nicola-spreafico/home-assistant-lean-utility-meter/commits)
[![GitHub Issues](https://img.shields.io/github/issues/nicola-spreafico/home-assistant-lean-utility-meter)](https://github.com/nicola-spreafico/home-assistant-lean-utility-meter/issues)

> **"How I moved from 13,246 database rows to one row per year"** — a real yearly gas meter tracked since January 2025 had accumulated 13,246 hourly rows in Long-Term Statistics; after one `thin_history` run it stores exactly one consolidated point per year (a 4,400:1 reduction), while the live counter keeps updating in real time.

**A drop-in extension of Home Assistant's `utility_meter` that keeps your counters live in the UI while storing only what matters: one consolidated point per closed cycle, instead of thousands of intermediate rows.**

Lean Utility Meter separates the two jobs a meter actually has:

- **Live visualization** — the sensor keeps growing in real time on your dashboards, exactly like a classic utility meter.
- **Consolidated persistence** — long-term history gets exactly **one point per closed cycle**, written directly into Long-Term Statistics outside the recorder pipeline.

The result: reactive dashboards in the short term, a dramatically lighter database in the long term — with full compatibility with standard `utility_meter` options (cycles, tariffs, cron, net consumption, …). Wondering why a meter should ever store thousands of rows in the first place? Start from [Concept & Motivation](docs/concept.md).

## Quick Start

```yaml
lean_utility_meter:
  monthly_electricity:
    source: sensor.energy_total
    cycle: monthly

recorder:
  exclude:
    entities:
      - sensor.monthly_electricity   # Lean manages its own LTS — recorder isn't needed
```

That's it: the meter behaves like a normal utility meter in the UI, but its stored history stays at one point per month. See [Configuration](docs/configuration.md) for all options and [How It Works](docs/how-it-works.md) for why the recorder exclusion is part of the design.

## Highlights

- **Drop-in** — inherits standard `utility_meter` semantics: `cycle`, `cron`, `tariffs`, `delta_values`, `net_consumption`, …
- **Live but throttled** — the current cycle updates in the UI in real time; LTS writes are throttled by a configurable `live_update_interval`
- **Migration-friendly** — dedicated services to import history from an existing meter and to retro-thin noisy legacy data
- **Self-monitoring** — raises Home Assistant Repairs when the setup drifts from the recommended state
- **Beyond utilities** — can also track non-monotonic signals (e.g. photovoltaic self-sufficiency percentage) on periodic cycles

## Documentation

| Page | What you'll find |
| --- | --- |
| [Concept & Motivation](docs/concept.md) | Why this exists: the problem, benefits with real numbers, community references |
| [How It Works](docs/how-it-works.md) | Operational model and why recorder exclusion is by design |
| [Configuration](docs/configuration.md) | All options (inherited and Lean-specific) with YAML examples |
| [Services & Actions](docs/services.md) | `thin_history`, `import_history`, `clear_history`, `calibrate`, `reset` — what each touches and when to use it |
| [Migration Workflows](docs/migration.md) | Converting an existing meter in place, or migrating in parallel with zero downtime |
| [Repairs](docs/repairs.md) | The self-diagnostics the integration reports and how to react |
| [Measuring Data Weight (SQL)](docs/sql-analysis.md) | Queries to verify the real storage impact on your own database |
| [Operational Notes](docs/operational-notes.md) | Compatibility, restart-at-rollover edge cases |
| [Advanced Uses](docs/advanced-uses.md) | Tracking non-monotonic metrics beyond classic utilities |

Suggested reading path: understand the problem ([Concept](docs/concept.md)) → understand the model ([How It Works](docs/how-it-works.md)) → apply it ([Configuration](docs/configuration.md), [Migration](docs/migration.md)) → verify with numbers ([SQL](docs/sql-analysis.md)).

## Safety and Disclaimer

This integration includes actions that can delete or rewrite statistical data. Before running `thin_history`, `clear_history`, or large imports:

1. make a recent verified backup
2. test first on a non-critical entity
3. apply to production only after validation

Use at your own risk: authors are not responsible for data loss or corruption caused by misuse.
