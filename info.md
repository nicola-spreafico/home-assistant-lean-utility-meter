# Lean Utility Meter

This integration decouples live sensor visual representation from long-term stats persistence, fully solving database bloating issues.

## Features
- **Transient-only short-term states**: Keeps only the current cycle value, discarding intermediate state changes from the short-term states/recorder database.
- **Cycle-end statistics injection**: Writes a single peak value to long-term statistics (LTS) at reset.
- **Database thinning service**: Invocable retroactive compaction tool to instantly compress bloated historical databases.
