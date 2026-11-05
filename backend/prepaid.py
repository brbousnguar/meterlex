"""Prepaid accounts (#77): money put in ahead, spent call by call, no month.

OpenRouter says what was bought and used, all time, so the balance is theirs.
OpenAI says neither: its costs API gives the spend by UTC day (with an admin
key), and the balance is the one Brahim read on the billing page, saved here
with the day's spend at that moment, less what was spent since. With the day
of the last top-up beside it (#79), the meter measures what was spent since that
top-up out of what the account held after it. Credits bought later can be added
to it as they are (#91): they count from the moment they were added.

The costs API runs a few hours behind, so the day's spend kept with the balance
can miss what was spent before it was read (#89). The usage API has hours: the
hub asks the collector for the hours of the day the balance was read, and counts
that day's spend after the reading as the share of its usage that came after it.

Amounts are micro-dollars in the tables and dollars out. The rate is the last
seven days: OpenRouter's from its own readings, OpenAI's from its full days.
"""
import json
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
# the share of that day's usage after the reading (#89), and whether the whole day was in when it was taken
OPENAI_AFTER_SHARE, OPENAI_SHARE_FINAL = "openai_balance_after_share", "openai_balance_share_final"
OPENAI_ADDED = "openai_balance_added"      # credits added since the reading, as JSON [{usd, at}] (#91)
HOUR = timedelta(hours=1)
SETTLED_AFTER = timedelta(hours=2)    # the usage API's hours are in this long after the day ends


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
    """The OpenAI balance as last saved: {usd, at, day_spent, topped_up, after_share, share_final, added} or None."""
    usd, at = _setting(session, OPENAI_BALANCE), _setting(session, OPENAI_BALANCE_AT)
    if not usd or not at:
        return None
    try:
        topup, share = _setting(session, OPENAI_TOPUP_DAY), _setting(session, OPENAI_AFTER_SHARE)
        return {"usd": float(usd), "at": datetime.fromisoformat(at),
                "day_spent": int(_setting(session, OPENAI_BALANCE_DAY_SPENT) or 0),
                "topped_up": date.fromisoformat(topup) if topup else None,
                "after_share": float(share) if share else None,
                "share_final": _setting(session, OPENAI_SHARE_FINAL) == "1",
                "added": [{"usd": float(x["usd"]), "at": datetime.fromisoformat(x["at"])}
                          for x in json.loads(_setting(session, OPENAI_ADDED) or "[]")]}
    except (ValueError, KeyError, TypeError):
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
    _save(session, {**values, OPENAI_AFTER_SHARE: "", OPENAI_SHARE_FINAL: "", OPENAI_ADDED: ""})   # a new reading, a new start


def add_credits(session: Session, usd: float, now: datetime) -> bool:
    """Add credits bought now to the saved balance, today the top-up day (#91); False with no balance to add to."""
    a = anchor(session)
    if a is None:
        return False
    added = [{"usd": x["usd"], "at": x["at"].isoformat(timespec="seconds")} for x in a["added"]]
    added.append({"usd": round(usd, 2), "at": now.isoformat(timespec="seconds")})
    _save(session, {OPENAI_ADDED: json.dumps(added), OPENAI_TOPUP_DAY: now.date().isoformat()})
    return True


def _save(session: Session, values: dict) -> None:
    for key, value in values.items():
        row = session.get(Setting, key) or Setting(key=key)
        row.value = value
        session.add(row)
    session.commit()


def hours_wanted(session: Session) -> Optional[str]:
    """The UTC day whose usage hours the hub still needs, the day the balance was read, or None."""
    a = anchor(session)
    return None if a is None or a["share_final"] else a["at"].date().isoformat()


def take_hours(session: Session, hours: list, read: datetime) -> None:
    """Keep the share of the reading's day usage that came after the reading, from the
    usage API's hours [(start, weight)] read at `read`, naive UTC; the reading's own
    hour is split by its minutes. A day with no usage on record keeps the day_spent rule."""
    a = anchor(session)
    if a is None or a["share_final"]:
        return
    start = datetime.combine(a["at"].date(), datetime.min.time())
    day = [(h, w) for h, w in hours if start <= h < start + DAY]
    if not day:
        return
    total = sum(w for _, w in day)
    after = sum(w if h >= a["at"] else w * max(0.0, (h + HOUR - a["at"]) / HOUR) for h, w in day)
    _save(session, {OPENAI_AFTER_SHARE: f"{after / total:.6f}" if total else "",
                    OPENAI_SHARE_FINAL: "1" if read >= start + DAY + SETTLED_AFTER else ""})


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
                                                     "topped_up": a["topped_up"] and a["topped_up"].isoformat(),
                                                     "added": [{"usd": x["usd"], "at": _iso(x["at"])} for x in a["added"]]}
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
            day_total = days.get(at_day.isoformat(), 0)
            # the reading's day: its share of usage after the reading, once the hours are in (#89)
            on_day = (round(day_total * a["after_share"]) if a["after_share"] is not None
                      else max(0, day_total - a["day_spent"]))
            out["spent_since_balance"] = _usd(after + on_day)
            added = sum(round(x["usd"] * MICRO) for x in a["added"])
            left = round(a["usd"] * MICRO) + added - after - on_day
            # what the account held after the top-up: what is left plus all spent from that day on. The
            # top-up day counts whole, so a day that began on the old money reads a little high; on the
            # day the balance was typed, as after a top-up today, only what was spent after it counts.
            t = a["topped_up"]
            if t is not None and start <= t <= today:
                since = sum(n for d, n in days.items() if date.fromisoformat(d) >= t)
                if t == at_day:
                    since -= day_total - on_day
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
