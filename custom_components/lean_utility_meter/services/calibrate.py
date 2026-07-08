"""Service: calibrate the utility meter to a given value."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..entity import LeanUtilityMeterSensor

_LOGGER = logging.getLogger(__name__)


async def async_calibrate(meter: LeanUtilityMeterSensor, **kwargs: Any) -> None:
    """Calibrate the utility meter."""
    value = kwargs.get("value")
    if value is None:
        return

    try:
        _LOGGER.info("Calibrating %s to %s", meter.entity_id, value)
        new_val = Decimal(str(value))

        # Update internal state tracking
        meter._state = new_val
        if hasattr(meter, "_attr_native_value"):
            meter._attr_native_value = new_val

        # If this is a non-delta sensor (like total energy), we must sync _last_sensor
        # with the current source value so the next delta calculation starts from here.
        if not getattr(meter, "_delta_values", False):
            source_state = meter.hass.states.get(meter._source_entity)
            if source_state and source_state.state not in (None, "unknown", "unavailable"):
                try:
                    # UtilityMeterSensor uses _last_sensor to track the previous source value
                    meter._last_sensor = Decimal(str(source_state.state))
                    _LOGGER.debug("%s: Synchronized _last_sensor to %s", meter.entity_id, meter._last_sensor)
                except Exception as err:
                    _LOGGER.warning("%s: Failed to synchronize _last_sensor: %s", meter.entity_id, err)

        meter.async_write_ha_state()
    except Exception as err:
        _LOGGER.error("Error calibrating %s: %s", meter.entity_id, err)
        raise
