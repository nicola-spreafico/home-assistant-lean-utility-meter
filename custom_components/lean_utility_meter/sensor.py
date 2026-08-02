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

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED, ConfigType, DiscoveryInfoType

from .const import DOMAIN
from .entity import LeanUtilityMeterSensor


def meter_from_spec(hass: HomeAssistant, spec: dict) -> LeanUtilityMeterSensor:
    """Build a meter from a spec supplied by another integration.

    The spec mirrors the YAML options (source, cycle, net_consumption, ...) plus
    the creator-only keys: `entity_id` (pin the entity id), `device_info` (attach
    the meter to the creator's device) and the presentation overrides
    `unit_of_measurement` / `device_class` / `state_class` /
    `suggested_display_precision` (forced when the key is present — an explicit
    None means "no value", an absent key means "inherit from the source entity",
    as usual).

    Public on purpose: a creator that needs its meters on its **own** entity
    platform — the only way `device_info` is honored, since Home Assistant
    attaches devices only for platforms backed by a config entry — builds them
    with this and calls :func:`register_entity_services` so the maintenance
    services keep working. Dispatching specs by discovery instead keeps the
    meters on this platform, where those services are already registered.
    """
    meter = LeanUtilityMeterSensor(
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
        force_suggested_display_precision=spec.get(
            "suggested_display_precision", UNDEFINED
        ),
    )
    # Only meaningful when the meter is added by a config-entry-backed platform;
    # harmless (ignored by Home Assistant) when it is added by this one.
    if (device_info := spec.get("device_info")) is not None:
        meter._attr_device_info = device_info
    # With a device, `name` is the entity's own part and Home Assistant renders
    # "<device> <name>" — so the creator can pass the bare measurement instead of
    # repeating the device in every label.
    if spec.get("has_entity_name"):
        meter._attr_has_entity_name = True
    return meter


def _source_device_info(hass: HomeAssistant, source_entity: str) -> DeviceInfo:
    """The device that groups every meter reading the same source.

    A meter chain — the lifetime plus one meter per cycle, all fed by the same
    transient — is one metered thing seen at several resolutions, so it belongs
    on one page. Grouping by `source:` gives exactly that, and needs nothing
    declared: the YAML already says which source each meter reads.

    The name follows the source's own, falling back to its object id when the
    source has no friendly name yet (it may not exist at setup time).
    """
    state = hass.states.get(source_entity)
    name = None
    if state is not None:
        name = state.attributes.get("friendly_name")
    if not name:
        name = source_entity.split(".", 1)[-1].replace("_", " ")
    return DeviceInfo(
        identifiers={(DOMAIN, source_entity)},
        name=name,
        manufacturer="Lean Utility Meter",
        model="Metered source",
    )


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Lean meters dispatched by another integration.

    Other integrations create Lean meters natively by dispatching
    ``async_load_platform(hass, "sensor", "lean_utility_meter", {"meters": [spec, ...]}, hass_config)``.
    Those meters belong to this platform, so the entity services (thin_history,
    calibrate, ...) target them exactly like YAML-defined ones — but, this
    platform having no config entry, they cannot belong to a device. A creator
    that needs devices builds them on its own platform instead: see
    :func:`meter_from_spec`.

    YAML meters no longer come through here; they are set up from the config
    entry (:func:`async_setup_entry`) so they can be grouped into devices.
    """
    if not (discovery_info and "meters" in discovery_info):
        return
    async_add_entities(
        [meter_from_spec(hass, spec) for spec in discovery_info["meters"]], True
    )
    register_entity_services()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build the YAML-declared meters, grouped into one device per source."""
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

        first = len(entities)

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

        # Every meter of this source lands on the same device — including the
        # per-tariff variants of a single declaration.
        device = _source_device_info(hass, source)
        for entity in entities[first:]:
            entity._attr_device_info = device

    async_add_entities(entities, True)
    register_entity_services()


def register_entity_services() -> None:
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
