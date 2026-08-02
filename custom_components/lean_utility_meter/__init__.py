"""The Lean Utility Meter integration."""

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.const import Platform, CONF_SOURCE

from homeassistant.components.utility_meter.const import DATA_UTILITY

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Config validation schema extending the native utility_meter schema
try:
    from homeassistant.components.utility_meter import METER_CONFIG_SCHEMA
    if isinstance(METER_CONFIG_SCHEMA.schema, vol.All):
        base_dict = METER_CONFIG_SCHEMA.schema.validators[0]
    elif isinstance(METER_CONFIG_SCHEMA.schema, dict):
        base_dict = METER_CONFIG_SCHEMA.schema
    else:
        raise ValueError("Unknown schema type")

    lean_dict = dict(base_dict)
    lean_dict.update({
        vol.Optional("live_update_interval", default=timedelta(minutes=5)): cv.time_period,
        vol.Optional("absolute_values", default=False): cv.boolean,
    })
    LEAN_METER_SCHEMA = vol.Schema(lean_dict)
except Exception as err:
    _LOGGER.warning("Could not extend native utility_meter schema, using fallback: %s", err)
    # Fallback schema if utility_meter is not yet initialized or importable in this path
    LEAN_METER_SCHEMA = vol.Schema({
        vol.Required(CONF_SOURCE): cv.entity_id,
        vol.Optional("name"): cv.string,
        vol.Optional("unique_id"): cv.string,
        vol.Optional("cycle"): cv.string,
        vol.Optional("offset"): cv.time_period,
        vol.Optional("cron"): cv.string,
        vol.Optional("delta_values", default=False): cv.boolean,
        vol.Optional("always_available", default=False): cv.boolean,
        vol.Optional("periodically_resetting", default=False): cv.boolean,
        vol.Optional("tariffs"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("live_update_interval", default=timedelta(minutes=5)): cv.time_period,
        vol.Optional("absolute_values", default=False): cv.boolean,
    })

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({cv.slug: LEAN_METER_SCHEMA})}, extra=vol.ALLOW_EXTRA
)

PLATFORMS = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Read the YAML meters and make sure an entry exists to back them.

    The entry exists only so Home Assistant will let the integration register
    devices — it holds no configuration. Note that the entities are unaffected
    by the move to an entry: the entity registry keys on
    (domain, platform, unique_id), and `platform` is this integration either
    way, so existing meters keep their rows, entity ids and statistics.
    """
    hass.data.setdefault(DOMAIN, {})
    # Initialize DATA_UTILITY to avoid KeyErrors in core UtilityMeterSensor
    hass.data.setdefault(DATA_UTILITY, {})

    if DOMAIN not in config:
        return True

    # Save meter configurations
    hass.data[DOMAIN] = config[DOMAIN]

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data={}
        )
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Forward the YAML meters to the sensor platform, which builds them."""
    if not hass.data.get(DOMAIN):
        _LOGGER.error(
            "Lean Utility Meter has a config entry but no 'lean_utility_meter:' "
            "block in your YAML configuration; no meters will be created. Restore "
            "the block, or delete the integration entry to remove it for good"
        )
        return False

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the platforms; the YAML meter configs stay for the next setup."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

