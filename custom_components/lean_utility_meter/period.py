"""Period (cycle) calculation utilities shared across the integration.

All functions work on timezone-aware datetimes: input is converted to local
time to compute cycle boundaries, output boundaries are returned in UTC.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.util import dt as dt_util

# The "lifetime" cycle mirrors a classic utility meter defined without a
# cycle: the meter never resets and its whole history is one single period.
# Its statistics row is anchored to a fixed hour-aligned epoch.
LIFETIME_CYCLE = "lifetime"
LIFETIME_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
# Sentinel end for the lifetime period; only compared against, never written.
LIFETIME_NEXT = datetime(2200, 1, 1, tzinfo=timezone.utc)


def normalize_cycle(cycle: str | None) -> str:
    """Map a meter's configured cycle to the one used for period math.

    A meter defined without a cycle never resets (same semantics as the core
    utility_meter), so its period math is the single "lifetime" period.
    """
    return cycle or LIFETIME_CYCLE


def get_period_key(dt: datetime, cycle: str) -> Any:
    """Get grouping key for a given timezone-aware datetime and cycle type."""
    if cycle == LIFETIME_CYCLE:
        return LIFETIME_CYCLE
    local_dt = dt_util.as_local(dt)
    if cycle == "hourly":
        return (local_dt.year, local_dt.month, local_dt.day, local_dt.hour)
    elif cycle == "daily":
        return (local_dt.year, local_dt.month, local_dt.day)
    elif cycle == "weekly":
        isocal = local_dt.isocalendar()
        return (isocal[0], isocal[1])
    elif cycle == "monthly":
        return (local_dt.year, local_dt.month)
    elif cycle == "bimonthly":
        return (local_dt.year, (local_dt.month - 1) // 2)
    elif cycle == "quarterly":
        return (local_dt.year, (local_dt.month - 1) // 3)
    elif cycle == "yearly":
        return local_dt.year
    else:
        return (local_dt.year, local_dt.month, local_dt.day)


def get_period_start(dt: datetime, cycle: str) -> datetime:
    """Get the start datetime of the period for the given cycle."""
    if cycle == LIFETIME_CYCLE:
        return LIFETIME_EPOCH
    local_dt = dt_util.as_local(dt)
    if cycle == "hourly":
        start = local_dt.replace(minute=0, second=0, microsecond=0)
    elif cycle == "daily":
        start = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "weekly":
        start = (local_dt - timedelta(days=local_dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "monthly":
        start = local_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "bimonthly":
        month = ((local_dt.month - 1) // 2) * 2 + 1
        start = local_dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "quarterly":
        month = ((local_dt.month - 1) // 3) * 3 + 1
        start = local_dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "yearly":
        start = local_dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        # Default to daily if unknown
        start = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt_util.as_utc(start)


def get_next_period_start(period_start: datetime, cycle: str) -> datetime:
    """Get the next period boundary for a given cycle."""
    if cycle == LIFETIME_CYCLE:
        return LIFETIME_NEXT
    local_dt = dt_util.as_local(period_start)

    if cycle == "hourly":
        next_start = (local_dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    elif cycle == "daily":
        next_start = (local_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "weekly":
        next_start = (local_dt + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "monthly":
        year = local_dt.year + (1 if local_dt.month == 12 else 0)
        month = 1 if local_dt.month == 12 else local_dt.month + 1
        next_start = local_dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "bimonthly":
        step = 2
        idx = (local_dt.month - 1) + step
        year = local_dt.year + (idx // 12)
        month = (idx % 12) + 1
        next_start = local_dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "quarterly":
        step = 3
        idx = (local_dt.month - 1) + step
        year = local_dt.year + (idx // 12)
        month = (idx % 12) + 1
        next_start = local_dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "yearly":
        next_start = local_dt.replace(year=local_dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_start = (local_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    return dt_util.as_utc(next_start)


def count_expected_points_from_first_start(first_start: datetime, now: datetime, cycle: str) -> int:
    """Count expected long-term points from first recorded point to now, inclusive."""
    first_cycle_start = get_period_start(first_start, cycle)
    current_cycle_start = get_period_start(now, cycle)

    if first_cycle_start > current_cycle_start:
        return 1

    points = 1
    cursor = first_cycle_start
    for _ in range(0, 100000):
        if cursor >= current_cycle_start:
            break
        cursor = get_next_period_start(cursor, cycle)
        points += 1
        if cursor > current_cycle_start:
            break

    return points
