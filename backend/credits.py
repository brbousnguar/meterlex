"""Monthly credits a provider meters for itself: ElevenLabs gives characters
used of the month's limit and the time the count resets, through its own API.

The collector reads that and the daily usage stats; the hub only lays them
against the month. The month runs from the reset before to the reset ahead
(the same day and time a calendar month apart), the pace is an even month,
and the projection carries the month's own rate to the reset.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import CreditDay, CreditReading

PROVIDERS = {"elevenlabs": "ElevenLabs"}
DAY = timedelta(days=1)
PACE_BAND = 2.0                     # points either side of the pace that read as "on pace"
STALE_AFTER = timedelta(hours=1)    # the collector reads every 15 minutes
MIN_FOR_PROJECTION = DAY            # a rate from less than a day of the month is noise
RECENT_DAYS = 7


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def month_before(dt: datetime) -> datetime:
    """The same day and time a month earlier, on the month's last day when it is shorter."""
    y, m = (dt.year, dt.month - 1) if dt.month > 1 else (dt.year - 1, 12)
    for day in (dt.day, 30, 29, 28):
        try:
            return dt.replace(year=y, month=m, day=day)
        except ValueError:
            continue
    raise ValueError(dt)


def month_after(dt: datetime) -> datetime:
    y, m = (dt.year, dt.month + 1) if dt.month < 12 else (dt.year + 1, 1)
    for day in (dt.day, 30, 29, 28):
        try:
            return dt.replace(year=y, month=m, day=day)
        except ValueError:
            continue
    raise ValueError(dt)


def plan_label(plan: Optional[str]) -> Optional[str]:
    """creator → Creator, pro → Pro."""
    return plan.replace("_", " ").capitalize() if plan else None


def _latest(session: Session, provider: str) -> Optional[CreditReading]:
    return session.exec(
        select(CreditReading).where(CreditReading.provider == provider).order_by(CreditReading.ts.desc())
    ).first()


def _days(session: Session, provider: str, first: date, last: date) -> dict:
    rows = session.exec(select(CreditDay).where(
        CreditDay.provider == provider, CreditDay.day >= first.isoformat(), CreditDay.day <= last.isoformat(),
    )).all()
    return {r.day: r.used for r in rows}


def view(session: Session, now: datetime, tz: ZoneInfo, provider: str = "elevenlabs") -> dict:
    out = {"now": _iso(now), "tz": str(tz), "provider": provider, "label": PROVIDERS[provider],
           "reading": None}
    r = _latest(session, provider)
    if r is None:
        return out
    reset = r.resets_at
    passed = reset <= now                       # the count reset since the last reading
    while reset <= now:
        reset = month_after(reset)
    start = month_before(reset)
    span = (reset - start).total_seconds()
    elapsed_pct = round(100 * min(1.0, (now - start).total_seconds() / span), 1)
    days_left = round((reset - now).total_seconds() / 86400, 2)

    used = None if passed else r.used
    limit = r.limit
    used_pct = round(100 * used / limit, 1) if used is not None and limit else None
    left = max(0, limit - used) if used is not None else None
    out.update({
        "plan": r.plan, "plan_label": plan_label(r.plan),
        "reading": {"at": _iso(r.ts), "machine": r.machine, "stale": now - r.ts > STALE_AFTER},
        "window": {"start": _iso(start), "reset": _iso(reset), "elapsed_pct": elapsed_pct,
                   "days_left": days_left, "reset_passed": passed},
        "used": used, "limit": limit, "left": left, "used_pct": used_pct,
    })

    # the pace is an even month; the rate is the month's own, from the reset to the reading
    pace = None
    since = (r.ts - start).total_seconds()
    if used is not None and limit:
        pace = {"delta_pts": round(used_pct - elapsed_pct, 1), "projected_used": None,
                "projected_pct": None, "runs_out_at": None, "per_day": None}
        pace["status"] = ("ahead" if pace["delta_pts"] > PACE_BAND
                          else "behind" if pace["delta_pts"] < -PACE_BAND else "on")
        if since >= MIN_FOR_PROJECTION.total_seconds():
            rate = used / since                 # credits a second
            pace["per_day"] = round(rate * 86400)
            projected = used + rate * (reset - r.ts).total_seconds()
            pace["projected_used"] = round(projected)
            pace["projected_pct"] = round(100 * projected / limit, 1)
            if used >= limit:
                pace["runs_out_at"] = _iso(r.ts)
            elif rate > 0 and projected > limit:
                pace["runs_out_at"] = _iso(r.ts + timedelta(seconds=(limit - used) / rate))
    out["pace"] = pace
    out["per_day_to_reset"] = round(left / days_left) if left is not None and days_left >= 1 else None

    # credits by UTC day, the way the provider counts them, from the reset's day to the reset ahead
    first, last = start.date(), (reset - timedelta(seconds=1)).date()
    by_day = _days(session, provider, first - timedelta(days=RECENT_DAYS), last)
    today = now.date()
    days, d = [], first
    while d <= last:
        days.append({"day": d.isoformat(), "used": by_day.get(d.isoformat(), 0),
                     "current": d == today, "future": d > today})
        d += DAY
    out["days"] = days
    done = [today - timedelta(days=i) for i in range(1, RECENT_DAYS + 1)]
    out["recent_per_day"] = round(sum(by_day.get(x.isoformat(), 0) for x in done) / RECENT_DAYS)
    return out
