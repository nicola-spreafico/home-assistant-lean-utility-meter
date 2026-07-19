"""Service: import consolidated history from a source entity (legacy migration)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import ServiceResponse
from homeassistant.util import dt as dt_util

from ..period import get_period_start, normalize_cycle
from ..util import consolidate_rows_by_period, parse_stat_start, resolve_unit, stat_field

if TYPE_CHECKING:
    from ..entity import LeanUtilityMeterSensor

_LOGGER = logging.getLogger(__name__)


async def async_import_history(meter: LeanUtilityMeterSensor, source_entity: str) -> ServiceResponse:
    """Perform legacy import from a source entity into this lean utility meter."""
    if not source_entity:
        return {"status": "error", "message": "source_entity is required"}

    statistic_id = meter.entity_id
    source_id = "recorder"

    now = dt_util.utcnow()
    current_period_start = get_period_start(now, normalize_cycle(meter._cycle))

    try:
        # Check if we have statistics prior to the current period
        last_stats = await get_recorder_instance(meter.hass).async_add_executor_job(
            get_last_statistics, meter.hass, 5, statistic_id, False, {"sum"}
        )

        if statistic_id in last_stats and last_stats[statistic_id]:
            for stat in last_stats[statistic_id]:
                s_start = parse_stat_start(stat_field(stat, "start"))

                # If we find ANY point before today, we block the import to avoid corruption
                if s_start and s_start < current_period_start:
                    _LOGGER.warning("Statistics for past cycles already exist for %s, skipping import. Use clear_history if you want to start over.", statistic_id)
                    return {
                        "status": "blocked",
                        "message": "Past-cycle statistics already exist. Use clear_history to start over.",
                    }
    except Exception as err:
        _LOGGER.error("Failed to query existing statistics for import check: %s", err)
        return {"status": "error", "message": f"Import pre-check failed: {err}"}

    _LOGGER.info("Starting history import from %s to %s (excluding current cycle)", source_entity, statistic_id)

    start_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    try:
        legacy_stats = await get_recorder_instance(meter.hass).async_add_executor_job(
            statistics_during_period,
            meter.hass,
            start_time,
            current_period_start, # End search at current period start
            [source_entity],
            "hour",
            None,
            {"sum", "state"}
        )
    except Exception as err:
        _LOGGER.error("Failed to retrieve statistics from source %s: %s", source_entity, err)
        return {"status": "error", "message": f"Failed to read source statistics: {err}"}

    if source_entity not in legacy_stats or not legacy_stats[source_entity]:
        _LOGGER.warning("No past statistics found for source entity %s", source_entity)
        return {"status": "no_data", "imported_points": 0}

    rows = legacy_stats[source_entity]
    valid_rows = []
    rejected_rows = 0
    for r in rows:
        r_start = stat_field(r, "start")
        r_state = stat_field(r, "state")
        r_sum = stat_field(r, "sum")

        # Validate all required fields are present
        if r_start is None or r_state is None or r_sum is None:
            rejected_rows += 1
            if r_start is None:
                _LOGGER.warning("Rejected row from %s: missing start timestamp (state=%s)", source_entity, r_state)
            continue

        r_start = parse_stat_start(r_start)

        # Double check: only import points strictly before current cycle
        if r_start and r_start < current_period_start:
            valid_rows.append({
                "start": r_start,
                "state": float(r_state),
                "sum": float(r_sum)
                })

    if not valid_rows:
        _LOGGER.warning("No valid legacy rows found for %s", source_entity)
        return {"status": "no_valid_data", "imported_points": 0}

    consolidated_rows = consolidate_rows_by_period(valid_rows, normalize_cycle(meter._cycle))

    statistics_data = []
    for r in consolidated_rows:
        statistics_data.append(StatisticData(
            start=r["start"],
            state=r["state"],
            sum=r["sum"]
        ))

    unit = resolve_unit(meter)

    # Leave unit_class empty to allow HA to handle it or user to customize via YAML
    unit_class = None

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        unit_class=unit_class,
        name=meter.name or meter.entity_id,
        source=source_id,
        statistic_id=statistic_id,
        unit_of_measurement=unit,
    )

    try:
        async_import_statistics(meter.hass, metadata, statistics_data)
        _LOGGER.info(
            "Successfully imported %s consolidated points from %s into %s (rejected %s corrupted rows)",
            len(statistics_data),
            source_entity,
            statistic_id,
            rejected_rows
        )
        return {
            "status": "success",
            "imported_points": len(statistics_data),
            "rejected_rows": rejected_rows,
            "source_entity": source_entity,
            "target_entity": statistic_id,
        }
    except Exception as err:
        _LOGGER.error("Failed to inject statistics for legacy import: %s", err)
        return {"status": "error", "message": f"Failed to inject statistics: {err}"}
