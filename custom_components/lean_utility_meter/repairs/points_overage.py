"""Repair: warn when long-term statistics points exceed the expected cycle points.

A lean meter should persist exactly one long-term point per cycle; more than
that (plus tolerance) means something else is writing statistics for the entity.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.db_schema import Statistics, StatisticsMeta
from homeassistant.components.recorder.util import session_scope
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..period import count_expected_points_from_first_start
from . import is_entity_recorded_by_recorder

if TYPE_CHECKING:
    from ..sensor import LeanUtilityMeterSensor

_LOGGER = logging.getLogger(__name__)

POINTS_OVERAGE_TOLERANCE = 1
POINTS_OVERAGE_CHECK_INTERVAL = timedelta(hours=6)


async def async_check_points_overage(meter: LeanUtilityMeterSensor) -> None:
    """Create/Delete a Repair when long-term points exceed expected cycle points."""
    await asyncio.sleep(20)

    # If recorder includes this entity, point overage is expected noise.
    # In this case we only keep the recorder_not_excluded warning.
    if is_entity_recorded_by_recorder(meter.hass, meter.entity_id):
        ir.async_delete_issue(
            meter.hass,
            domain=DOMAIN,
            issue_id=f"unexpected_points_{meter.entity_id}",
        )
        return

    def _load_stats_counts() -> dict[str, Any]:
        with session_scope(hass=meter.hass) as session:
            meta = session.query(StatisticsMeta).filter_by(statistic_id=meter.entity_id).first()
            if not meta:
                return {"status": "no_data"}

            first_start_ts = session.query(Statistics.start_ts).filter(
                Statistics.metadata_id == meta.id
            ).order_by(Statistics.start_ts.asc()).limit(1).scalar()

            if first_start_ts is None:
                return {"status": "no_data"}

            long_term_points = session.query(Statistics).filter(
                Statistics.metadata_id == meta.id
            ).count()

            return {
                "status": "ok",
                "first_start": datetime.fromtimestamp(first_start_ts, tz=timezone.utc),
                "actual_points": long_term_points,
            }

    try:
        payload = await get_recorder_instance(meter.hass).async_add_executor_job(_load_stats_counts)
    except Exception as err:
        _LOGGER.warning("%s: points overage check failed: %s", meter.entity_id, err)
        return

    issue_id = f"unexpected_points_{meter.entity_id}"
    if payload.get("status") != "ok":
        ir.async_delete_issue(meter.hass, domain=DOMAIN, issue_id=issue_id)
        return

    cycle = meter._cycle or "daily"
    first_start = payload["first_start"]
    actual_points = int(payload["actual_points"])
    expected_points = count_expected_points_from_first_start(
        first_start,
        dt_util.utcnow(),
        cycle,
    )

    if actual_points > expected_points + POINTS_OVERAGE_TOLERANCE:
        ir.async_create_issue(
            meter.hass,
            domain=DOMAIN,
            issue_id=issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="unexpected_points_for_cycle",
            translation_placeholders={
                "entity_id": meter.entity_id,
                "cycle": cycle,
                "actual_points": str(actual_points),
                "expected_points": str(expected_points),
                "tolerance": str(POINTS_OVERAGE_TOLERANCE),
                "first_point": dt_util.as_local(first_start).isoformat(timespec="seconds"),
            },
        )
        return

    ir.async_delete_issue(meter.hass, domain=DOMAIN, issue_id=issue_id)
