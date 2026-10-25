"""Prepaid accounts (#77): money put in ahead, spent call by call, no month.

OpenRouter says what was bought and used, all time, so the balance is theirs.
OpenAI says neither: its costs API gives the spend by UTC day (with an admin
key), and the balance is the one Brahim read on the billing page, saved here
with the day's spend at that moment, less what was spent since. With the day
of the last top-up beside it (#79), the meter measures what was spent since that
top-up out of what the account held after it.

Amounts are micro-dollars in the tables and dollars out. The rate is the last
seven days: OpenRouter's from its own readings, OpenAI's from its full days.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import BalanceReading, CreditDay, Setting

PROVIDERS = {"openrouter": "OpenRouter", "openai": "OpenAI"}
DAY = timedelta(days=1)
DAYS_SHOWN = 30
RATE_DAYS = 7
MIN_FOR_RATE = timedelta(hours=6)   # a rate from less than that is noise
STALE_AFTER = timedelta(hours=1)    # the collector reads every 15 minutes
MICRO = 1_000_000
# the balance read on the OpenAI billing page, the day's spend when it was saved, and the last top-up's day
OPENAI_BALANCE, OPENAI_BALANCE_AT, OPENAI_BALANCE_DAY_SPENT, OPENAI_TOPUP_DAY = (
    "openai_balance_usd", "openai_balance_at", "openai_balance_day_spent", "openai_topup_day")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _usd(micro: Optional[int]) -> Optional[float]:
    return None if micro is None else round(micro / MICRO, 4)


def _latest(session: Session, provider: str, *, with_used: bool = False) -> Optional[BalanceReading]:
    q = select(BalanceReading).where(BalanceReading.provider == provider)
    if with_used:
        q = q.where(BalanceReading.used.is_not(None))
    return session.exec(q.order_by(BalanceReading.ts.desc())).first()


def _first(session: Session, provider: str) -> Optional[BalanceReading]:
    return session.exec(select(BalanceReading).where(BalanceReading.provider == provider)
                        .order_by(BalanceReading.ts)).first()


def _days(session: Session, provider: str) -> dict:
    rows = session.exec(select(CreditDay).where(CreditDay.provider == provider)).all()
    return {r.day: r.used for r in rows}


def _setting(session: Session, key: str) -> str:
    row = session.get(Setting, key)
    return row.value if row else ""


def anchor(session: Session) -> Optional[dict]:
    """The OpenAI balance as last saved: {usd, at, day_spent, topped_up} or None."""
    usd, at = _setting(session, OPENAI_BALANCE), _setting(session, OPENAI_BALANCE_AT)
    if not usd or not at:
        return None
    try:
        topup = _setting(session, OPENAI_TOPUP_DAY)
        return {"usd": float(usd), "at": datetime.fromisoformat(at),
                "day_spent": int(_setting(session, OPENAI_BALANCE_DAY_SPENT) or 0),
                "topped_up": date.fromisoformat(topup) if topup else None}
    except ValueError:
        return None


def save_anchor(session: Session, usd: Optional[float], now: datetime, topped_up: Optional[date] = None) -> None:
    """Keep the balance read on the billing page now, and the day of the last
    top-up if known; None clears both. The day's spend so far is already out of
    that balance, so it is kept to take off."""
    spent = _days(session, "openai").get(now.date().isoformat(), 0)
    values = ({OPENAI_BALANCE: "", OPENAI_BALANCE_AT: "", OPENAI_BALANCE_DAY_SPENT: "", OPENAI_TOPUP_DAY: ""}
              if usd is None else
              {OPENAI_BALANCE: f"{usd:.2f}", OPENAI_BALANCE_AT: now.isoformat(timespec="seconds"),
               OPENAI_BALANCE_DAY_SPENT: str(spent), OPENAI_TOPUP_DAY: topped_up.isoformat() if topped_up else ""})
    for key, value in values.items():
        row = session.get(Setting, key) or Setting(key=key)
        row.value = value
        session.add(row)
    session.commit()


def _rate_openrouter(session: Session, latest: BalanceReading, now: datetime) -> Optional[int]:
    """Micro-dollars a day over the last seven days of readings."""
    since = now - RATE_DAYS * DAY
    base = session.exec(select(BalanceReading).where(
        BalanceReading.provider == "openrouter", BalanceReading.used.is_not(None), BalanceReading.ts >= since,
    ).order_by(BalanceReading.ts)).first()
    if base is None or latest.ts - base.ts < MIN_FOR_RATE:
        return None
    return round((latest.used - base.used) / ((latest.ts - base.ts) / DAY))


def view(session: Session, now: datetime, tz: ZoneInfo, provider: str) -> dict:
    out = {"kind": "prepaid", "now": _iso(now), "tz": str(tz), "provider": provider,
           "label": PROVIDERS[provider], "reading": None}
    latest = _latest(session, provider)
    days = _days(session, provider)
    if provider == "openai":
        a = anchor(session)
        out["balance_set"] = None if a is None else {"usd": a["usd"], "at": _iso(a["at"]),
                                                     "topped_up": a["topped_up"] and a["topped_up"].isoformat()}
        out["topped_up"] = None
    if latest is None:
        return out
    out["reading"] = {"at": _iso(latest.ts), "machine": latest.machine, "stale": now - latest.ts > STALE_AFTER}
    today = now.date()

    # where the record starts: OpenRouter from its first reading, OpenAI from its oldest day
    if provider == "openrouter":
        start = _first(session, provider).ts.date()
    else:
        start = min((date.fromisoformat(d) for d in days), default=today)

    rate = None
    if provider == "openrouter":
        latest = _latest(session, provider, with_used=True) or latest
        bought, used = latest.bought, latest.used
        left = None if bought is None or used is None else bought - used
        out.update({"bought": _usd(bought), "used": _usd(used)})
        if used is not None:
            rate = _rate_openrouter(session, latest, now)
    else:
        a = anchor(session)
        left = None
        if a is not None:
            at_day = a["at"].date()
            after = sum(n for d, n in days.items() if date.fromisoformat(d) > at_day)
            on_day = max(0, days.get(at_day.isoformat(), 0) - a["day_spent"])
            out["spent_since_balance"] = _usd(after + on_day)
            left = round(a["usd"] * MICRO) - after - on_day
            # what the account held after the top-up: what is left plus all spent from that day on. The
            # top-up day counts whole, so a day that began on the old money reads a little high; on the
            # day the balance was typed, as after a top-up today, only what was spent after it counts.
            t = a["topped_up"]
            if t is not None and start <= t <= today:
                since = sum(n for d, n in days.items() if date.fromisoformat(d) >= t)
                if t == at_day:
                    since -= min(a["day_spent"], days.get(t.isoformat(), 0))
                out["topped_up"] = {"day": t.isoformat(), "held": _usd(left + since), "spent": _usd(since)}
        full = [today - timedelta(days=i) for i in range(1, RATE_DAYS + 1)]
        if start <= full[-1]:
            rate = round(sum(days.get(d.isoformat(), 0) for d in full) / RATE_DAYS)

    out["left"] = _usd(left)
    out["per_day"] = _usd(rate)
    if left is not None and rate:
        lasts = max(0.0, left / rate)
        out["lasts_days"] = round(lasts, 1)
        out["runs_out_at"] = _iso(now + timedelta(days=lasts)) if lasts < 3650 else None
    else:
        out["lasts_days"] = out["runs_out_at"] = None

    month = today.replace(day=1)
    week = today - timedelta(days=RATE_DAYS - 1)
    known = lambda d: d >= start
    out["spent"] = {
        "today": _usd(days.get(today.isoformat(), 0)),
        "week": _usd(sum(n for d, n in days.items() if week <= date.fromisoformat(d) <= today)),
        "month": _usd(sum(n for d, n in days.items() if month <= date.fromisoformat(d) <= today)),
        "since": start.isoformat(),
        "week_complete": known(week), "month_complete": known(month),
    }
    out["days"] = [{"day": d.isoformat(), "spent": _usd(days.get(d.isoformat(), 0)),
                    "current": d == today, "known": known(d)}
                   for d in (today - timedelta(days=i) for i in range(DAYS_SHOWN - 1, -1, -1))]
    return out
