# The UI surface

[← Back to README](../README.md)

Meters are declared in YAML, but they are **visible** in the UI: the integration appears under *Settings → Devices & Services*, with one device per metered source.

Nothing there is editable. The YAML stays the single source of truth; the integration entry exists only because Home Assistant refuses to register devices for integrations that have no config entry. Opening it offers no options, and the entry stores nothing.

## One device per source

Meters are grouped by their `source:`. A meter chain — the never-resetting lifetime plus one meter per cycle, all fed by the same transient — is one metered thing seen at several resolutions, so it belongs on one page:

```
Device: my_source_energy_transient
├─ my_source_energy_lifetime
├─ my_source_energy_hourly
├─ my_source_energy_daily
├─ my_source_energy_monthly
└─ my_source_energy_yearly
```

Nothing has to be declared for this: the YAML already says which source each meter reads. A meter with `tariffs:` puts all its per-tariff variants on the same device too.

The device takes the source entity's friendly name, falling back to its object id when the source has no name yet — it may not exist at the moment the meters are built.

**Entity names are untouched.** Whatever you set with `name:` (or the title derived from the meter slug) is what you keep: the device groups, it does not rename.

## Nothing was re-keyed

Adding a config entry does not disturb existing meters. The entity registry keys on *(domain, platform, unique id)*, and `platform` is `lean_utility_meter` whether the platform was set up from discovery or from an entry — this integration owns it either way. Entity ids, registry rows and long-term statistics all carry over untouched.

## Meters created by other integrations

An integration that builds Lean meters by dispatching specs to this platform gets meters without a device, because this platform is the discovery one and has no config entry behind it. That is a property of Home Assistant, not a choice here.

A creator that wants its meters on **its own** device builds them with the public `meter_from_spec` on its own platform, passing `device_info` in the spec, and calls `register_entity_services` so the maintenance services keep working. [Energy Profiler](https://github.com/nicola-spreafico/home-assistant-energy-profiler) does exactly that. Note that those services are then registered under the *creator's* domain — see [Services & Actions](services.md).

## What is still YAML-only

Adding, editing or removing meters means editing the YAML and restarting Home Assistant. There is no options flow.
