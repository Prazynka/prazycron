from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .analyzer import MONTHS, WEEKDAYS, validate_schedule

ALIASES = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def system_timezone_name() -> str:
    try:
        value = open("/etc/timezone", encoding="utf-8").read().strip()
        if value:
            return value
    except OSError:
        pass
    try:
        tzinfo = datetime.now().astimezone().tzinfo
        key = getattr(tzinfo, "key", None)
        return str(key or tzinfo or "UTC")
    except Exception:
        return "UTC"


def get_zone(name: str | None = None) -> ZoneInfo:
    try:
        return ZoneInfo(name or system_timezone_name())
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _named(value: str, names: dict[str, int]) -> str:
    upper = value.upper()
    for name, number in names.items():
        upper = re.sub(rf"\b{name}\b", str(number), upper)
    return upper


def expand_field(value: str, low: int, high: int, names: dict[str, int] | None = None, sunday: bool = False) -> set[int]:
    value = _named(value, names or {})
    result: set[int] = set()
    for part in value.split(","):
        step = 1
        if "/" in part:
            base, raw_step = part.split("/", 1)
            step = int(raw_step)
        else:
            base = part
        if base == "*":
            start, end = low, high
        elif "-" in base:
            left, right = base.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(base)
        result.update(range(start, end + 1, step))
    if sunday and 7 in result:
        result.remove(7); result.add(0)
    return result


def _matches(dt: datetime, fields: list[str]) -> bool:
    minute, hour, dom, month, dow = fields
    minute_set = expand_field(minute, 0, 59)
    hour_set = expand_field(hour, 0, 23)
    dom_set = expand_field(dom, 1, 31)
    month_set = expand_field(month, 1, 12, MONTHS)
    dow_set = expand_field(dow, 0, 7, WEEKDAYS, sunday=True)
    cron_dow = (dt.weekday() + 1) % 7
    dom_match = dt.day in dom_set
    dow_match = cron_dow in dow_set
    # Vixie cron: when both DOM and DOW are restricted, either may match.
    day_match = (dom_match or dow_match) if dom != "*" and dow != "*" else dom_match and dow_match
    return dt.minute in minute_set and dt.hour in hour_set and dt.month in month_set and day_match


def next_runs(schedule: str, count: int = 5, start: datetime | None = None, timezone_name: str | None = None, max_days: int = 730) -> list[datetime]:
    schedule = schedule.strip()
    if schedule == "@reboot":
        return []
    schedule = ALIASES.get(schedule, schedule)
    errors = validate_schedule(schedule)
    if errors:
        raise ValueError("; ".join(errors))
    zone = get_zone(timezone_name)
    current = (start or datetime.now(zone)).astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    minute, hour, dom, month, dow = schedule.split()
    minutes = sorted(expand_field(minute, 0, 59))
    hours = sorted(expand_field(hour, 0, 23))
    dom_set = expand_field(dom, 1, 31)
    month_set = expand_field(month, 1, 12, MONTHS)
    dow_set = expand_field(dow, 0, 7, WEEKDAYS, sunday=True)
    result: list[datetime] = []
    for day_offset in range(max_days + 1):
        date_value = (current + timedelta(days=day_offset)).date()
        if date_value.month not in month_set:
            continue
        cron_dow = (date_value.weekday() + 1) % 7
        dom_match = date_value.day in dom_set
        dow_match = cron_dow in dow_set
        day_match = (dom_match or dow_match) if dom != "*" and dow != "*" else dom_match and dow_match
        if not day_match:
            continue
        for hour_value in hours:
            for minute_value in minutes:
                candidate = datetime(date_value.year, date_value.month, date_value.day, hour_value, minute_value, tzinfo=zone)
                if candidate < current:
                    continue
                # Round-trip through UTC to skip local wall times that do not exist at DST spring-forward.
                roundtrip = candidate.astimezone(ZoneInfo("UTC")).astimezone(zone)
                if (roundtrip.year, roundtrip.month, roundtrip.day, roundtrip.hour, roundtrip.minute) != (candidate.year, candidate.month, candidate.day, candidate.hour, candidate.minute):
                    continue
                result.append(candidate)
                if len(result) >= count:
                    return result
    return result


def dst_note(schedule: str, timezone_name: str | None = None) -> str | None:
    if schedule == "@reboot":
        return None
    zone = get_zone(timezone_name)
    now = datetime.now(zone)
    offsets = {(now + timedelta(days=day)).utcoffset() for day in range(0, 370, 7)}
    if len(offsets) <= 1:
        return None
    raw = ALIASES.get(schedule, schedule)
    try:
        hours = expand_field(raw.split()[1], 0, 23)
    except Exception:
        return "Strefa czasowa używa zmiany czasu; sprawdź zachowanie zadania w dniach zmiany DST."
    if any(hour in {0, 1, 2, 3} for hour in hours):
        return "Zadanie działa w godzinach nocnej zmiany czasu i może zostać pominięte albo wykonane dwukrotnie podczas przejścia DST."
    return "Strefa czasowa używa czasu letniego; terminy są obliczane według lokalnego zegara systemu."
