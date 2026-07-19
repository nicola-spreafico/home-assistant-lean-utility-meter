"""Write live/final consolidated statistics for Lean Utility Meter sensors.

Core loop of the integration: capture the current cycle value and upsert it as
a single statistics row per cycle (the "lean" part). Also hosts the startup
recovery that reconciles the restored meter value with the recorder database
(see async_recover_after_restart).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.util import dt as dt_util

from .period import get_period_start, normalize_cycle
from .util import parse_stat_start, resolve_unit, stat_field

if TYPE_CHECKING:
    from .entity import LeanUtilityMeterSensor

_LOGGER = logging.getLogger(__name__)

# A final (reset-time) capture that runs within this window after a cycle
# boundary belongs to the cycle that just closed, not to the new one.
ROLLOVER_GRACE_SECONDS = 600


async def async_capture_and_write_stats(meter: LeanUtilityMeterSensor, is_final: bool = False) -> None:
    """Capture the current cycle value and write it as external statistic."""
    _LOGGER.debug("Capturing statistics for %s (final=%s)", meter.entity_id, is_final)

    try:
        current_val = float(meter.native_value)
    except (ValueError, TypeError, AttributeError):
        current_val = 0.0

    statistic_id = meter.entity_id

    now = dt_util.utcnow()

    # Calculate the start of the current period to ensure we only have 1 row per period
    cycle = normalize_cycle(meter._cycle)
    period_start = get_period_start(now, cycle)
    elapsed_from_period_start = (now - period_start).total_seconds()

    # We always use the period start as the timestamp to ensure we update
    # the same statistics row (upsert) for the entire cycle, achieving 1 point per cycle.
    stat_timestamp = period_start

    # A final capture triggered by the rollover reset runs a moment *after*
    # the boundary, but the value it carries is the closing total of the cycle
    # that just ended: anchor it to that cycle's row, so the closing row gets
    # its exact final total and the new cycle's row never transiently holds
    # the previous cycle's value.
    if is_final and elapsed_from_period_start <= ROLLOVER_GRACE_SECONDS:
        stat_timestamp = get_period_start(period_start - timedelta(seconds=1), cycle)

    # Absolute-value sources can reset to 0 exactly at cycle rollover.
    # If the reset snapshot runs in the first minutes of the new cycle,
    # preserve the latest known pre-rollover value instead of persisting 0.
    if is_final and meter._absolute_values:
        if elapsed_from_period_start <= 600 and current_val == 0.0:
            candidate = meter._previous_valid_state
            if candidate is not None:
                try:
                    candidate_val = float(candidate)
                    if candidate_val > 0.0:
                        current_val = candidate_val
                except (ValueError, TypeError):
                    pass

    last_sum = 0.0
    try:
        # Look back to the previous cycle's start so its closing row is found
        # even for cycles longer than a few days (monthly, quarterly, yearly).
        start_search = get_period_start(stat_timestamp - timedelta(hours=1), cycle)
        stats_map = await get_recorder_instance(meter.hass).async_add_executor_job(
            statistics_during_period,
            meter.hass,
            start_search,
            None, # end_time = now
            [statistic_id],
            "hour",
            None,
            {"sum"}
        )

        if statistic_id in stats_map and stats_map[statistic_id]:
            stats_list = stats_map[statistic_id]

            base_sum = 0.0
            found = False

            # We need the highest sum from a point strictly BEFORE today's start
            for stat in reversed(stats_list):
                s_start = parse_stat_start(stat_field(stat, "start"))

                if s_start and s_start < stat_timestamp:
                    base_sum = stat_field(stat, "sum") or 0.0
                    found = True
                    _LOGGER.info("%s: Found base sum %s at %s", statistic_id, base_sum, s_start)
                    break

            last_sum = base_sum if found else 0.0
        else:
            _LOGGER.debug("%s: No statistics found in period, starting from 0", statistic_id)
    except Exception as err:
        _LOGGER.error("Error retrieving base statistics for %s: %s", statistic_id, err)

    new_sum = last_sum + current_val

    unit = resolve_unit(meter)

    # Leave unit_class empty to allow HA to handle it or user to customize via YAML
    unit_class = None

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        unit_class=unit_class,
        name=meter.name or meter.entity_id,
        source="recorder",
        statistic_id=statistic_id,
        unit_of_measurement=unit,
    )

    stat_data = StatisticData(
        start=stat_timestamp,
        state=current_val,
        sum=new_sum,
    )

    try:
        _LOGGER.info(
            "Saving %s statistic for %s at %s: state=%s, sum=%s",
            "final" if is_final else "live",
            statistic_id,
            stat_timestamp,
            current_val,
            new_sum,
        )
        async_import_statistics(meter.hass, metadata, [stat_data])
    except Exception as err:
        _LOGGER.error("Failed to add statistics for %s: %s", statistic_id, err)


async def async_recover_after_restart(meter: LeanUtilityMeterSensor) -> None:
    """Reconcile the restored meter value with the recorder statistics.

    core.restore_state is dumped every 15 minutes and a hard crash loses
    everything after the last dump: the meter can come back with a value up to
    15 minutes old — or belonging to the previous cycle entirely, when the
    crash swallowed the rollover reset. The statistics row for the running
    cycle is upserted every 5 minutes inside the recorder database, which does
    survive a crash, so on startup the database is the better authority:

    1. Restored last_reset older than the running cycle: the rollover happened
       while HA was down, or its reset was lost with the crash. Core only
       schedules the *next* reset, so the missed one would never be recovered
       and the whole previous cycle would leak into the new one. Apply the
       reset now (value to 0, previous value archived in last_period).
    2. Otherwise, if the running cycle already has a statistics row whose
       state is ahead of the restored value, adopt it (monotonic meters only:
       for them a higher value is by definition the fresher one).
    """
    # Absolute meters mirror the source instantaneously (nothing accumulates
    # across a gap) and cron cycles have no period math here: leave both alone.
    if meter._absolute_values or meter._cron_pattern is not None:
        return

    now = dt_util.utcnow()
    cycle = normalize_cycle(meter._cycle)
    period_start = get_period_start(now, cycle)

    restored: Decimal | None = None
    if meter.native_value is not None:
        try:
            restored = Decimal(str(meter.native_value))
        except (ValueError, TypeError, InvalidOperation):
            restored = None

    # --- 1) Missed rollover reset ---
    last_reset = meter._last_reset
    if last_reset is not None and dt_util.as_utc(last_reset) < period_start:
        _LOGGER.warning(
            "%s: restored last_reset (%s) predates the current %s cycle (%s): "
            "recovering the missed reset, previous value %s moved to last_period",
            meter.entity_id,
            last_reset,
            cycle,
            period_start,
            restored,
        )
        meter._last_period = restored if restored is not None else Decimal(0)
        meter._attr_native_value = Decimal(0)
        meter._last_reset = period_start
        return

    # --- 2) Adopt the fresher value from the running cycle's row ---
    if meter._sensor_delta_values or meter._sensor_net_consumption:
        # Non-monotonic meters: "higher = fresher" does not hold.
        return

    # Right after a rollover the row may still carry the previous cycle's
    # closing total written by an older version of the final capture: not a
    # trustworthy recovery source, and there is nothing to recover yet anyway.
    if (now - period_start).total_seconds() <= ROLLOVER_GRACE_SECONDS:
        return

    row_state: Decimal | None = None
    try:
        stats_map = await get_recorder_instance(meter.hass).async_add_executor_job(
            statistics_during_period,
            meter.hass,
            period_start,
            None,  # end_time = now
            [meter.entity_id],
            "hour",
            None,
            {"state"},
        )
        for stat in stats_map.get(meter.entity_id, []):
            value = stat_field(stat, "state")
            if value is None:
                continue
            try:
                candidate = Decimal(str(value))
            except (ValueError, TypeError, InvalidOperation):
                continue
            if row_state is None or candidate > row_state:
                row_state = candidate
    except Exception as err:
        _LOGGER.error("Error reading recovery statistics for %s: %s", meter.entity_id, err)
        return

    if row_state is None:
        return

    if restored is None or row_state > restored:
        _LOGGER.warning(
            "%s: restored value %s is behind the statistics row for the current "
            "cycle: adopting %s (restore snapshot was older than the last upsert)",
            meter.entity_id,
            restored,
            row_state,
        )
        meter._attr_native_value = row_state
