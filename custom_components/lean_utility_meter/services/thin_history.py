"""Service: consolidate duplicate statistics points (retroactive cleanup)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.db_schema import (
    States,
    StatesMeta,
    Statistics,
    StatisticsMeta,
    StatisticsShortTerm,
)
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.components.recorder.util import session_scope
from homeassistant.core import ServiceResponse

from ..period import normalize_cycle
from ..util import consolidate_rows_by_period, parse_stat_start, stat_field

if TYPE_CHECKING:
    from ..entity import LeanUtilityMeterSensor

_LOGGER = logging.getLogger(__name__)


async def async_thin_history(meter: LeanUtilityMeterSensor, **kwargs: Any) -> ServiceResponse:
    """Perform retroactive history thinning to consolidate duplicate points."""
    statistic_id = meter.entity_id
    cycle = normalize_cycle(meter._cycle)
    _LOGGER.info("Thinning history for %s with cycle %s", statistic_id, cycle)
    result = await _async_thin_statistic_id(meter, statistic_id, cycle)
    return result


async def _async_thin_statistic_id(meter: LeanUtilityMeterSensor, statistic_id: str, cycle: str) -> dict[str, Any]:
    """Perform thinning on this meter's statistic ID."""
    _LOGGER.info("Thinning statistic ID: %s with cycle %s", statistic_id, cycle)

    start_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    try:
        stats = await get_recorder_instance(meter.hass).async_add_executor_job(
            statistics_during_period,
            meter.hass,
            start_time,
            None,
            [statistic_id],
            "hour",
            None,
            {"sum", "state"}
        )
    except Exception as err:
        _LOGGER.error("Failed to query statistics for %s: %s", statistic_id, err)
        return {"error": str(err)}

    if statistic_id not in stats or not stats[statistic_id]:
        _LOGGER.info("No statistics found for %s to thin", statistic_id)
        return {"status": "no_data", "message": "No statistics found for the target entity"}

    rows = stats[statistic_id]
    total_found = len(rows)
    valid_rows = []
    for r in rows:
        r_start = stat_field(r, "start")
        r_state = stat_field(r, "state")
        r_sum = stat_field(r, "sum")

        if r_start is not None and r_state is not None and r_sum is not None:
            r_start = parse_stat_start(r_start)

            valid_rows.append({
                "start": r_start,
                "state": float(r_state),
                "sum": float(r_sum)
            })

    if not valid_rows:
        _LOGGER.info("No valid rows found for %s", statistic_id)
        return {
            "status": "no_valid_data",
            "total_before": total_found,
            "rows_kept": 0,
            "long_term_before": 0,
            "long_term_deleted": 0,
            "long_term_after": 0,
            "short_term_before": 0,
            "short_term_deleted": 0,
            "short_term_after": 0,
            "states_before": 0,
            "states_deleted": 0,
            "states_after": 0,
        }

    consolidated_rows = consolidate_rows_by_period(valid_rows, cycle)

    def _delete_db_rows() -> dict[str, Any]:
        with session_scope(hass=meter.hass) as session:
            meta = session.query(StatisticsMeta).filter_by(statistic_id=statistic_id).first()
            if not meta:
                _LOGGER.warning("Could not find database metadata for %s", statistic_id)
                return {
                    "status": "metadata_missing",
                    "total_before": total_found,
                    "rows_kept": len(consolidated_rows),
                    "long_term_before": 0,
                    "long_term_deleted": 0,
                    "long_term_after": 0,
                    "short_term_before": 0,
                    "short_term_deleted": 0,
                    "short_term_after": 0,
                    "states_before": 0,
                    "states_deleted": 0,
                    "states_after": 0,
                }

            metadata_id = meta.id

            long_term_before = session.query(Statistics).filter(
                Statistics.metadata_id == metadata_id
            ).count()
            short_term_before = session.query(StatisticsShortTerm).filter(
                StatisticsShortTerm.metadata_id == metadata_id
            ).count()

            states_meta = session.query(StatesMeta).filter_by(entity_id=statistic_id).first()
            if states_meta is None:
                states_before = 0
            else:
                states_before = session.query(States).filter(
                    States.metadata_id == states_meta.metadata_id
                ).count()

            expected_long_term_after = len(consolidated_rows)
            rebuild_required = long_term_before > expected_long_term_after

            # Deterministic rebuild only when thinning is actually needed.
            # When not needed, keep long-term rows untouched.
            if rebuild_required:
                deleted_long_term_raw = session.query(Statistics).filter(
                    Statistics.metadata_id == metadata_id
                ).delete(synchronize_session=False)
            else:
                deleted_long_term_raw = 0

            # Delete all 5-minute stats for this metadata_id
            deleted_short = session.query(StatisticsShortTerm).filter(
                StatisticsShortTerm.metadata_id == metadata_id
            ).delete(synchronize_session=False)

            if states_meta is None:
                states_deleted = 0
            else:
                # states rows reference their predecessor via the self-referencing
                # old_state_id FK. Clear it before the bulk delete: backends that
                # enforce the FK row-by-row (e.g. MySQL/InnoDB) reject the delete
                # otherwise. Same approach as recorder's purge; no-op elsewhere.
                session.query(States).filter(
                    States.metadata_id == states_meta.metadata_id
                ).update({"old_state_id": None}, synchronize_session=False)
                states_deleted = session.query(States).filter(
                    States.metadata_id == states_meta.metadata_id
                ).delete(synchronize_session=False)

            long_term_after = session.query(Statistics).filter(
                Statistics.metadata_id == metadata_id
            ).count()
            short_term_after = session.query(StatisticsShortTerm).filter(
                StatisticsShortTerm.metadata_id == metadata_id
            ).count()

            if states_meta is None:
                states_after = 0
            else:
                states_after = session.query(States).filter(
                    States.metadata_id == states_meta.metadata_id
                ).count()

            _LOGGER.info(
                "Prepared thinning for %s: consolidated=%d, rebuild_required=%s, deleted %d long-term(raw), %d short-term, %d states rows",
                statistic_id,
                len(consolidated_rows),
                rebuild_required,
                deleted_long_term_raw,
                deleted_short,
                states_deleted,
            )
            return {
                "status": "prepared",
                "rebuild_required": rebuild_required,
                "total_before": total_found,
                "rows_kept": len(consolidated_rows),
                "long_term_before": long_term_before,
                "long_term_deleted_raw": deleted_long_term_raw,
                "long_term_expected_after": expected_long_term_after,
                "long_term_after": long_term_after,
                "short_term_before": short_term_before,
                "short_term_deleted": deleted_short,
                "short_term_after": short_term_after,
                "states_before": states_before,
                "states_deleted": states_deleted,
                "states_after": states_after,
            }

    delete_result = await get_recorder_instance(meter.hass).async_add_executor_job(_delete_db_rows)

    if delete_result.get("status") != "prepared":
        return delete_result

    rebuild_required = delete_result.get("rebuild_required", False)

    state = meter.hass.states.get(statistic_id)
    unit = None
    name = statistic_id
    if state is not None:
        unit = state.attributes.get("unit_of_measurement")
        name = state.attributes.get("friendly_name") or statistic_id

    if rebuild_required:
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            unit_class=None,
            name=name,
            source="recorder",
            statistic_id=statistic_id,
            unit_of_measurement=unit,
        )
        statistics_data = [
            StatisticData(start=r["start"], state=r["state"], sum=r["sum"])
            for r in consolidated_rows
        ]
        async_import_statistics(meter.hass, metadata, statistics_data)

        # async_import_statistics is asynchronous: give recorder time to persist
        # before reporting final counts.
        await asyncio.sleep(1)

    def _count_long_term_after_rebuild() -> int:
        with session_scope(hass=meter.hass) as session:
            meta = session.query(StatisticsMeta).filter_by(statistic_id=statistic_id).first()
            if not meta:
                return 0
            return session.query(Statistics).filter(Statistics.metadata_id == meta.id).count()

    long_term_after_db = await get_recorder_instance(meter.hass).async_add_executor_job(_count_long_term_after_rebuild)
    expected_long_term_after = delete_result["long_term_expected_after"]
    long_term_before = delete_result["long_term_before"]

    if rebuild_required:
        # Prefer DB-confirmed value when available; otherwise expected value.
        long_term_after = long_term_after_db if long_term_after_db > 0 else expected_long_term_after
    else:
        long_term_after = long_term_before

    long_term_deleted = max(0, long_term_before - long_term_after)

    return {
        "status": "success",
        "total_before": total_found,
        "rows_kept": len(consolidated_rows),
        "long_term_before": long_term_before,
        "long_term_deleted": long_term_deleted,
        "long_term_after": long_term_after,
        "short_term_before": delete_result["short_term_before"],
        "short_term_deleted": delete_result["short_term_deleted"],
        "short_term_after": delete_result["short_term_after"],
        "states_before": delete_result["states_before"],
        "states_deleted": delete_result["states_deleted"],
        "states_after": delete_result["states_after"],
    }
