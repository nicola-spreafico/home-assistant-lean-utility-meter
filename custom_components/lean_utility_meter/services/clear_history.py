"""Service: permanently delete all historical statistics for the entity."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.db_schema import (
    States,
    StatesMeta,
    Statistics,
    StatisticsMeta,
    StatisticsShortTerm,
)
from homeassistant.components.recorder.util import session_scope
from homeassistant.core import ServiceResponse

if TYPE_CHECKING:
    from ..entity import LeanUtilityMeterSensor

_LOGGER = logging.getLogger(__name__)


async def async_clear_history(meter: LeanUtilityMeterSensor, **kwargs: Any) -> ServiceResponse:
    """Permanently delete all historical statistics for this entity."""
    confirm = kwargs.get("confirm_deletion")
    if confirm != "DELETE":
        _LOGGER.error("History clear for %s aborted: confirmation string mismatch (received '%s', expected 'DELETE')", meter.entity_id, confirm)
        return {"status": "error", "message": "Confirmation string mismatch"}

    statistic_id = meter.entity_id
    _LOGGER.warning("Clearing all statistics for %s", statistic_id)

    def _delete_db_rows() -> dict[str, Any]:
        with session_scope(hass=meter.hass) as session:
            meta = session.query(StatisticsMeta).filter_by(statistic_id=statistic_id).first()
            if not meta:
                _LOGGER.info("No statistics metadata found for %s, nothing to clear", statistic_id)
                return {"status": "no_data", "message": "No statistics metadata found"}

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

            # Delete all long-term and short-term statistics rows
            deleted_long_term = session.query(Statistics).filter(Statistics.metadata_id == metadata_id).delete(synchronize_session=False)
            deleted_short = session.query(StatisticsShortTerm).filter(StatisticsShortTerm.metadata_id == metadata_id).delete(synchronize_session=False)

            if states_meta is None:
                states_deleted = 0
            else:
                # States.old_state_id is a self-referencing FK linking each row to
                # its predecessor. A bulk DELETE checks that constraint per-row in
                # unspecified order, so it can fail (and roll back the whole
                # transaction) if an older row is deleted before the newer row
                # that still points to it. Break the chain first.
                session.query(States).filter(
                    States.metadata_id == states_meta.metadata_id
                ).update({States.old_state_id: None}, synchronize_session=False)
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
                "Successfully cleared %s: deleted %d long-term, %d short-term, %d states rows",
                statistic_id,
                deleted_long_term,
                deleted_short,
                states_deleted,
            )
            return {
                "status": "success",
                "long_term_before": long_term_before,
                "long_term_deleted": deleted_long_term,
                "long_term_after": long_term_after,
                "short_term_before": short_term_before,
                "short_term_deleted": deleted_short,
                "short_term_after": short_term_after,
                "states_before": states_before,
                "states_deleted": states_deleted,
                "states_after": states_after,
            }

    return await get_recorder_instance(meter.hass).async_add_executor_job(_delete_db_rows)
