"""Entity services for Lean Utility Meter.

One module per service:
- calibrate: set a manual calibration value on the meter
- import_history: import consolidated history from a source entity (legacy migration)
- thin_history: consolidate duplicate statistics points (retroactive cleanup)
- clear_history: permanently delete all statistics for the entity
"""
