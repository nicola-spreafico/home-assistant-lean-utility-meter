# Concept & Motivation

[← Back to README](../README.md)

## Why This Exists

> "Why, with a monthly meter, should I store thousands of values instead of just the 12 points that matter each year?"
>
> "Why, to visualize yearly monthly trends, do I need to scan through thousands of intermediate rows?"
>
> "Why can I not have a counter that grows in real time but is stored in a consolidated way?"

A classic utility meter updates — and records — every time its source sensor changes, which for a power sensor can mean tens of thousands of database rows per year just to answer the question "how much did I consume each month?". This integration was created to solve that data bloating at the root, while leaving the live counter behavior untouched.

## Benefits with Real Numbers

**`statistics` table (Long-Term Statistics)** — exactly one point per closed cycle, so the yearly footprint depends only on the cycle:

- `daily` -> about 365 rows/year
- `monthly` -> 12 rows/year
- `yearly` -> 1 row/year

**`states` table (Short-Term History)** — since Lean entities are meant to be excluded from recorder (see [Why Recorder Should Be Excluded](how-it-works.md#why-recorder-should-be-excluded)):

- about 0 rows/year

If recorder is misconfigured and still includes the entity, the `states` table falls back to the usual steady-state growth of any recorded entity — see [Measuring Data Weight (SQL)](sql-analysis.md) for the formula and the queries to verify the impact on your own database.

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
