"""The Lean Utility Meter sensor entity.

Core measurement logic:
- __init__(): Instantiate sensor with meter config
- async_added_to_hass(): Recover state from recorder statistics (crash/missed-reset
  resilience), register callbacks, start periodic updates, schedule repair checks
- async_reading(): Fetch source entity state and update lean meter with delta/state
- _async_on_state_change(): Callback when source entity changes, trigger stats write
- _async_delayed_absolute_sync(): Sync absolute values after initial delay (for absolute_values=True)
- async_reset_meter(): Write final stats, then reset via the core class
- Service methods are thin delegates to the services/ modules (registered by name in sensor.py)
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.utility_meter.sensor import UtilityMeterSensor
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, ServiceResponse, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.typing import UNDEFINED, UndefinedType
from homeassistant.util import dt as dt_util

from . import stats_writer
from .repairs import points_overage, recorder_exclusion
from .services import calibrate, clear_history, import_history, thin_history

_LOGGER = logging.getLogger(__name__)


class LeanUtilityMeterSensor(UtilityMeterSensor):
    """Representation of a Lean Utility Meter sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        source_entity: str,
        name: str,
        unique_id: str | None,
        meter_type: str | None,
        meter_offset: timedelta,
        cron_pattern: str | None,
        delta_values: bool,
        net_consumption: bool,
        sensor_always_available: bool,
        periodically_resetting: bool,
        absolute_values: bool,
        tariff: str | None,
        tariff_entity: str | None,
        parent_meter: str,
        live_update_interval: timedelta = timedelta(minutes=5),
        entity_id: str | None = None,
        force_unit_of_measurement: str | None | UndefinedType = UNDEFINED,
        force_device_class: SensorDeviceClass | None | UndefinedType = UNDEFINED,
        force_state_class: SensorStateClass | None | UndefinedType = UNDEFINED,
    ) -> None:
        """Initialize the Lean Utility Meter sensor.

        The trailing keyword parameters exist for meters created by *other*
        integrations via discovery (see sensor.py): `entity_id` pins the entity id
        so it matches a pre-existing LTS series, and the `force_*` presentation
        overrides replace the values the core meter would adopt from the source
        entity. `UNDEFINED` means "inherit"; an explicit `None` forces "no value".
        """
        super().__init__(
            hass=hass,
            source_entity=source_entity,
            name=name,
            unique_id=unique_id,
            meter_type=meter_type,
            meter_offset=meter_offset,
            cron_pattern=cron_pattern,
            delta_values=delta_values,
            sensor_always_available=sensor_always_available,
            periodically_resetting=periodically_resetting,
            tariff=tariff,
            tariff_entity=tariff_entity,
            parent_meter=parent_meter,
            net_consumption=net_consumption,
        )
        self._cycle = meter_type
        self._delta_values = delta_values
        self._source_entity = source_entity
        self._live_update_interval = live_update_interval
        self._absolute_values = absolute_values
        self._last_stats_update = None
        self._previous_valid_state = None
        if entity_id is not None:
            self.entity_id = entity_id
        self._force_unit_of_measurement = force_unit_of_measurement
        self._force_device_class = force_device_class
        self._force_state_class = force_state_class

    # Presentation: forced value when the creator provided one, otherwise the
    # inherited behavior (core utility_meter adopts these from the source entity).
    @property
    def native_unit_of_measurement(self) -> str | None:
        if self._force_unit_of_measurement is not UNDEFINED:
            return self._force_unit_of_measurement
        return super().native_unit_of_measurement

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if self._force_device_class is not UNDEFINED:
            return self._force_device_class
        return super().device_class

    @property
    def state_class(self) -> SensorStateClass | None:
        if self._force_state_class is not UNDEFINED:
            return self._force_state_class
        return super().state_class

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        # Reconcile the value core just restored from core.restore_state with
        # the recorder DB, which survives crashes the restore file does not:
        # recovers a rollover reset lost while HA was down and re-adopts the
        # fresher 5-minute upsert of the running cycle. Must run before any
        # source event is processed and before the first stats write.
        await stats_writer.async_recover_after_restart(self)

        # Check if entity is excluded from recorder and raise issue if not.
        self.hass.async_create_task(recorder_exclusion.async_check_recorder_exclusion(self))
        self.hass.async_create_task(points_overage.async_check_points_overage(self))

        # Re-check periodically to keep Repairs in sync with historical drift.
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_handle_periodic_points_overage_check,
                points_overage.POINTS_OVERAGE_CHECK_INTERVAL,
            )
        )

        # Start periodic stats updates to show live growth.
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.entity_id], self._async_on_state_change
            )
        )

        # Seed the meter from the current source so it becomes available even if
        # the source does not emit a fresh event after startup.
        current_value = self.native_value
        source_state = self.hass.states.get(self._source_entity)
        if source_state is not None and source_state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            if self._absolute_values or current_value in (None, STATE_UNAVAILABLE, STATE_UNKNOWN):
                self.start(source_state.attributes or {})
                self._attr_available = True
                try:
                    parsed_state = Decimal(str(source_state.state))
                    self._previous_valid_state = self._last_valid_state
                    self._last_valid_state = parsed_state
                    if self._absolute_values:
                        self._attr_native_value = parsed_state
                except Exception:
                    self._last_valid_state = None
                self.async_write_ha_state()

        if self._absolute_values:
            self.hass.async_create_task(self._async_delayed_absolute_sync())

    async def _async_delayed_absolute_sync(self) -> None:
        """Retry absolute-value sync shortly after startup to avoid ordering issues."""
        await asyncio.sleep(10)
        source_state = self.hass.states.get(self._source_entity)
        if source_state is None or source_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        parsed_state = self._validate_state(source_state)
        if parsed_state is None:
            return

        self._attr_available = True
        self._attr_native_value = parsed_state
        self._input_device_class = source_state.attributes.get("device_class")
        self._attr_native_unit_of_measurement = source_state.attributes.get(
            "unit_of_measurement"
        )
        self._last_valid_state = parsed_state
        self.async_write_ha_state()

    @callback
    def _async_on_state_change(self, event: Event) -> None:
        """Handle state change to trigger periodic stats update."""
        now = dt_util.utcnow()
        if self._last_stats_update is not None and now - self._last_stats_update < self._live_update_interval:
            return

        self._last_stats_update = now
        self.hass.async_create_task(self._async_capture_and_write_stats(is_final=False))

    async def async_reset_meter(self, entity_id: str) -> None:
        """Reset the utility meter status."""
        # 1. Capture and write stats BEFORE resetting the meter
        if self._tariff_entity is None or self._tariff_entity == entity_id:
            try:
                await self._async_capture_and_write_stats(is_final=True)
            except Exception as err:
                _LOGGER.error("Error capturing and writing statistics before reset: %s", err)

        # 2. Call the base class to complete the reset
        await super().async_reset_meter(entity_id)

    @callback
    def async_reading(self, event: Event) -> None:
        """Handle source sensor state changes."""
        if self._absolute_values:
            source_state = self.hass.states.get(self._source_entity)
            if source_state is None or source_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                if not self._sensor_always_available:
                    self._attr_available = False
                    self.async_write_ha_state()
                return

            self._attr_available = True

            new_state = event.data.get("new_state")
            if new_state is None:
                return

            new_state_attributes = new_state.attributes or {}
            parsed_state = self._validate_state(new_state)
            if parsed_state is None:
                return

            self._attr_native_value = parsed_state
            self._input_device_class = new_state_attributes.get("device_class")
            self._attr_native_unit_of_measurement = new_state_attributes.get(
                "unit_of_measurement"
            )
            self._previous_valid_state = self._last_valid_state
            self._last_valid_state = parsed_state
            self.async_write_ha_state()
            return

        # Override to avoid KeyErrors in core UtilityMeterSensor when parent_meter is not in DATA_UTILITY
        try:
            super().async_reading(event)
        except KeyError as err:
            if str(err).strip("'") == self._parent_meter:
                source_state = self.hass.states.get(self._source_entity)
                if source_state is None or source_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    if not self._sensor_always_available:
                        self._attr_available = False
                        self.async_write_ha_state()
                    return

                self._attr_available = True

                old_state = event.data.get("old_state")
                new_state = event.data.get("new_state")
                if new_state is None:
                    return

                new_state_attributes = new_state.attributes or {}
                if self.native_value is None:
                    self.start(new_state_attributes)

                if (
                    adjustment := self.calculate_adjustment(old_state, new_state)
                ) is not None and (self._sensor_net_consumption or adjustment >= 0):
                    if self._attr_native_value is None:
                        self._attr_native_value = Decimal(0)
                    self._attr_native_value += adjustment

                self._input_device_class = new_state_attributes.get("device_class")
                self._attr_native_unit_of_measurement = new_state_attributes.get(
                    "unit_of_measurement"
                )
                self._previous_valid_state = self._last_valid_state
                self._last_valid_state = self._validate_state(new_state)
                self.async_write_ha_state()
            else:
                raise

    async def _async_capture_and_write_stats(self, is_final: bool = False) -> None:
        """Capture the current cycle value and write it as external statistic."""
        await stats_writer.async_capture_and_write_stats(self, is_final=is_final)

    @callback
    def _async_handle_periodic_points_overage_check(self, _now: datetime) -> None:
        """Schedule periodic overage check."""
        self.hass.async_create_task(points_overage.async_check_points_overage(self))

    # --- Entity services (thin delegates to the services/ modules) ---

    async def async_calibrate(self, **kwargs: Any) -> None:
        """Calibrate the utility meter."""
        await calibrate.async_calibrate(self, **kwargs)

    async def async_import_history(self, source_entity: str) -> ServiceResponse:
        """Perform legacy import from a source entity into this lean utility meter."""
        return await import_history.async_import_history(self, source_entity)

    async def async_thin_history(self, **kwargs: Any) -> ServiceResponse:
        """Perform retroactive history thinning to consolidate duplicate points."""
        return await thin_history.async_thin_history(self, **kwargs)

    async def async_clear_history(self, **kwargs: Any) -> ServiceResponse:
        """Permanently delete all historical statistics for this entity."""
        return await clear_history.async_clear_history(self, **kwargs)
