"""The Claude Max quota: the weekly window Anthropic meters, read against the
week's elapsed time and against the tokens Claude Code used in it.

Anthropic gives the quota as a percentage and a reset time (collectors record
what Claude Code hands its status line), never in tokens. The tokens come from
the stored Claude Code turns, and the two together give the week's budget in
tokens: tokens used up to a reading ÷ that reading's percentage. It is an
estimate — Opus and Sonnet weigh differently and cache reads count for little,
and usage on claude.ai or the desktop app is in the percentage but not in the
turns — so the percentage stays the figure to trust.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import QuotaReading, UsageTurn

WEEK = timedelta(days=7)
DAY = timedelta(days=1)
SOURCE = "claude-code"          # the turns that count against Claude Max
PAST_WINDOWS = 8
MIN_PCT_FOR_BUDGET = 3.0        # below this a budget is mostly rounding noise
SAME_RESET = timedelta(hours=1) # resets_at can move by seconds between readings
PACE_BAND = 2.0                 # points either side of the pace that read as "on pace"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(tz)


def _readings(session: Session, window: str, since: datetime) -> list:
    return list(session.exec(
        select(QuotaReading).where(QuotaReading.window == window, QuotaReading.ts >= since)
        .order_by(QuotaReading.ts)
    ).all())


def _latest(session: Session, window: str) -> Optional[QuotaReading]:
    return session.exec(
        select(QuotaReading).where(QuotaReading.window == window).order_by(QuotaReading.ts.desc())
    ).first()


def current_window(latest: Optional[QuotaReading], now: datetime) -> tuple[datetime, datetime, bool]:
    """(start, reset, anchored). Windows are a fixed week ending at the reset
    Anthropic gives; once that reset passes, the next window is a week later.
    Without any reading yet, the last seven days stand in (anchored = False)."""
    if latest is None:
        return now - WEEK, now, False
    reset = latest.resets_at
    while reset <= now:
        reset += WEEK
    return reset - WEEK, reset, True


def _in_window(r: QuotaReading, reset: datetime) -> bool:
    return abs(r.resets_at - reset) <= SAME_RESET


def _running_max(rs: list) -> list:
    """The readings that raised the figure, in time order."""
    out, top = [], -1.0
    for r in sorted(rs, key=lambda r: r.ts):
        if r.used_pct > top:
            out.append(r)
            top = r.used_pct
    return out


def view(session: Session, now: datetime, tz: ZoneInfo) -> dict:
    latest_week = _latest(session, "seven_day")
    start, reset, anchored = current_window(latest_week, now)
    oldest = start - PAST_WINDOWS * WEEK

    turns = session.exec(
        select(UsageTurn.ts, UsageTurn.total_tokens, UsageTurn.model_id, UsageTurn.project, UsageTurn.machine)
        .where(UsageTurn.source == SOURCE, UsageTurn.ts >= min(oldest, now - 28 * DAY), UsageTurn.ts < now)
    ).all()
    week_readings = _readings(session, "seven_day", oldest - WEEK)

    def tokens_between(a: datetime, b: datetime) -> int:
        return sum(t[1] for t in turns if a <= t[0] < b)

    # ── this window ─────────────────────────────────────────────────────────
    # Within a window the percentage only grows, but every open Claude Code
    # session reports the last figure it was given, so a stale one can arrive
    # late: the reading is the highest so far, and the line its running max.
    current = _running_max([r for r in week_readings if _in_window(r, reset)])
    reading = max(current, key=lambda r: (r.used_pct, r.ts)) if current else None
    used_pct = reading.used_pct if reading else (0.0 if anchored else None)
    elapsed = min(1.0, max(0.0, (now - start) / WEEK))
    used_tokens = tokens_between(start, now)

    budget, budget_from = None, None
    for r in reversed(current):
        if r.used_pct >= MIN_PCT_FOR_BUDGET:
            budget, budget_from = tokens_between(start, r.ts) / (r.used_pct / 100), "this week"
            break

    # ── past windows, on the same anchor ────────────────────────────────────
    past = []
    for k in range(1, PAST_WINDOWS + 1):
        p_reset = reset - (k - 1) * WEEK - WEEK
        p_start = p_reset - WEEK
        rs = [r for r in week_readings if _in_window(r, p_reset)]
        final = max((r.used_pct for r in rs), default=None)
        tok = tokens_between(p_start, p_reset)
        p_budget = None
        top = max(rs, key=lambda r: r.used_pct, default=None)
        if top is not None and top.used_pct >= MIN_PCT_FOR_BUDGET:
            p_budget = tokens_between(p_start, top.ts) / (top.used_pct / 100)
        past.append({
            "start": _iso(p_start), "reset": _iso(p_reset),
            "start_local": _local(p_start, tz).isoformat(),
            "tokens": tok, "final_pct": final, "budget": round(p_budget) if p_budget else None,
        })
    if budget is None:
        known = [p["budget"] for p in past if p["budget"]][:4]
        if known:
            budget, budget_from = sum(known) / len(known), f"the last {len(known)} week{'s' if len(known) > 1 else ''}"

    full_past = [p for p in past if p["tokens"] > 0][:4]
    avg_day = sum(p["tokens"] for p in full_past) / (7 * len(full_past)) if full_past else None
    finals = [p["final_pct"] for p in past[:4] if p["final_pct"] is not None]

    # ── pace ────────────────────────────────────────────────────────────────
    pace = None
    if used_pct is not None and anchored:
        delta = used_pct - elapsed * 100
        projected = used_pct / elapsed if elapsed >= 0.04 else None
        pace = {
            "delta_pts": round(delta, 1),
            "status": "ahead" if delta > PACE_BAND else "behind" if delta < -PACE_BAND else "on",
            "projected_pct": round(projected, 1) if projected is not None else None,
        }

    left = None
    days_left = (reset - now) / DAY if anchored else None
    if budget:
        remaining = max(0.0, budget - used_tokens)
        left = {
            "tokens": round(remaining),
            "per_day": round(remaining / days_left) if days_left and days_left > 0.04 else None,
        }

    # ── the window's days: 24h steps from its start ─────────────────────────
    day_pts = []
    for i in range(7):
        a, b = start + i * DAY, start + (i + 1) * DAY
        end_reading = None
        for r in current:
            if r.ts < b:
                end_reading = r.used_pct
        day_pts.append({
            "start": _iso(a), "start_local": _local(a, tz).isoformat(),
            "tokens": tokens_between(a, min(b, now)) if a < now else 0,
            "future": a >= now, "current": a <= now < b,
            "end_pct": end_reading if a < now else None,
            "pace_pct": round((i + 1) / 7 * 100, 1),
        })

    # ── when: weekday × hour over the last four weeks, local time ───────────
    grid = [[0] * 24 for _ in range(7)]
    since = now - 28 * DAY
    by_project, by_machine, by_model = defaultdict(int), defaultdict(int), defaultdict(int)
    for ts, tok, model, project, machine in turns:
        if ts >= since:
            loc = _local(ts, tz)
            grid[loc.weekday()][loc.hour] += tok
        if start <= ts < now:
            by_project[project or "(no folder)"] += tok
            by_machine[machine] += tok
            by_model[model] += tok

    def ranked(d: dict, n: int) -> list:
        return [{"key": k, "tokens": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]

    five = _latest(session, "five_hour")
    five_hour = None
    if five is not None and five.resets_at > now:
        same = _running_max([r for r in _readings(session, "five_hour", now - timedelta(hours=6))
                             if abs(r.resets_at - five.resets_at) <= SAME_RESET])
        top = same[-1] if same else five
        five_hour = {"used_pct": top.used_pct, "reset": _iso(top.resets_at), "at": _iso(top.ts)}

    return {
        "now": _iso(now),
        "tz": str(tz),
        "anchored": anchored,
        "window": {
            "start": _iso(start), "reset": _iso(reset),
            "start_local": _local(start, tz).isoformat(), "reset_local": _local(reset, tz).isoformat(),
            "elapsed_pct": round(elapsed * 100, 1),
            "days_left": round(days_left, 2) if days_left is not None else None,
        },
        "reading": {"used_pct": reading.used_pct, "at": _iso(reading.ts), "machine": reading.machine} if reading else None,
        "used_pct": used_pct,
        "pace": pace,
        "tokens": {
            "used": used_tokens,
            "per_day_so_far": round(used_tokens / max(elapsed * 7, 1 / 24)) if anchored else round(used_tokens / 7),
            "avg_day_past": round(avg_day) if avg_day is not None else None,
            "budget": round(budget) if budget else None,
            "budget_from": budget_from,
            "left": left,
        },
        "five_hour": five_hour,
        "history": [{"at": _iso(r.ts), "pct": r.used_pct} for r in current],
        "days": day_pts,
        "past": past,
        "avg_final_pct": round(sum(finals) / len(finals), 1) if finals else None,
        "hours": grid,
        "by_project": ranked(by_project, 8),
        "by_machine": ranked(by_machine, 6),
        "by_model": ranked(by_model, 6),
    }
