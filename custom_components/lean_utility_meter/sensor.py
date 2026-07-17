"""Platform for Lean Utility Meter sensors.

STRUCTURE:
-----------

This module contains only the platform setup (entity creation from YAML config
and entity-service registration). Everything else lives in dedicated modules:

- entity.py                     LeanUtilityMeterSensor class (core measurement logic)
- period.py                     Period/cycle calculation utilities (shared)
- util.py                       Shared helpers for recorder statistics rows
- stats_writer.py               Core loop: capture cycle value and write 1 statistics row per cycle
- repairs/recorder_exclusion.py Repair: entity not excluded from recorder
- repairs/points_overage.py     Repair: more long-term points than expected for the cycle
- services/calibrate.py         Service: set manual calibration value
- services/import_history.py    Service: import consolidated history (legacy migration)
- services/thin_history.py      Service: consolidate duplicate points (retroactive cleanup)
- services/clear_history.py     Service: permanently delete all statistics
"""

from datetime import timedelta

import voluptuous as vol

from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED, ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .entity import LeanUtilityMeterSensor


def _meter_from_spec(hass: HomeAssistant, spec: dict) -> LeanUtilityMeterSensor:
    """Build a meter from a discovery spec sent by another integration.

    The spec mirrors the YAML options (source, cycle, net_consumption, ...) plus
    the creator-only keys: `entity_id` (pin the entity id) and the presentation
    overrides `unit_of_measurement` / `device_class` / `state_class` (forced when
    the key is present — an explicit None means "no value", an absent key means
    "inherit from the source entity", as usual).
    """
    return LeanUtilityMeterSensor(
        hass=hass,
        source_entity=spec["source"],
        name=spec.get("name", spec["unique_id"]),
        unique_id=spec["unique_id"],
        meter_type=spec.get("cycle"),
        meter_offset=spec.get("offset", timedelta(0)),
        cron_pattern=spec.get("cron"),
        delta_values=spec.get("delta_values", False),
        net_consumption=spec.get("net_consumption", False),
        sensor_always_available=spec.get("always_available", True),
        periodically_resetting=spec.get("periodically_resetting", True),
        absolute_values=spec.get("absolute_values", False),
        tariff=None,
        tariff_entity=None,
        parent_meter=spec.get("parent_meter", spec["unique_id"]),
        live_update_interval=spec.get("live_update_interval", timedelta(minutes=5)),
        entity_id=spec.get("entity_id"),
        force_unit_of_measurement=spec.get("unit_of_measurement", UNDEFINED),
        force_device_class=spec.get("device_class", UNDEFINED),
        force_state_class=spec.get("state_class", UNDEFINED),
    )


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Lean Utility Meter sensors — from YAML, or from another integration.

    Other integrations create Lean meters natively by dispatching
    ``async_load_platform(hass, "sensor", "lean_utility_meter", {"meters": [spec, ...]}, hass_config)``.
    Those meters belong to this platform, so the entity services (thin_history,
    calibrate, ...) target them exactly like YAML-defined ones.
    """
    if discovery_info and "meters" in discovery_info:
        async_add_entities(
            [_meter_from_spec(hass, spec) for spec in discovery_info["meters"]], True
        )
        _register_entity_services()
        return

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
    _register_entity_services()


def _register_entity_services() -> None:
    """Register the maintenance entity services (idempotent across platform setups)."""
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
