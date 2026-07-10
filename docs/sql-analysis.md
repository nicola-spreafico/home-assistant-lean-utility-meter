# Measuring Data Weight (SQL)

[← Back to README](../README.md)

Use these queries to verify, on your own database, the storage impact described in [Benefits with Real Numbers](concept.md#benefits-with-real-numbers). Always replace `sensor.your_entity_id` with your real entity ID.

**Count LTS rows (`statistics`):**

```sql
SELECT count(*) AS row_count
FROM statistics
WHERE metadata_id = (
  SELECT id
  FROM statistics_meta
  WHERE statistic_id = 'sensor.your_entity_id'
);
```

**Expected result**: roughly one row per cycle elapsed since the entity's first point (closed cycles + the current one, which is updated in place, not multiplied) — the per-cycle yearly footprint is listed in [Benefits with Real Numbers](concept.md#benefits-with-real-numbers). A much higher count signals unconsolidated noise — see [`unexpected_points_for_cycle`](repairs.md#unexpected_points_for_cycle).

**Count short-term rows (`states`):**

```sql
SELECT count(*) AS row_count
FROM states
WHERE metadata_id = (
  SELECT metadata_id
  FROM states_meta
  WHERE entity_id = 'sensor.your_entity_id'
);
```

**Expected result**: **0**, if the entity is excluded from recorder as recommended (see [Why Recorder Should Be Excluded](how-it-works.md#why-recorder-should-be-excluded)). Any value greater than 0 means the entity is still being recorded — check your `recorder.exclude` config and the [`recorder_not_excluded`](repairs.md#recorder_not_excluded) Repair.

**Expected steady-state `states` weight** (only relevant if recorder is *not* excluding the entity):

```text
rows_states_steady ~ updates_per_day x purge_keep_days
```

**Expected result**: this is not a query but the formula the previous count should converge to, in the misconfigured case where recorder still tracks the entity — it is the number the Lean exclusion is meant to avoid entirely.
