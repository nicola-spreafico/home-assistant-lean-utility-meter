# Migration Workflows

[← Back to README](../README.md)

| | Scenario 1: Convert In Place | Scenario 2: Parallel Migration |
| --- | --- | --- |
| **Goal** | Keep the same `entity_id` | Zero downtime, minimal risk |
| **Entity ID** | Reused (same as old meter) | New/temporary, renamed at cut-over |
| **Downtime** | 2 restarts | None |
| **Rollback** | Not possible once converted | Trivial — delete the new entity |
| **Validation window** | None | Yes, side by side with the old meter |
| **Key service** | `thin_history` | `import_history` |

## Scenario 1: Convert an Existing Utility Meter

**Goal**: reuse the same `entity_id` so the new Lean meter continues writing into the *same* LTS series the old meter left behind (statistics are keyed by `statistic_id` = entity ID) — no broken dashboard cards, no re-linking, history stays attached.

**Steps:**

1. disable/comment old `utility_meter` block
2. restart Home Assistant
3. remove old entity (restored/orphan) from the UI registry
4. create `lean_utility_meter` block with the same name/id
5. restart again
6. run `lean_utility_meter.thin_history` to clean legacy noise

**Why two restarts:**

| Restart | Purpose |
| --- | --- |
| 1st (after removing old block) | Frees the `entity_id`: while the old block is loaded, its entity *owns* the ID and the registry won't let you delete it. Restarting turns it into a *restored/orphan* entry, which can be removed. |
| 2nd (after adding Lean block) | Loads the new entity, which now finds the ID free and claims it cleanly. |

⚠️ **Skipping the first restart or step 3** → Home Assistant finds the ID still taken and registers the new entity as `sensor.name_2`, breaking the migration: the new meter would write into a *different* statistics series instead of continuing the old one.

**Note**: if you later remove a Lean meter from YAML, its historical statistics become an orphaned series like any other integration's — deletable via `Developer Tools > Statistics` as usual.

## Scenario 2: Smooth Parallel Migration with Import

**Goal**: run old and new meter side by side before committing, so you can validate before cutting over and roll back trivially if something looks wrong.

**Steps:**

1. create a new Lean entity with a secondary ID (e.g. `sensor.energy_new_lean`)
2. run it in parallel with the old meter
3. run `lean_utility_meter.import_history` from old to new
4. let both run for a while and validate dashboards/automations
5. once satisfied:
   - remove old entity
   - rename the Lean entity/ID to its final production name
6. optional: run `thin_history` if needed

**Trade-off**: requires a final rename step at cut-over, in exchange for zero downtime and an easy rollback path (just delete the Lean entity) during validation.
