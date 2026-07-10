"""Repairs (issue registry) checks for Lean Utility Meter.

One module per repair:
- recorder_exclusion: warn when the meter entity is not excluded from recorder
- points_overage: warn when long-term points exceed the expected cycle points

This package is also the integration's `repairs` platform: `async_create_fix_flow`
is invoked by Home Assistant when the user clicks a fixable issue.
"""

from __future__ import annotations

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str] | None,
) -> RepairsFlow:
    """Create a fix flow for a fixable issue."""
    if issue_id.startswith("unexpected_points_") and data:
        from .points_overage import PointsOverageRepairFlow

        return PointsOverageRepairFlow(issue_id, data)
    return ConfirmRepairFlow()


def is_entity_recorded_by_recorder(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True if the entity is currently included by recorder filters."""
    try:
        recorder_instance = get_recorder_instance(hass)
        if recorder_instance and recorder_instance.entity_filter:
            return recorder_instance.entity_filter(entity_id)
        return True
    except (KeyError, AttributeError):
        return True
