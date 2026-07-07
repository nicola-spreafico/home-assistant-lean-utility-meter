# Concept & Motivation

[← Back to README](../README.md)

## Why This Exists

> "Why, with a monthly meter, should I store thousands of values instead of just the 12 points that matter each year?"
>
> "Why, to visualize yearly monthly trends, do I need to scan through thousands of intermediate rows?"
>
> "Why can I not have a counter that grows in real time but is stored in a consolidated way?"

This integration was created to solve data bloating in high-frequency utility meters.

## Project Goal

The goal is to clearly separate:

1. **live visualization** — the current cycle keeps growing in the UI
2. **consolidated persistence** — one point per closed cycle

In short: reactive dashboards in the short term, lighter database in the long term.

## Benefits with Real Numbers

**`statistics` table (Long-Term Statistics)** — number of rows depends on the cycle:

- `daily` -> about 365 rows/year
- `monthly` -> 12 rows/year
- `yearly` -> 1 row/year

**`states` table (Short-Term History)** — since Lean entities are meant to be excluded from recorder (see [Why Recorder Should Be Excluded](how-it-works.md#why-recorder-should-be-excluded)):

- about 0 rows/year

If recorder is misconfigured and still includes the entity, it falls back to the usual steady-state order of magnitude:

- `updates_per_day x purge_keep_days`
- plus a temporary buffer before purge runs

So, in practice:

- Lean's gain covers both consolidated historical storage (`statistics`) and short-term storage (`states`), as long as the recommended recorder exclusion is in place

## Community References and Prior Discussions

This topic has been discussed multiple times in the Home Assistant ecosystem:

- GitHub Feature Request: [Utility Meter option: Consolidate History #2786](https://github.com/home-assistant/feature-requests/discussions/2786)
- Home Assistant Architecture: [Collect long-term statistics #559](https://github.com/home-assistant/architecture/discussions/559)
- Forum: [Utility Meter should only write one a cycle](https://community.home-assistant.io/t/utility-meter-should-only-write-one-a-cycle/192434/1)
- Forum: [Utility Meter and records created in state_attributes](https://community.home-assistant.io/t/utility-meter-and-records-created-in-table-state-attributes/575553)
- Forum: [Utility meter reports (daily/monthly/yearly)](https://community.home-assistant.io/t/utility-meter-reports-daily-monthly-yearly/198262)

Official API / statistics references:

- Developer docs (LTS): [Sensor entity - Long-term Statistics](https://developers.home-assistant.io/docs/core/entity/sensor/#long-term-statistics)
- Core recorder implementation: [homeassistant/components/recorder/statistics.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/recorder/statistics.py)
- API evolution: [Recorder statistics API changes](https://developers.home-assistant.io/blog/2025/10/16/recorder-statistics-api-changes/)
