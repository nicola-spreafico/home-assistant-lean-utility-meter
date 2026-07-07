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

**Expected steady-state `states` weight:**

```text
rows_states_steady ~ updates_per_day x purge_keep_days
```

**Top heaviest entities in `states`:**

```sql
SELECT sm.entity_id, count(*) AS rows_count
FROM states s
JOIN states_meta sm ON sm.metadata_id = s.metadata_id
GROUP BY sm.entity_id
ORDER BY rows_count DESC
LIMIT 20;
```

**Top heaviest entities in `statistics`:**

```sql
SELECT stm.statistic_id, count(*) AS rows_count
FROM statistics st
JOIN statistics_meta stm ON stm.id = st.metadata_id
GROUP BY stm.statistic_id
ORDER BY rows_count DESC
LIMIT 20;
```
