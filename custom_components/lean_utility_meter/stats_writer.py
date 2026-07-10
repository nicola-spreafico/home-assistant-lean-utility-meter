"""Write live/final consolidated statistics for Lean Utility Meter sensors.

Core loop of the integration: capture the current cycle value and upsert it as
a single statistics row per cycle (the "lean" part).
"""

from __future__ import annotations

import logging
from datetime import timedelta
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

from .period import get_period_start
from .util import parse_stat_start, resolve_unit, stat_field

if TYPE_CHECKING:
    from .entity import LeanUtilityMeterSensor

_LOGGER = logging.getLogger(__name__)


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
    period_start = get_period_start(now, meter._cycle or "monthly")

    # We always use the period start as the timestamp to ensure we update
    # the same statistics row (upsert) for the entire cycle, achieving 1 point per cycle.
    stat_timestamp = period_start

    # Absolute-value sources can reset to 0 exactly at cycle rollover.
    # If the reset snapshot runs in the first minutes of the new cycle,
    # preserve the latest known pre-rollover value instead of persisting 0.
    if is_final and meter._absolute_values:
        elapsed_from_period_start = (now - period_start).total_seconds()
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
        start_search = get_period_start(
            period_start - timedelta(hours=1), meter._cycle or "monthly"
        )
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
