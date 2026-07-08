"""Shared helpers for working with recorder statistics rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .period import get_period_key

if TYPE_CHECKING:
    from .sensor import LeanUtilityMeterSensor


def stat_field(row: Any, name: str) -> Any:
    """Read a field from a statistics row, which may be a dict or an object."""
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def parse_stat_start(value: Any) -> datetime | None:
    """Normalize a statistics start value (epoch, ISO string or datetime) to aware UTC."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        value = dt_util.parse_datetime(value)
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def consolidate_rows_by_period(valid_rows: list[dict[str, Any]], cycle: str) -> list[dict[str, Any]]:
    """Keep only the most recent row per cycle period, sorted by start."""
    groups: dict[Any, list[dict[str, Any]]] = {}
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
    return consolidated_rows


def resolve_unit(meter: LeanUtilityMeterSensor) -> str | None:
    """Resolve the unit of measurement, falling back to the source entity."""
    unit = meter.unit_of_measurement
    if unit is None:
        source_state = meter.hass.states.get(meter._source_entity)
        if source_state:
            unit = source_state.attributes.get("unit_of_measurement")
    return unit
