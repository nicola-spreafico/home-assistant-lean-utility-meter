"""Config flow for Lean Utility Meter — import only, never interactive.

Meters are declared in YAML and that stays the single source of truth. This
flow exists solely to obtain a config entry, which Home Assistant requires
before an integration may register devices: without one, every meter is
device-less and the integration page can only show the "not set up via the UI"
notice.

The entry carries no configuration of its own — `data` is deliberately empty.
"""

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class LeanUtilityMeterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance flow whose only real step is the YAML import."""

    VERSION = 1

    async def async_step_import(self, import_data: dict | None = None):
        """Create the one entry that backs the whole YAML configuration."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Lean Utility Meter", data={})

    async def async_step_user(self, user_input: dict | None = None):
        """Refuse interactive setup: there is nothing to configure here."""
        return self.async_abort(reason="yaml_only")
