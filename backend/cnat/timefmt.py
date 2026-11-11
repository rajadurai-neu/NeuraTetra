"""All user-facing times: 12-hour, Eastern time, e.g. 10/30/2026 2:05 PM ET (spec A8.38)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def fmt_et(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ET)
    return f"{local:%m/%d/%Y} {local.hour % 12 or 12}:{local:%M} {local:%p} ET"
