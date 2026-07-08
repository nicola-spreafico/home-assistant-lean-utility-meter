"""Repairs (issue registry) checks for Lean Utility Meter.

One module per repair:
- recorder_exclusion: warn when the meter entity is not excluded from recorder
- points_overage: warn when long-term points exceed the expected cycle points
"""

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.core import HomeAssistant


def is_entity_recorded_by_recorder(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True if the entity is currently included by recorder filters."""
    try:
        recorder_instance = get_recorder_instance(hass)
        if recorder_instance and recorder_instance.entity_filter:
            return recorder_instance.entity_filter(entity_id)
        return True
    except (KeyError, AttributeError):
        return True
