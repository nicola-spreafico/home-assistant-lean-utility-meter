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

## Why This Also Makes Dashboards Faster

Fewer stored points don't just save disk space — they make every chart that reads them faster to load, because the row count is what the query has to scan and the frontend has to process, independently of the time range shown.

A monthly History/Statistics graph asking for the last 10 years only ever needs **120 points** (`12 rows/year × 10 years`) from a `monthly` Lean meter, because that's all that was ever written. The database returns them in a single fast index lookup, and the frontend renders 120 points instantly.

With a classic `utility_meter`, that same 10-year monthly chart still has to read every row the source ever produced in that window — easily tens of thousands for a power sensor updating every few seconds — then downsample them to a monthly resolution. The extra rows are pure overhead: read from disk, transferred, and discarded, and it's paid on every single dashboard load, not just once.

## Why Defining Multiple Cycles Per Sensor Is the Right Pattern

With a classic `utility_meter`, the usual advice is to define a *single* high-resolution meter (e.g. `hourly`) and let the frontend downsample it for daily, monthly, and yearly charts — because every additional cycle you define is a *full second copy* of the same source, each writing its own row on every source update. Four meters (`hourly`, `daily`, `monthly`, `yearly`) on the same sensor means 4× the row count, not four complementary views.

With Lean Utility Meter that trade-off disappears, because a Lean meter only ever writes **one row per cycle it closes** — a `yearly` meter writes ~1 row/year regardless of how often the source changes. Defining four Lean meters with four different `cycle` values on the same source is therefore not wasteful duplication: it's four independent, minimal footprints that sum to roughly `365 + 12 + 1` rows/year for `daily` + `monthly` + `yearly` (see [Benefits with Real Numbers](#benefits-with-real-numbers)), each one storing *only* the data a chart at that resolution actually needs — no downsampling required at query time.

This is why the [Energy Dashboard setup](energy-dashboard.md) and the example packages define one Lean meter per cycle instead of one meter plus frontend aggregation: it's the pattern the integration is designed around, not a workaround.

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
