"""Repair: warn when the meter entity is not excluded from recorder.

Recording the meter entity itself defeats the purpose of the integration
(the recorder would store frequent state rows alongside the lean statistics).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.helpers import issue_registry as ir

from ..const import DOMAIN
from . import is_entity_recorded_by_recorder

if TYPE_CHECKING:
    from ..sensor import LeanUtilityMeterSensor


async def async_check_recorder_exclusion(meter: LeanUtilityMeterSensor) -> None:
    """Check if the entity is excluded from recorder, and create Repairs issue if not."""
    await asyncio.sleep(15)
    is_recorded = is_entity_recorded_by_recorder(meter.hass, meter.entity_id)

    if is_recorded:
        ir.async_create_issue(
            meter.hass,
            domain=DOMAIN,
            issue_id=f"recorder_not_excluded_{meter.entity_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="recorder_not_excluded",
            translation_placeholders={"entity_id": meter.entity_id},
        )
    else:
        ir.async_delete_issue(meter.hass, domain=DOMAIN, issue_id=f"recorder_not_excluded_{meter.entity_id}")
