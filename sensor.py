"""Platform for Lean Utility Meter sensors.

STRUCTURE:
-----------

1. PLATFORM SETUP
   - async_setup_platform(): Initialize platform, register entity services (calibrate, import_history, thin_history, clear_history)

2. LEANUTILITIMETERSENSOR CLASS
   
   a) Initialization & Lifecycle
      - __init__(): Instantiate sensor with meter config
      - async_added_to_hass(): Register callbacks, start periodic updates, check recorder exclusion
   
   b) Core Measurement Logic
      - async_reading(): Fetch source entity state and update lean meter with delta/state
      - _async_on_state_change(): Callback when source entity changes, trigger async_reading
      - _async_delayed_absolute_sync(): Sync absolute values after initial delay (for delta_values=True)
      - _async_capture_and_write_stats(): Core loop - consolidate stats from current period and write to DB
   
   c) Utilities & Validators
      - async_reset_meter(): Reset meter by clearing current state and reloading from recorder
      - _async_check_recorder_exclusion(): Verify entity is excluded from recorder (performance check)
   
   d) Services (User-facing history operations)
      - async_calibrate(): Set manual calibration offset
      - async_import_history(): Import consolidated history from a source entity (legacy migration)
      - async_thin_history(): Consolidate duplicate points in history (retroactive cleanup)
      - async_clear_history(): Permanently delete all statistics
   
   e) Internal Consolidation
      - _async_thin_statistic_id(): Implementation of consolidation logic (groups by cycle, keeps most recent)

3. HELPER FUNCTIONS (Module-level utilities)
   - get_period_key(): Compute grouping key (year/month/day) for a datetime given a cycle type
   - get_period_start(): Compute cycle-start boundary for a datetime given a cycle type
"""

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import logging
from typing import Any

from homeassistant.components.utility_meter.sensor import UtilityMeterSensor
from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.util import session_scope
from homeassistant.components.recorder.db_schema import (
    Statistics,
    StatisticsShortTerm,
    StatisticsMeta,
    States,
    StatesMeta,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback, Event, ServiceResponse
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util
import voluptuous as vol
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.core import SupportsResponse

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

POINTS_OVERAGE_TOLERANCE = 1
POINTS_OVERAGE_CHECK_INTERVAL = timedelta(hours=6)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Lean Utility Meter sensors."""
    meters = hass.data.get(DOMAIN, {})
    
    entities = []
    
    for meter_slug, meter_conf in meters.items():
        source = meter_conf["source"]
        name = meter_conf.get("name", meter_slug.replace("_", " ").title())
        unique_id = meter_conf.get("unique_id")
        cycle = meter_conf.get("cycle")
        offset = meter_conf.get("offset", timedelta(0))
        cron = meter_conf.get("cron")
        delta_values = meter_conf.get("delta_values", False)
        net_consumption = meter_conf.get("net_consumption", False)
        always_available = meter_conf.get("always_available", False)
        periodically_resetting = meter_conf.get("periodically_resetting", True)
        absolute_values = meter_conf.get("absolute_values", False)
        tariffs = meter_conf.get("tariffs", [])
        
        live_update_interval = meter_conf.get("live_update_interval", timedelta(minutes=5))
        
        if tariffs:
            tariff_entity = f"select.{meter_slug}"
            for tariff in tariffs:
                tariff_unique_id = f"{unique_id}_{tariff}" if unique_id else None
                tariff_name = f"{name} {tariff}"
                
                entities.append(
                    LeanUtilityMeterSensor(
                        hass=hass,
                        source_entity=source,
                        name=tariff_name,
                        unique_id=tariff_unique_id,
                        meter_type=cycle,
                        meter_offset=offset,
                        cron_pattern=cron,
                        delta_values=delta_values,
                        net_consumption=net_consumption,
                        sensor_always_available=always_available,
                        periodically_resetting=periodically_resetting,
                        absolute_values=absolute_values,
                        tariff=tariff,
                        tariff_entity=tariff_entity,
                        parent_meter=meter_slug,
                        live_update_interval=live_update_interval,
                    )
                )
        else:
            entities.append(
                LeanUtilityMeterSensor(
                    hass=hass,
                    source_entity=source,
                    name=name,
                    unique_id=unique_id,
                    meter_type=cycle,
                    meter_offset=offset,
                    cron_pattern=cron,
                    delta_values=delta_values,
                    net_consumption=net_consumption,
                    sensor_always_available=always_available,
                    periodically_resetting=periodically_resetting,
                    absolute_values=absolute_values,
                    tariff=None,
                    tariff_entity=None,
                    parent_meter=meter_slug,
                    live_update_interval=live_update_interval,
                )
            )
            
    async_add_entities(entities, True)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "calibrate",
        {vol.Required("value"): vol.Coerce(float)},
        "async_calibrate",
    )
    platform.async_register_entity_service(
        "import_history",
        {vol.Required("source_entity"): cv.entity_id},
        "async_import_history",
        supports_response=SupportsResponse.ONLY,
    )
    platform.async_register_entity_service(
        "thin_history",
        {},
        "async_thin_history",
        supports_response=SupportsResponse.ONLY,
    )
    platform.async_register_entity_service(
        "clear_history",
        {vol.Required("confirm_deletion"): cv.string},
        "async_clear_history",
        supports_response=SupportsResponse.ONLY,
    )


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
    ) -> None:
        """Initialize the Lean Utility Meter sensor."""
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
        
    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        # Check if entity is excluded from recorder and raise issue if not.
        self.hass.async_create_task(self._async_check_recorder_exclusion())
        self.hass.async_create_task(self._async_check_points_overage())

        # Re-check periodically to keep Repairs in sync with historical drift.
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_handle_periodic_points_overage_check,
                POINTS_OVERAGE_CHECK_INTERVAL,
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

    async def async_calibrate(self, **kwargs: Any) -> None:
        """Calibrate the utility meter."""
        value = kwargs.get("value")
        if value is None:
            return
            
        try:
            _LOGGER.info("Calibrating %s to %s", self.entity_id, value)
            new_val = Decimal(str(value))
            
            # Update internal state tracking
            self._state = new_val
            if hasattr(self, "_attr_native_value"):
                self._attr_native_value = new_val
            
            # If this is a non-delta sensor (like total energy), we must sync _last_sensor
            # with the current source value so the next delta calculation starts from here.
            if not getattr(self, "_delta_values", False):
                source_state = self.hass.states.get(self._source_entity)
                if source_state and source_state.state not in (None, "unknown", "unavailable"):
                    try:
                        # UtilityMeterSensor uses _last_sensor to track the previous source value
                        self._last_sensor = Decimal(str(source_state.state))
                        _LOGGER.debug("%s: Synchronized _last_sensor to %s", self.entity_id, self._last_sensor)
                    except Exception as err:
                        _LOGGER.warning("%s: Failed to synchronize _last_sensor: %s", self.entity_id, err)
            
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Error calibrating %s: %s", self.entity_id, err)
            raise

    async def _async_capture_and_write_stats(self, is_final: bool = False) -> None:
        """Capture the current cycle value and write it as external statistic."""
        _LOGGER.debug("Capturing statistics for %s (final=%s)", self.entity_id, is_final)
        
        try:
            current_val = float(self.native_value)
        except (ValueError, TypeError, AttributeError):
            current_val = 0.0
            
        statistic_id = self.entity_id
            
        now = dt_util.utcnow()
        
        # Calculate the start of the current period to ensure we only have 1 row per period
        period_start = get_period_start(now, self._cycle or "monthly")
        
        # We always use the period start as the timestamp to ensure we update
        # the same statistics row (upsert) for the entire cycle, achieving 1 point per cycle.
        stat_timestamp = period_start

        # Absolute-value sources can reset to 0 exactly at cycle rollover.
        # If the reset snapshot runs in the first minutes of the new cycle,
        # preserve the latest known pre-rollover value instead of persisting 0.
        if is_final and self._absolute_values:
            elapsed_from_period_start = (now - period_start).total_seconds()
            if elapsed_from_period_start <= 600 and current_val == 0.0:
                candidate = self._previous_valid_state
                if candidate is not None:
                    try:
                        candidate_val = float(candidate)
                        if candidate_val > 0.0:
                            current_val = candidate_val
                    except (ValueError, TypeError):
                        pass

        last_sum = 0.0
        try:
            # Switch to statistics_during_period for more reliable retrieval of external stats
            start_search = now - timedelta(days=10)
            stats_map = await get_recorder_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
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
                    s_start = stat.get("start") if isinstance(stat, dict) else getattr(stat, "start", None)
                    if isinstance(s_start, (int, float)):
                        s_start = datetime.fromtimestamp(s_start, tz=timezone.utc)
                    elif isinstance(s_start, str):
                        s_start = dt_util.parse_datetime(s_start)
                    
                    if s_start and s_start.tzinfo is None:
                        s_start = s_start.replace(tzinfo=timezone.utc)
                    
                    if s_start and s_start < stat_timestamp:
                        base_sum = stat.get("sum") if isinstance(stat, dict) else getattr(stat, "sum", 0.0)
                        found = True
                        _LOGGER.info("%s: Found base sum %s at %s", statistic_id, base_sum, s_start)
                        break
                
                last_sum = base_sum if found else 0.0
            else:
                _LOGGER.debug("%s: No statistics found in period, starting from 0", statistic_id)
        except Exception as err:
            _LOGGER.error("Error retrieving base statistics for %s: %s", statistic_id, err)
            
        new_sum = last_sum + current_val
        
        unit = self.unit_of_measurement
        device_class = self.device_class
        if unit is None or device_class is None:
            source_state = self.hass.states.get(self._source_entity)
            if source_state:
                unit = unit or source_state.attributes.get("unit_of_measurement")
                device_class = device_class or source_state.attributes.get("device_class")
        
        # Leave unit_class empty to allow HA to handle it or user to customize via YAML
        unit_class = None
        
        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            unit_class=unit_class,
            name=self.name or self.entity_id,
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
            async_import_statistics(self.hass, metadata, [stat_data])
        except Exception as err:
            _LOGGER.error("Failed to add statistics for %s: %s", statistic_id, err)

    async def _async_check_recorder_exclusion(self) -> None:
        """Check if the entity is excluded from recorder, and create Repairs issue if not."""
        await asyncio.sleep(15)
        is_recorded = self._is_entity_recorded_by_recorder()
            
        if is_recorded:
            ir.async_create_issue(
                self.hass,
                domain=DOMAIN,
                issue_id=f"recorder_not_excluded_{self.entity_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="recorder_not_excluded",
                translation_placeholders={"entity_id": self.entity_id},
            )
        else:
            ir.async_delete_issue(self.hass, domain=DOMAIN, issue_id=f"recorder_not_excluded_{self.entity_id}")

    def _is_entity_recorded_by_recorder(self) -> bool:
        """Return True if the entity is currently included by recorder filters."""
        try:
            recorder_instance = get_recorder_instance(self.hass)
            if recorder_instance and recorder_instance.entity_filter:
                return recorder_instance.entity_filter(self.entity_id)
            return True
        except (KeyError, AttributeError):
            return True

    @callback
    def _async_handle_periodic_points_overage_check(self, _now: datetime) -> None:
        """Schedule periodic overage check."""
        self.hass.async_create_task(self._async_check_points_overage())

    async def _async_check_points_overage(self) -> None:
        """Create/Delete a Repair when long-term points exceed expected cycle points."""
        await asyncio.sleep(20)

        # If recorder includes this entity, point overage is expected noise.
        # In this case we only keep the recorder_not_excluded warning.
        if self._is_entity_recorded_by_recorder():
            ir.async_delete_issue(
                self.hass,
                domain=DOMAIN,
                issue_id=f"unexpected_points_{self.entity_id}",
            )
            return

        def _load_stats_counts() -> dict[str, Any]:
            with session_scope(hass=self.hass) as session:
                meta = session.query(StatisticsMeta).filter_by(statistic_id=self.entity_id).first()
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
            payload = await get_recorder_instance(self.hass).async_add_executor_job(_load_stats_counts)
        except Exception as err:
            _LOGGER.warning("%s: points overage check failed: %s", self.entity_id, err)
            return

        issue_id = f"unexpected_points_{self.entity_id}"
        if payload.get("status") != "ok":
            ir.async_delete_issue(self.hass, domain=DOMAIN, issue_id=issue_id)
            return

        cycle = self._cycle or "daily"
        first_start = payload["first_start"]
        actual_points = int(payload["actual_points"])
        expected_points = count_expected_points_from_first_start(
            first_start,
            dt_util.utcnow(),
            cycle,
        )

        if actual_points > expected_points + POINTS_OVERAGE_TOLERANCE:
            ir.async_create_issue(
                self.hass,
                domain=DOMAIN,
                issue_id=issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="unexpected_points_for_cycle",
                translation_placeholders={
                    "entity_id": self.entity_id,
                    "cycle": cycle,
                    "actual_points": str(actual_points),
                    "expected_points": str(expected_points),
                    "tolerance": str(POINTS_OVERAGE_TOLERANCE),
                    "first_point": dt_util.as_local(first_start).isoformat(timespec="seconds"),
                },
            )
            return

        ir.async_delete_issue(self.hass, domain=DOMAIN, issue_id=issue_id)

    async def async_import_history(self, source_entity: str) -> ServiceResponse:
        """Perform legacy import from a source entity into this lean utility meter."""
        if not source_entity:
            return {"status": "error", "message": "source_entity is required"}
            
        statistic_id = self.entity_id
        source_id = "recorder"
        
        now = dt_util.utcnow()
        current_period_start = get_period_start(now, self._cycle or "monthly")
            
        try:
            # Check if we have statistics prior to the current period
            last_stats = await get_recorder_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 5, statistic_id, False, {"sum"}
            )
            
            if statistic_id in last_stats and last_stats[statistic_id]:
                for stat in last_stats[statistic_id]:
                    s_start = stat.get("start") if isinstance(stat, dict) else getattr(stat, "start", None)
                    if isinstance(s_start, (int, float)):
                        s_start = datetime.fromtimestamp(s_start, tz=timezone.utc)
                    elif isinstance(s_start, str):
                        s_start = dt_util.parse_datetime(s_start)
                    
                    if s_start and s_start.tzinfo is None:
                        s_start = s_start.replace(tzinfo=timezone.utc)
                        
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
            legacy_stats = await get_recorder_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
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
            r_start = r.get("start") if isinstance(r, dict) else getattr(r, "start", None)
            r_state = r.get("state") if isinstance(r, dict) else getattr(r, "state", None)
            r_sum = r.get("sum") if isinstance(r, dict) else getattr(r, "sum", None)
            
            # Validate all required fields are present
            if r_start is None or r_state is None or r_sum is None:
                rejected_rows += 1
                if r_start is None:
                    _LOGGER.warning("Rejected row from %s: missing start timestamp (state=%s)", source_entity, r_state)
                continue
            
            if isinstance(r_start, (int, float)):
                r_start = datetime.fromtimestamp(r_start, tz=timezone.utc)
            elif isinstance(r_start, str):
                r_start = dt_util.parse_datetime(r_start)
            
            if r_start and r_start.tzinfo is None:
                r_start = r_start.replace(tzinfo=timezone.utc)
            
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

        groups = {}
        for r in valid_rows:
            key = get_period_key(r["start"], self._cycle or "monthly")
            if key not in groups:
                groups[key] = []
            groups[key].append(r)

        consolidated_rows = []
        for key, group_rows in groups.items():
            max_row = max(group_rows, key=lambda x: x["start"])
            consolidated_rows.append(max_row)

        consolidated_rows.sort(key=lambda x: x["start"])

        statistics_data = []
        for r in consolidated_rows:
            statistics_data.append(StatisticData(
                start=r["start"],
                state=r["state"],
                sum=r["sum"]
            ))

        unit = self.unit_of_measurement
        device_class = self.device_class
        if unit is None or device_class is None:
            source_state = self.hass.states.get(self._source_entity)
            if source_state:
                unit = unit or source_state.attributes.get("unit_of_measurement")
                device_class = device_class or source_state.attributes.get("device_class")

        # Leave unit_class empty to allow HA to handle it or user to customize via YAML
        unit_class = None

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            unit_class=unit_class,
            name=self.name or self.entity_id,
            source=source_id,
            statistic_id=statistic_id,
            unit_of_measurement=unit,
        )

        try:
            async_import_statistics(self.hass, metadata, statistics_data)
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

    async def async_thin_history(self, **kwargs: Any) -> ServiceResponse:
        """Perform retroactive history thinning to consolidate duplicate points."""
        statistic_id = self.entity_id
        cycle = self._cycle or "monthly"
        _LOGGER.info("Thinning history for %s with cycle %s", statistic_id, cycle)
        result = await self._async_thin_statistic_id(statistic_id, cycle)
        return result

    async def _async_thin_statistic_id(self, statistic_id: str, cycle: str) -> dict[str, Any]:
        """Perform thinning on this meter's statistic ID."""
        _LOGGER.info("Thinning statistic ID: %s with cycle %s", statistic_id, cycle)

        start_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
        try:
            stats = await get_recorder_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
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
            r_start = r.get("start") if isinstance(r, dict) else getattr(r, "start", None)
            r_state = r.get("state") if isinstance(r, dict) else getattr(r, "state", None)
            r_sum = r.get("sum") if isinstance(r, dict) else getattr(r, "sum", None)

            if r_start is not None and r_state is not None and r_sum is not None:
                if isinstance(r_start, (int, float)):
                    r_start = datetime.fromtimestamp(r_start, tz=timezone.utc)
                elif isinstance(r_start, str):
                    r_start = dt_util.parse_datetime(r_start)
                
                if r_start and r_start.tzinfo is None:
                    r_start = r_start.replace(tzinfo=timezone.utc)
                
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

        # Group by period
        groups = {}
        for r in valid_rows:
            key = get_period_key(r["start"], cycle)
            if key not in groups:
                groups[key] = []
            groups[key].append(r)

        consolidated_rows = []
        for key, group_rows in groups.items():
            max_row = max(group_rows, key=lambda x: x["start"])
            consolidated_rows.append(max_row)

        consolidated_rows.sort(key=lambda x: x["start"])

        def _delete_db_rows() -> dict[str, Any]:
            with session_scope(hass=self.hass) as session:
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

        delete_result = await get_recorder_instance(self.hass).async_add_executor_job(_delete_db_rows)

        if delete_result.get("status") != "prepared":
            return delete_result

        rebuild_required = delete_result.get("rebuild_required", False)

        state = self.hass.states.get(statistic_id)
        unit = None
        name = statistic_id
        if state is not None:
            unit = state.attributes.get("unit_of_measurement")
            name = state.attributes.get("friendly_name") or statistic_id

        if rebuild_required:
            metadata = StatisticMetaData(
                has_mean=False,
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
            async_import_statistics(self.hass, metadata, statistics_data)

            # async_import_statistics is asynchronous: give recorder time to persist
            # before reporting final counts.
            await asyncio.sleep(1)

        def _count_long_term_after_rebuild() -> int:
            with session_scope(hass=self.hass) as session:
                meta = session.query(StatisticsMeta).filter_by(statistic_id=statistic_id).first()
                if not meta:
                    return 0
                return session.query(Statistics).filter(Statistics.metadata_id == meta.id).count()

        long_term_after_db = await get_recorder_instance(self.hass).async_add_executor_job(_count_long_term_after_rebuild)
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

    async def async_clear_history(self, **kwargs: Any) -> ServiceResponse:
        """Permanently delete all historical statistics for this entity."""
        confirm = kwargs.get("confirm_deletion")
        if confirm != "DELETE":
            _LOGGER.error("History clear for %s aborted: confirmation string mismatch (received '%s', expected 'DELETE')", self.entity_id, confirm)
            return {"status": "error", "message": "Confirmation string mismatch"}

        statistic_id = self.entity_id
        _LOGGER.warning("Clearing all statistics for %s", statistic_id)

        def _delete_db_rows() -> dict[str, Any]:
            with session_scope(hass=self.hass) as session:
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

        return await get_recorder_instance(self.hass).async_add_executor_job(_delete_db_rows)




def get_period_key(dt: datetime, cycle: str) -> Any:
    """Get grouping key for a given timezone-aware datetime and cycle type."""
    local_dt = dt_util.as_local(dt)
    if cycle == "daily":
        return (local_dt.year, local_dt.month, local_dt.day)
    elif cycle == "weekly":
        isocal = local_dt.isocalendar()
        return (isocal[0], isocal[1])
    elif cycle == "monthly":
        return (local_dt.year, local_dt.month)
    elif cycle == "bimonthly":
        return (local_dt.year, (local_dt.month - 1) // 2)
    elif cycle == "quarterly":
        return (local_dt.year, (local_dt.month - 1) // 3)
    elif cycle == "yearly":
        return local_dt.year
    else:
        return (local_dt.year, local_dt.month, local_dt.day)


def get_period_start(dt: datetime, cycle: str) -> datetime:
    """Get the start datetime of the period for the given cycle."""
    local_dt = dt_util.as_local(dt)
    if cycle == "hourly":
        start = local_dt.replace(minute=0, second=0, microsecond=0)
    elif cycle == "daily":
        start = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "weekly":
        start = (local_dt - timedelta(days=local_dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "monthly":
        start = local_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "bimonthly":
        month = ((local_dt.month - 1) // 2) * 2 + 1
        start = local_dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "quarterly":
        month = ((local_dt.month - 1) // 3) * 3 + 1
        start = local_dt.replace(month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "yearly":
        start = local_dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        # Default to daily if unknown
        start = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt_util.as_utc(start)


def get_next_period_start(period_start: datetime, cycle: str) -> datetime:
    """Get the next period boundary for a given cycle."""
    local_dt = dt_util.as_local(period_start)

    if cycle == "hourly":
        next_start = (local_dt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    elif cycle == "daily":
        next_start = (local_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "weekly":
        next_start = (local_dt + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "monthly":
        year = local_dt.year + (1 if local_dt.month == 12 else 0)
        month = 1 if local_dt.month == 12 else local_dt.month + 1
        next_start = local_dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "bimonthly":
        step = 2
        idx = (local_dt.month - 1) + step
        year = local_dt.year + (idx // 12)
        month = (idx % 12) + 1
        next_start = local_dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "quarterly":
        step = 3
        idx = (local_dt.month - 1) + step
        year = local_dt.year + (idx // 12)
        month = (idx % 12) + 1
        next_start = local_dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif cycle == "yearly":
        next_start = local_dt.replace(year=local_dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_start = (local_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    return dt_util.as_utc(next_start)


def count_expected_points_from_first_start(first_start: datetime, now: datetime, cycle: str) -> int:
    """Count expected long-term points from first recorded point to now, inclusive."""
    first_cycle_start = get_period_start(first_start, cycle)
    current_cycle_start = get_period_start(now, cycle)

    if first_cycle_start > current_cycle_start:
        return 1

    points = 1
    cursor = first_cycle_start
    for _ in range(0, 100000):
        if cursor >= current_cycle_start:
            break
        cursor = get_next_period_start(cursor, cycle)
        points += 1
        if cursor > current_cycle_start:
            break

    return points
